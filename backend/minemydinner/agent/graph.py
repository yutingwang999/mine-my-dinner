import json
from collections.abc import Collection
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel
from sqlalchemy import select

from minemydinner.agent.planner import TOOLS_VERSION, PlanProposal, build_planner
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import (
    PROMPT_VERSION,
    REQUEST_STYLES,
    ParsedRequest,
    apply_defaults,
    get_request_style,
    load_prompt,
    needs_question,
    request_problem,
    set_request_style,
)
from minemydinner.agent.state import ChatState
from minemydinner.llm.client import map_strings
from minemydinner.models import Diner, Recipe
from minemydinner.planning.composition import composition_gap
from minemydinner.planning.context import MealConstraints, diner_codes, load_planning_context
from minemydinner.planning.feasibility import Shortfall, check_feasibility
from minemydinner.planning.inventory import meal_preferences
from minemydinner.planning.shortlist import ShortlistEntry, build_shortlist_result
from minemydinner.planning.stock import compute_missing, missing_cookware
from minemydinner.planning.tools import PlannerDeps
from minemydinner.planning.validate import PlanSpec, ValidationResult, validate_plan

# Retries after the first proposal fails validation (design 7.3: at most N times).
MAX_RETRIES = 2
# A plan with one of these violations is never shown (design 6.3).
NEVER_SHOWN = ("H2", "H5")

OTHER_REPLY_EN = (
    "I can help you plan dinners from your recipes and what you have at home. "
    "Tell me what you would like to cook, for how many people, and I will suggest a plan."
)
OTHER_REPLY_ZH = "我可以根据你的菜谱和家里现有的食材帮你安排晚餐。告诉我你想做什么、几个人吃，我来给你出方案。"
NO_PLAN_NOTE_EN = (
    "I could not find a plan that meets every hard rule, so none is shown. "
    "Below is what stopped it and what you can change."
)
NO_PLAN_NOTE_ZH = "没有找到满足所有硬性规则的方案，所以这次不展示方案。下面是原因和可以调整的地方。"
EARLY_STOP_NOTE_EN = (
    "This request cannot be met as it stands, so I have not planned anything. "
    "Below is why and what you can change."
)
EARLY_STOP_NOTE_ZH = "按现在的条件这个需求做不到，所以没有出方案。下面是原因和可以调整的地方。"
NO_PLAN_NOTE_BARE_EN = (
    "I could not find a plan that meets every hard rule, so none is shown. "
    "Below is what stopped it. Send the request again to try once more."
)
NO_PLAN_NOTE_BARE_ZH = "没有找到满足所有硬性规则的方案，所以这次不展示方案。下面是原因，可以再发一次请求重试。"
CLOSEST_NOTE_EN = "This is the closest plan I could find. Some rules are not met; see below."
UNCLEAR_REPLY_EN = (
    "I could not make sense of the number of dishes in that request, so I have not planned "
    "anything. Tell me again how many of each kind you would like."
)
UNCLEAR_REPLY_ZH = "我没能理解这次要几道菜，所以没有出方案。请再告诉我每一类各要几道。"
CLOSEST_NOTE_ZH = "这是我能找到的最接近的方案，有些规则没有满足，详见下方。"


class Intent(BaseModel):
    intent: Literal["plan", "other"]


@dataclass
class GraphDeps:
    session_factory: Any
    client: Any
    recorder: PlanningRecorder
    # The LLMClient task name for every model call in the graph.
    task: str = "planning"
    # False sends the planner the request summary alone, with no shortlist (evaluation).
    use_shortlist: bool = True
    # Query embeddings per planning run (by run_id), shared by the shortlist and every
    # attempt's searches. Kept here, not in the checkpointed state; dropped when the run ends.
    run_query_vectors: dict[int, dict[str, list[float]]] = field(default_factory=dict)


def load_route_prompt() -> str:
    return load_prompt("route")


def other_reply(locale: str) -> str:
    return OTHER_REPLY_ZH if locale == "zh" else OTHER_REPLY_EN


def _zh(locale: str) -> bool:
    return locale == "zh"


def _constraints(state: ChatState) -> MealConstraints:
    data = state["constraints"]
    return MealConstraints(
        diner_ids=tuple(data["diner_ids"]), unconfirmed_diners=data["unconfirmed_diners"]
    )


def _current_request(messages: list) -> list[str]:
    """The user's messages since the assistant's last reply."""
    texts: list[str] = []
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            break
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            texts.insert(0, message.content)
    return texts


# ---- plain words ----


CATEGORY_WORDS = {
    "dishes": ("dish", "dishes", "道菜"),
    "soups": ("soup", "soups", "道汤"),
    "meat_dishes": ("meat dish", "meat dishes", "道荤菜"),
    "vegetable_dishes": ("vegetable dish", "vegetable dishes", "道素菜"),
    "staples": ("staple food", "staple foods", "份主食"),
    "desserts": ("dessert", "desserts", "份甜品"),
    "snacks": ("snack", "snacks", "份小吃"),
}


def _counts_text(counts: dict, detail: dict, zh: bool) -> str:
    """The counts of the categories that matter, in plain words: those the plan asks for
    or contains, plus a named meat or vegetable count. A 0 nobody asked for is left out."""
    required, present = detail["required"], detail["present"]
    parts = []
    for category, (one, many, cn) in CATEGORY_WORDS.items():
        named_split = category in required and category.endswith("_dishes")
        if required.get(category, 0) or present.get(category, 0) or named_split:
            n = counts.get(category, 0)
            parts.append(f"{n} {cn}" if zh else f"{n} {one if n == 1 else many}")
    return ("，" if zh else ", ").join(parts)


def _minimum_note(detail: dict, zh: bool) -> str:
    if not detail["minimum"]:
        return ""
    if zh:
        return "（荤菜和素菜的数量是最少要求）"
    return " (the meat and vegetable counts are minimums)"


def _composition_gap_of(spec: PlanSpec, validation: dict) -> int:
    """How far an attempt is from the asked composition. The counts come from the H4
    finding; with no H4 finding every category already matches, so the gap is 0."""
    for finding in validation["violations"]:
        if finding["code"] == "H4":
            return composition_gap(spec, finding["detail"]["present"])
    return 0


def _violation_for_model(finding: dict, codes: dict[str, str]) -> str:
    """One violation, told to the planner so it can fix it."""
    code, recipe_id, detail = finding["code"], finding["recipe_id"], finding["detail"]
    if code == "H1":
        missing = ", ".join(detail["missing"])
        return f"Recipe {recipe_id}: not in stock: {missing}. Fridge-only mode needs them at home."
    if code == "H2" and "unknown" in detail:
        unknown = ", ".join(detail["unknown"])
        return (
            f"Recipe {recipe_id}: ingredients with unknown allergens ({unknown}) while a diner "
            "has an allergy, so it cannot be used."
        )
    if code == "H2":
        who = codes.get(detail["diner_key"], "a diner")
        if detail.get("line"):
            return f"Recipe {recipe_id}: {detail['line']} breaks a dietary limit of {who}."
        return f"Recipe {recipe_id} breaks a dietary limit of {who}."
    if code == "H3":
        lacking = ", ".join(detail["missing"])
        return f"Recipe {recipe_id}: needs cookware the household lacks: {lacking}."
    if code == "H4":
        return (
            f"The plan has {_counts_text(detail['present'], detail, False)}; "
            f"the user wants {_counts_text(detail['required'], detail, False)}"
            f"{_minimum_note(detail, False)}."
        )
    if code == "H7":
        ids = ", ".join(str(i) for i in detail["recipes_without"])
        if detail["rule"] == "must":
            return f"No dish in the plan uses {detail['name']}, which the user requires in the meal."
        return (
            f"Every dish must use {detail['name']}, but these recipes do not: {ids}. "
            "Replace them with dishes that use it."
        )
    if code == "H5" and detail.get("reason") == "duplicate":
        return f"Recipe {recipe_id} appears more than once."
    if code == "H5":
        return f"Recipe {recipe_id} is not in the library. Use ids from tool results only."
    if code == "H6":
        return (
            f"Recipe {recipe_id}: takes {detail['time_min']} minutes, "
            f"over the {detail['limit']}-minute limit."
        )
    return f"{code}: {json.dumps(detail, ensure_ascii=False)}"


def _unmet_for_user(finding: dict, titles: dict[int, str], locale: str) -> str:
    code, detail = finding["code"], finding["detail"]
    title = titles.get(finding["recipe_id"], "")
    zh = _zh(locale)
    if code == "H1":
        names = ("、" if zh else ", ").join(detail["missing"])
        count = len(detail["missing"])
        if zh:
            return f"{title}：缺少 {count} 种食材（{names}）"
        return f"{title}: {count} ingredient{'s' if count != 1 else ''} missing ({names})"
    if code == "H3":
        names = ("、" if zh else ", ").join(detail["missing"])
        if zh:
            return f"{title}：缺少厨具（{names}）"
        return f"{title}: missing cookware ({names})"
    if code == "H4":
        present = _counts_text(detail["present"], detail, zh)
        wanted = _counts_text(detail["required"], detail, zh)
        note = _minimum_note(detail, zh)
        if zh:
            return f"方案有 {present}，要求是 {wanted}{note}"
        return f"The plan has {present}; {wanted} were asked for{note}"
    if code == "H7":
        lacking = ("、" if zh else ", ").join(
            f"“{titles[i]}”" if zh else f'"{titles[i]}"'
            for i in detail["recipes_without"]
            if i in titles
        )
        name = detail["name"]
        if detail["rule"] == "must":
            if zh:
                return f"方案里没有用到{name}的菜"
            return f"No dish in the plan uses {name}"
        if zh:
            return f"这些菜没有用到{name}：{lacking}"
        return f"These dishes do not use {name}: {lacking}"
    if code == "H6":
        minutes, limit = detail["time_min"], detail["limit"]
        if zh:
            return f"{title}：需要 {minutes} 分钟，超过 {limit} 分钟的限制"
        return f"{title}: takes {minutes} minutes, over the {limit}-minute limit"
    return f"{title}: {code}"


def _no_plan_reason(code: str, locale: str) -> str:
    """Why no plan is shown (H2 or H5), without naming the dishes."""
    zh = _zh(locale)
    if code == "H2":
        if zh:
            return "找到的方案都含有用餐者过敏或忌口的食材"
        return "Every plan found had a dish that breaks a diner's allergies or dietary restrictions"
    if zh:
        return "提议的菜并不都来自你的菜谱库"
    return "The proposed dishes were not all from your recipe library"


def _count_phrase(n: int | None, category: str, zh: bool, minimum: bool = False) -> str:
    one, many, cn = CATEGORY_WORDS[category]
    if zh:
        return f"{'至少 ' if minimum else ''}{n} {cn}"
    return f"{'at least ' if minimum else ''}{n} {one if n == 1 else many}"


def reason_text(shortfall: dict, mode: str, locale: str) -> str:
    """One feasibility reason in plain words, in the request's locale. Fixed strings that
    state what the meal needs and what is available; never model output."""
    zh = _zh(locale)
    reason, category = shortfall["reason"], shortfall["category"]
    needed, available = shortfall["needed"], shortfall["available"]
    name = shortfall.get("ingredient")
    fridge = mode == "fridge_only"
    minimum = bool(shortfall.get("minimum"))
    if reason == "empty_inventory":
        return "冰箱里没有可以用来做菜的食材" if zh else "There is nothing in the fridge to cook with"
    if reason == "no_cookware":
        if zh:
            return "你的菜谱里没有一道能用你现有的厨具做出来"
        return "None of your recipes can be made with the cookware you have"
    if reason == "time_limit":
        return "你的菜谱里没有一道符合时间限制" if zh else "None of your recipes fits the time limit"
    if reason == "not_enough_dishes":
        have = _count_phrase(available, category, zh)
        want = _count_phrase(needed, category, zh, minimum)
        if zh:
            return f"按冰箱现有的食材只能做 {have}，这餐需要 {want}"
        return f"By the current fridge you can make {have}; this meal needs {want}"
    if reason == "not_enough_in_library":
        have = _count_phrase(available, category, zh)
        want = _count_phrase(needed, category, zh, minimum)
        if zh:
            return f"你的菜谱库里你能吃的只有 {have}，这餐需要 {want}"
        return f"Your library has {have} you can eat; this meal needs {want}"
    # named_ingredient
    if category is None:
        if fridge and shortfall.get("absent_from_stock"):
            return f"冰箱里没有{name}" if zh else f"No {name} in the fridge"
        if fridge:
            if zh:
                return f"用现有的食材做不出用到{name}的菜"
            return f"No dish that can be made from the fridge uses {name}"
        if zh:
            return f"你的菜谱库里没有用到{name}的菜"
        return f"No dish in your library uses {name}"
    have = _count_phrase(available, category, zh)
    want = _count_phrase(needed, category, zh, minimum)
    if zh:
        verb = "只能做" if fridge else "只有"
        return f"用到{name}的菜{verb} {have}，这餐需要 {want}"
    if fridge:
        return f"Only {have} with {name} can be made; this meal needs {want}"
    return f"Your library has only {have} with {name}; this meal needs {want}"


# Every next action, in the order the card shows them.
NEXT_ACTION_ORDER = (
    "switch_to_shopping",
    "fewer_dishes",
    "drop_time_limit",
    "relax_split",
    "relax_named",
    "update_inventory",
    "register_cookware",
    "import_recipes",
)
_SIDE_CATEGORIES = ("meat_dishes", "vegetable_dishes")
# Every reason and unmet rule that offers fewer_dishes: the design 5.3 table plus H2 (ruling
# after review); H5 alone gets no action.
_FEWER_DISHES_CODES = {
    "empty_inventory", "no_cookware", "not_enough_dishes", "time_limit", "not_enough_in_library",
    "named_ingredient", "H1", "H2", "H3", "H4", "H6", "H7",
}


def next_actions(
    spec: dict,
    reasons: list[str],
    unmet_codes: list[str],
    short_categories: list[str] | None = None,
) -> list[str]:
    """The next actions that fit the reasons and unmet rules (design 5.3): their union, in
    NEXT_ACTION_ORDER. `short_categories` are the feasibility categories that fell short;
    when given, relax_split needs a meat or vegetable category among them."""
    codes = set(reasons) | set(unmet_codes)
    fridge = spec["mode"] == "fridge_only"
    total = sum(spec.get(n, 0) for n in ("dishes", "soups", "staples", "desserts", "snacks"))
    chosen: set[str] = set()
    if fridge and codes & {"empty_inventory", "not_enough_dishes", "named_ingredient", "H1", "H2"}:
        chosen.add("switch_to_shopping")
    if total > 1 and codes & _FEWER_DISHES_CODES:
        chosen.add("fewer_dishes")
    if codes & {"time_limit", "H6"}:
        chosen.add("drop_time_limit")
    names_split = spec.get("meat_dishes") is not None or spec.get("vegetable_dishes") is not None
    if names_split:
        if short_categories is None:
            side_short = bool(codes & {"not_enough_in_library", "not_enough_dishes"})
        else:
            side_short = any(c in _SIDE_CATEGORIES for c in short_categories)
        if side_short or "H4" in codes:
            chosen.add("relax_split")
    if codes & {"named_ingredient", "H7"}:
        chosen.add("relax_named")
    if codes & {"empty_inventory", "not_enough_dishes", "H1"} or (
        fridge and "named_ingredient" in codes
    ):
        chosen.add("update_inventory")
    if codes & {"no_cookware", "H3"}:
        chosen.add("register_cookware")
    if codes & {"not_enough_in_library", "H4", "named_ingredient", "H7"}:
        chosen.add("import_recipes")
    return [a for a in NEXT_ACTION_ORDER if a in chosen]


# ---- the planner's opening ----


SHORTLIST_HEADER = (
    "Shortlist: candidates from the user's library for each dish type this meal needs, "
    "best first, as JSON keyed by dish type."
)


def shortlist_json(
    entries: dict[str, list[ShortlistEntry]], complete: Collection[str] = ()
) -> str:
    """The shortlist as compact JSON, in a fixed order: dish types as the shortlist orders
    them, each as {"complete": bool, "candidates": [...]} with entries in rank order and
    fields in a fixed order (the dish type is the key)."""
    data = {
        dish_type: {
            "complete": dish_type in complete,
            "candidates": [
                {name: value for name, value in asdict(entry).items() if name != "dish_type"}
                for entry in items
            ],
        }
        for dish_type, items in entries.items()
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def planner_opening(
    entries: dict[str, list[ShortlistEntry]] | None,
    summary: str,
    complete: Collection[str] = (),
) -> str:
    """The planner's first message, built once per run: the shortlist, then the request
    summary (design 5.7: most stable first). Without a shortlist, the summary alone."""
    if entries is None:
        return summary
    return f"{SHORTLIST_HEADER}\n{shortlist_json(entries, complete)}\n\n{summary}"


def _split_text(spec: dict) -> str:
    sides = [
        _count_phrase(spec[side], side, False, bool(spec.get("split_is_minimum")))
        for side in _SIDE_CATEGORIES
        if spec.get(side) is not None
    ]
    return ", ".join(sides) if sides else "none (any mix of meat and vegetable dishes)"


def _merge_notes(notes: dict | None, titles: list[str], paused: bool) -> dict:
    """Search notes with `titles` added (once each) and `paused` or-ed in."""
    notes = notes or {}
    left_out = list(notes.get("left_out_unknown_allergens", []))
    left_out += [t for t in dict.fromkeys(titles) if t not in left_out]
    return {
        "left_out_unknown_allergens": left_out,
        "semantic_paused": bool(notes.get("semantic_paused", False)) or paused,
    }


# ---- the graph ----


def build_graph(deps: GraphDeps, checkpointer) -> CompiledStateGraph:
    planner_agent: CompiledStateGraph | None = None

    def get_planner() -> CompiledStateGraph:
        # Built on first use, so conversations that never plan never build a model.
        nonlocal planner_agent
        if planner_agent is None:
            planner_agent = build_planner(deps)
        return planner_agent

    def route_intent(state: ChatState) -> dict:
        messages = [SystemMessage(load_route_prompt()), *state["messages"]]
        result = deps.client.invoke_structured(deps.task, messages, Intent)
        # Each new message is a new request, which may be asked about once.
        return {"intent": result.parsed.intent, "asked_question": False}

    def other(state: ChatState) -> dict:
        return {"messages": [AIMessage(other_reply(state.get("locale", "en")))]}

    def choose(state: ChatState) -> str:
        return "parse_request" if state["intent"] == "plan" else "other"

    def parse_request(state: ChatState) -> dict:
        messages = [SystemMessage(load_prompt("parse")), *state["messages"]]
        result = deps.client.invoke_structured(deps.task, messages, ParsedRequest)
        return {"request": result.parsed.model_dump()}

    def unclear(state: ChatState) -> dict:
        reply = UNCLEAR_REPLY_ZH if _zh(state.get("locale", "en")) else UNCLEAR_REPLY_EN
        return {"messages": [AIMessage(reply)]}

    def after_parse(state: ChatState) -> str:
        parsed = ParsedRequest.model_validate(state["request"])
        if request_problem(parsed):
            # A contradiction is asked about whatever the style, once per new user message.
            if len(state["messages"]) > state.get("problem_asked_at", -1):
                return "ask_question"
            return "unclear"
        with deps.session_factory() as session:
            style = get_request_style(session)
        if style is None:
            return "ask_style"
        if not state.get("asked_question") and needs_question(parsed, style):
            return "ask_question"
        return "load_context"

    def ask_style(state: ChatState) -> dict:
        value = interrupt({"kind": "request_style"})
        if value in REQUEST_STYLES:
            with deps.session_factory() as session:
                set_request_style(session, value)
            return {}
        # Skipped: the question is asked once, and skipping means plan then adjust (design 6.2).
        with deps.session_factory() as session:
            set_request_style(session, "plan_then_adjust")
        return {}

    def ask_question(state: ChatState) -> dict:
        parsed = ParsedRequest.model_validate(state["request"])
        problem = request_problem(parsed)
        update: dict[str, Any] = {}
        if problem:
            answer = interrupt({"kind": "question", "fields": ["composition"], "problem": problem})
            update["problem_asked_at"] = len(state["messages"])
        else:
            # Only the ask-first question is limited to once per request.
            update["asked_question"] = True
            fields = needs_question(parsed, "ask_first")
            answer = interrupt({"kind": "question", "fields": fields})
        if isinstance(answer, str) and answer.strip():
            update["messages"] = [HumanMessage(answer)]
        return update

    def load_context(state: ChatState, config: RunnableConfig) -> dict:
        parsed = ParsedRequest.model_validate(state["request"])
        with deps.session_factory() as session:
            self_id = session.scalar(select(Diner.id).where(Diner.is_self))
        spec, constraints, defaults_used = apply_defaults(parsed, self_id)
        # The self diner plus everyone not yet confirmed (apply_defaults' headcount).
        headcount = constraints.unconfirmed_diners + 1
        request = {
            "messages": [deps.client.prepare_text(t) for t in _current_request(state["messages"])],
            "parsed": map_strings(parsed.model_dump(), deps.client.prepare_text),
            # The spec carries the named ingredients, which may hold a replaced name.
            "spec": map_strings(spec.model_dump(), deps.client.prepare_text),
            "headcount": headcount,
            "locale": state.get("locale", "en"),
        }
        run_id = deps.recorder.start(
            thread_id=config["configurable"]["thread_id"],
            request=request,
            defaults_used=defaults_used,
            prompt_version=PROMPT_VERSION,
            tools_version=TOOLS_VERSION,
            has_temporary_guests=False,
        )
        return {
            "spec": spec.model_dump(),
            "constraints": {
                "diner_ids": list(constraints.diner_ids),
                "unconfirmed_diners": constraints.unconfirmed_diners,
                "headcount": headcount,
            },
            "defaults_used": defaults_used,
            "run_id": run_id,
            "retries": 0,
            "proposal": None,
            "validation": None,
            "first_passed": None,
            "best": None,
            "blocked_codes": [],
            "shortfalls": [],
            "search_notes": {"left_out_unknown_allergens": [], "semantic_paused": False},
            "planner_opening": None,
            "shortlist_ids": [],
        }

    def feasibility_step(state: ChatState) -> Command:
        """No model call: when the request cannot be met, skip the planner and say why."""
        spec = PlanSpec.model_validate(state["spec"])
        with deps.session_factory() as session:
            ctx = load_planning_context(session, _constraints(state))
            found: list[Shortfall] = check_feasibility(session, ctx, spec)
        if not found:
            return Command(update={"shortfalls": []}, goto="planner")
        dumped = [asdict(shortfall) for shortfall in found]
        deps.recorder.add_event(
            state["run_id"],
            {
                "kind": "feasibility",
                "shortfalls": map_strings(dumped, deps.client.prepare_text),
            },
        )
        return Command(update={"shortfalls": dumped}, goto="present_plan")

    def request_summary(state: ChatState, preferences: list[str]) -> str:
        spec, parsed = state["spec"], state["request"]
        lines = [
            f"Reply locale: {state.get('locale', 'en')}",
            f"Mode: {spec['mode']}",
            f"Headcount: {state['constraints']['headcount']}",
            f"Dishes (meat plus vegetable): {spec['dishes']}",
            f"Split: {_split_text(spec)}",
            f"Soups: {spec['soups']}",
            f"Staple foods: {spec.get('staples', 0)}, desserts: {spec.get('desserts', 0)}, "
            f"snacks: {spec.get('snacks', 0)}",
            "Every count is exact unless it says at least.",
            f"Time limit: {spec['time_limit_min'] or 'none'}"
            + (" minutes" if spec["time_limit_min"] else ""),
            f"Filled by default: {', '.join(state['defaults_used']) or 'nothing'}",
        ]
        if parsed.get("query"):
            lines.append(f"The user feels like: {parsed['query']}")
        # The spec carries the named ingredients as the request settled them.
        if spec.get("must_use"):
            lines.append(f"Must use in at least one dish: {', '.join(spec['must_use'])}")
        if spec.get("all_use"):
            lines.append(f"Every dish must use: {', '.join(spec['all_use'])}")
        if spec.get("prefer_use"):
            lines.append(f"Prefer using: {', '.join(spec['prefer_use'])}")
        if parsed.get("exclude_recent_days"):
            lines.append(f"Exclude dishes from the last {parsed['exclude_recent_days']} days")
        lines.append("Diners' dietary limits:")
        lines.extend(f"- {line}" for line in preferences or ["none recorded"])
        return "Plan this meal.\n" + "\n".join(lines)

    def retry_message(state: ChatState, session, ctx) -> str:
        """The violations of the previous attempt, then a short summary of it."""
        codes = diner_codes(_constraints(state))
        ids = state["proposal"]["recipe_ids"]
        problems = [_violation_for_model(v, codes) for v in state["validation"]["violations"]]
        known = set(session.scalars(select(Recipe.id).where(Recipe.id.in_(ids)))) if ids else set()
        missing = compute_missing(session, ctx, sorted(known)) if known else {}
        per_recipe = [
            f"{i}: " + (", ".join(line.name for line in missing.get(i, [])) or "nothing")
            for i in dict.fromkeys(ids)
            if i in known
        ]
        offered = state.get("shortlist_ids") or []
        summary = [
            "Shortlist ids offered: " + (", ".join(str(i) for i in offered) or "none"),
            "Ids proposed: " + (", ".join(str(i) for i in ids) or "none"),
            "Missing items per proposed recipe: " + ("; ".join(per_recipe) or "none"),
        ]
        return (
            f"Your proposal {ids} failed these checks. Propose a new plan that fixes each one:\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\n\nThe previous attempt:\n"
            + "\n".join(f"- {line}" for line in summary)
        )

    def planner(state: ChatState) -> dict:
        try:
            return plan_attempt(state)
        except BaseException:
            # The run will not reach present_plan: drop its query embeddings here.
            deps.run_query_vectors.pop(state["run_id"], None)
            raise

    def plan_attempt(state: ChatState) -> dict:
        constraints = _constraints(state)
        spec = PlanSpec.model_validate(state["spec"])
        # One cache per run, so the shortlist and every attempt embed a query once.
        vectors = deps.run_query_vectors.setdefault(state["run_id"], {})
        update: dict[str, Any] = {}
        notes = state.get("search_notes")
        opening = state.get("planner_opening")
        with deps.session_factory() as session:
            ctx = load_planning_context(session, constraints)
            if opening is None:
                # Built once per run and stored, so every attempt sends the same bytes.
                summary = request_summary(state, meal_preferences(session, ctx))
                entries = None
                complete: frozenset[str] = frozenset()
                if deps.use_shortlist:
                    parsed = ParsedRequest.model_validate(state["request"])
                    found = build_shortlist_result(
                        session, deps.client, ctx, spec, parsed, vectors
                    )
                    entries = found.entries
                    complete = found.complete
                    notes = _merge_notes(
                        notes, found.left_out_unknown_allergens, found.semantic_paused
                    )
                opening = planner_opening(entries, summary, complete)
                update["planner_opening"] = opening
                update["shortlist_ids"] = [
                    entry.id for items in (entries or {}).values() for entry in items
                ]
            messages = [HumanMessage(opening)]
            validation = state.get("validation")
            if validation is not None and not validation["passed"]:
                messages.append(HumanMessage(retry_message(state, session, ctx)))
        context = PlannerDeps(
            deps.session_factory,
            deps.client,
            constraints,
            spec.mode,
            query_vectors=vectors,
            must_use=list(spec.must_use),
            all_use=list(spec.all_use),
            prefer_use=list(spec.prefer_use),
            time_limit_min=spec.time_limit_min,
            exclude_recent_days=state["request"].get("exclude_recent_days"),
            query=state["request"].get("query"),
        )
        out = get_planner().invoke(
            {"messages": messages, "run_id": state["run_id"]}, context=context
        )
        # A run cut off by the call limits or the token budget has no proposal; validation
        # then fails it.
        proposal = out.get("structured_response") or PlanProposal(recipe_ids=[], note="")
        for message in out["messages"]:
            if not (isinstance(message, ToolMessage) and message.name == "search_recipes"):
                continue
            try:
                result = json.loads(message.content)
            except (TypeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            notes = _merge_notes(
                notes,
                list(result.get("left_out_unknown_allergens", [])),
                bool(result.get("semantic_paused")),
            )
        return {
            **update,
            "proposal": proposal.model_dump(),
            "search_notes": _merge_notes(notes, [], False),
        }

    def check_plan(state: ChatState) -> Command:
        spec = PlanSpec.model_validate(state["spec"])
        ids = state["proposal"]["recipe_ids"]
        with deps.session_factory() as session:
            ctx = load_planning_context(session, _constraints(state))
            result: ValidationResult = validate_plan(session, ctx, spec, ids)
        dumped = result.model_dump(mode="json")
        deps.recorder.add_event(
            state["run_id"],
            {
                "kind": "validation",
                "attempt": state["retries"],
                "recipe_ids": ids,
                "result": map_strings(dumped, deps.client.prepare_text),
            },
        )
        update: dict[str, Any] = {"validation": dumped}
        if state.get("first_passed") is None:
            update["first_passed"] = result.passed
        blocked = [code for code in NEVER_SHOWN if any(v.code == code for v in result.violations)]
        if blocked:
            seen = state.get("blocked_codes") or []
            update["blocked_codes"] = [c for c in NEVER_SHOWN if c in seen or c in blocked]
        else:
            best = state.get("best")
            gap = _composition_gap_of(spec, dumped)
            attempt = (len(result.violations), gap)
            previous = (
                (len(best["validation"]["violations"]), best.get("gap", 0)) if best else None
            )
            # Fewest violations, then the smallest composition gap; a later attempt wins a
            # tie, since it saw the errors.
            if previous is None or attempt <= previous:
                update["best"] = {"proposal": state["proposal"], "validation": dumped, "gap": gap}
        if not result.passed and state["retries"] < MAX_RETRIES:
            update["retries"] = state["retries"] + 1
            return Command(update=update, goto="check_feasibility")
        return Command(update=update, goto="present_plan")

    def present_plan(state: ChatState) -> dict:
        locale = state.get("locale", "en")
        spec = state["spec"]
        final = state["validation"] or {"passed": False}
        shortfalls = state.get("shortfalls") or []
        constraints = _constraints(state)
        headcount = state["constraints"]["headcount"]
        notes = state.get("search_notes") or {}
        if shortfalls:
            shown = None
        elif final["passed"]:
            shown = {"proposal": state["proposal"], "validation": final}
        else:
            # Design 6.3: the closest attempt without an H2 or H5 finding, if any.
            shown = state.get("best")

        plan: dict[str, Any] = {
            "recipes": [],
            "missing": {},
            "missing_cookware": {},
            "warnings": [],
            "unmet": [],
            "next_actions": [],
            "defaults_used": state["defaults_used"],
            "spec": spec,
            "headcount": headcount,
            "servings_notes": [],
            "left_out_unknown_allergens": notes.get("left_out_unknown_allergens", []),
            "semantic_paused": notes.get("semantic_paused", False),
            "reasons": [],
            "passed": final["passed"],
        }
        if shortfalls:
            # Design 5.2: the planner did not run (or a retry was stopped): fixed text, no recipes.
            plan["passed"] = False
            plan["reasons"] = [
                {
                    "reason": s["reason"],
                    "category": s["category"],
                    "needed": s["needed"],
                    "available": s["available"],
                    "ingredient": s.get("ingredient"),
                    "absent_from_stock": bool(s.get("absent_from_stock")),
                    "text": reason_text(s, spec["mode"], locale),
                }
                for s in shortfalls
            ]
            plan["next_actions"] = next_actions(
                spec,
                [s["reason"] for s in shortfalls],
                [],
                [s["category"] for s in shortfalls if s["category"]],
            )
            note = EARLY_STOP_NOTE_ZH if _zh(locale) else EARLY_STOP_NOTE_EN
        elif shown is None:
            # Design 6.3: a plan breaking H2 or H5 is never shown; the note is fixed text.
            plan["unmet"] = [
                {"code": code, "recipe_id": None, "text": _no_plan_reason(code, locale)}
                for code in state.get("blocked_codes") or []
            ]
            plan["next_actions"] = next_actions(spec, [], list(state.get("blocked_codes") or []))
            if plan["next_actions"]:
                note = NO_PLAN_NOTE_ZH if _zh(locale) else NO_PLAN_NOTE_EN
            else:
                note = NO_PLAN_NOTE_BARE_ZH if _zh(locale) else NO_PLAN_NOTE_BARE_EN
        else:
            ids = shown["proposal"]["recipe_ids"]
            with deps.session_factory() as session:
                rows = {r.id: r for r in session.scalars(select(Recipe).where(Recipe.id.in_(ids)))}
                ctx = load_planning_context(session, constraints)
                missing = compute_missing(session, ctx, ids)
                cookware = missing_cookware(session, ctx, ids)
                recipes = [rows[i] for i in ids]
                plan["recipes"] = [
                    {
                        "id": r.id,
                        "title": r.title,
                        "dish_type": r.dish_type,
                        "time_min": r.time_min,
                        "time_source": r.time_source,
                        "servings": r.servings,
                    }
                    for r in recipes
                ]
            titles = {r["id"]: r["title"] for r in plan["recipes"]}
            plan["missing"] = {
                str(i): [line.name for line in lines] for i, lines in missing.items() if lines
            }
            plan["missing_cookware"] = {
                str(i): [name for _, name in items] for i, items in cookware.items() if items
            }
            plan["warnings"] = shown["validation"]["warnings"]
            plan["servings_notes"] = [
                {"recipe_id": r["id"], "servings": r["servings"], "headcount": headcount}
                for r in plan["recipes"]
                if r["servings"] is not None and r["servings"] != headcount
            ]
            if not shown["validation"]["passed"]:
                plan["passed"] = False
                plan["unmet"] = [
                    {
                        "code": v["code"],
                        "recipe_id": v["recipe_id"],
                        "text": _unmet_for_user(v, titles, locale),
                    }
                    for v in shown["validation"]["violations"]
                ]
                plan["next_actions"] = next_actions(
                    spec, [], [v["code"] for v in shown["validation"]["violations"]]
                )
            note = shown["proposal"]["note"]
            if not shown["validation"]["passed"]:
                # The fixed closest note always leads; the model's own note may follow it.
                closest = CLOSEST_NOTE_ZH if _zh(locale) else CLOSEST_NOTE_EN
                note = closest + "\n\n" + note if note else closest
        # The run is over: its query embeddings are no longer needed.
        deps.run_query_vectors.pop(state["run_id"], None)
        deps.recorder.finish(
            state["run_id"],
            retries=state["retries"],
            first_passed=bool(state.get("first_passed")),
            final_passed=plan["passed"],
            # The record keeps the replaced form; the state keeps the restored one.
            final_plan=map_strings(plan, deps.client.prepare_text),
        )
        return {
            "current_plan": plan,
            "plan_versions": [*(state.get("plan_versions") or []), plan],
            "messages": [AIMessage(note)],
        }

    builder = StateGraph(ChatState)
    builder.add_node("route_intent", route_intent)
    builder.add_node("other", other)
    builder.add_node("parse_request", parse_request)
    builder.add_node("ask_style", ask_style)
    builder.add_node("ask_question", ask_question)
    builder.add_node("unclear", unclear)
    builder.add_node("load_context", load_context)
    builder.add_node("planner", planner)
    builder.add_node(
        "check_feasibility", feasibility_step, destinations=("planner", "present_plan")
    )
    builder.add_node(
        "validate_plan", check_plan, destinations=("check_feasibility", "present_plan")
    )
    builder.add_node("present_plan", present_plan)
    builder.add_edge(START, "route_intent")
    builder.add_conditional_edges("route_intent", choose, ["parse_request", "other"])
    gates = ["ask_style", "ask_question", "load_context", "unclear"]
    builder.add_conditional_edges("parse_request", after_parse, gates)
    builder.add_conditional_edges("ask_style", after_parse, gates)
    builder.add_edge("ask_question", "parse_request")
    builder.add_edge("load_context", "check_feasibility")
    builder.add_edge("planner", "validate_plan")
    builder.add_edge("present_plan", END)
    builder.add_edge("other", END)
    builder.add_edge("unclear", END)
    return builder.compile(checkpointer=checkpointer)
