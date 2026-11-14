import json
from dataclasses import dataclass
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
from minemydinner.planning.context import MealConstraints, diner_codes, load_planning_context
from minemydinner.planning.inventory import meal_preferences
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


def load_route_prompt() -> str:
    return load_prompt("route.md")


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
        wanted_dishes, wanted_soups = detail["wanted"]
        return (
            f"The plan has {detail['dishes']} dishes and {detail['soups']} soups; "
            f"the user wants {wanted_dishes} dishes and {wanted_soups} soups."
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
        dishes, soups = detail["wanted"]
        if zh:
            return (
                f"方案有 {detail['dishes']} 道菜和 {detail['soups']} 道汤，"
                f"要求是 {dishes} 道菜和 {soups} 道汤"
            )
        return (
            f"The plan has {detail['dishes']} dishes and {detail['soups']} soups; "
            f"{dishes} dishes and {soups} soups were asked for"
        )
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


def _next_actions(spec: dict) -> list[str]:
    actions = []
    if spec["mode"] == "fridge_only":
        actions.append("switch_to_shopping")
    counted = sum(
        spec.get(name, 0) for name in ("dishes", "soups", "staples", "desserts", "snacks")
    )
    if counted > 1:
        actions.append("fewer_dishes")
    if spec["time_limit_min"] is not None:
        actions.append("drop_time_limit")
    return actions


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
        messages = [SystemMessage(load_prompt("parse.md")), *state["messages"]]
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
            "search_notes": {"left_out_unknown_allergens": [], "semantic_paused": False},
        }

    def request_summary(state: ChatState, preferences: list[str]) -> str:
        spec, parsed = state["spec"], state["request"]
        lines = [
            f"Reply locale: {state.get('locale', 'en')}",
            f"Mode: {spec['mode']}",
            f"Dishes: {spec['dishes']}, soups: {spec['soups']}",
            f"Headcount: {state['constraints']['headcount']}",
            f"Time limit: {spec['time_limit_min'] or 'none'}"
            + (" minutes" if spec["time_limit_min"] else ""),
            f"Filled by default: {', '.join(state['defaults_used']) or 'nothing'}",
        ]
        if parsed.get("query"):
            lines.append(f"The user feels like: {parsed['query']}")
        counts = [
            f"{name.replace('_', ' ')}: {spec[name]}"
            for name in ("meat_dishes", "vegetable_dishes", "staples", "desserts", "snacks")
            if spec.get(name)
        ]
        if counts:
            suffix = " (the split counts are minimums)" if spec.get("split_is_minimum") else ""
            lines.append(f"Also: {', '.join(counts)}{suffix}")
        if parsed.get("must_use"):
            lines.append(f"Must use in at least one dish: {', '.join(parsed['must_use'])}")
        if parsed.get("all_use"):
            lines.append(f"Every dish must use: {', '.join(parsed['all_use'])}")
        if parsed.get("prefer_use"):
            lines.append(f"Prefer using: {', '.join(parsed['prefer_use'])}")
        if parsed.get("exclude_recent_days"):
            lines.append(f"Exclude dishes from the last {parsed['exclude_recent_days']} days")
        lines.append("Diners' dietary limits:")
        lines.extend(f"- {line}" for line in preferences or ["none recorded"])
        return "Plan this meal.\n" + "\n".join(lines)

    def retry_message(state: ChatState) -> str:
        codes = diner_codes(_constraints(state))
        ids = state["proposal"]["recipe_ids"]
        problems = [_violation_for_model(v, codes) for v in state["validation"]["violations"]]
        return (
            f"Your proposal {ids} failed these checks. Propose a new plan that fixes each one:\n"
            + "\n".join(f"- {p}" for p in problems)
        )

    def planner(state: ChatState) -> dict:
        constraints = _constraints(state)
        with deps.session_factory() as session:
            ctx = load_planning_context(session, constraints)
            preferences = meal_preferences(session, ctx)
        messages = [HumanMessage(request_summary(state, preferences))]
        validation = state.get("validation")
        if validation is not None and not validation["passed"]:
            messages.append(HumanMessage(retry_message(state)))
        context = PlannerDeps(
            deps.session_factory, deps.client, constraints, state["spec"]["mode"]
        )
        out = get_planner().invoke(
            {"messages": messages, "run_id": state["run_id"]}, context=context
        )
        # A run cut off by the call limits has no proposal; validation then fails it.
        proposal = out.get("structured_response") or PlanProposal(recipe_ids=[], note="")
        notes = dict(state.get("search_notes") or {})
        left_out = list(notes.get("left_out_unknown_allergens", []))
        paused = bool(notes.get("semantic_paused", False))
        for message in out["messages"]:
            if not (isinstance(message, ToolMessage) and message.name == "search_recipes"):
                continue
            try:
                result = json.loads(message.content)
            except (TypeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            for title in result.get("left_out_unknown_allergens", []):
                if title not in left_out:
                    left_out.append(title)
            paused = paused or bool(result.get("semantic_paused"))
        return {
            "proposal": proposal.model_dump(),
            "search_notes": {"left_out_unknown_allergens": left_out, "semantic_paused": paused},
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
            # Fewest violations; a later attempt wins a tie, since it saw the errors.
            if best is None or len(result.violations) <= len(best["validation"]["violations"]):
                update["best"] = {"proposal": state["proposal"], "validation": dumped}
        if not result.passed and state["retries"] < MAX_RETRIES:
            update["retries"] = state["retries"] + 1
            return Command(update=update, goto="planner")
        return Command(update=update, goto="present_plan")

    def present_plan(state: ChatState) -> dict:
        locale = state.get("locale", "en")
        spec, final = state["spec"], state["validation"]
        constraints = _constraints(state)
        headcount = state["constraints"]["headcount"]
        notes = state.get("search_notes") or {}
        if final["passed"]:
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
            "passed": final["passed"],
        }
        if shown is None:
            # Design 6.3: a plan breaking H2 or H5 is never shown; the note is fixed text.
            plan["unmet"] = [
                {"code": code, "recipe_id": None, "text": _no_plan_reason(code, locale)}
                for code in state.get("blocked_codes") or []
            ]
            plan["next_actions"] = _next_actions(spec)
            note = NO_PLAN_NOTE_ZH if _zh(locale) else NO_PLAN_NOTE_EN
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
                plan["next_actions"] = _next_actions(spec)
            note = shown["proposal"]["note"]
            if not shown["validation"]["passed"]:
                # The fixed closest note always leads; the model's own note may follow it.
                closest = CLOSEST_NOTE_ZH if _zh(locale) else CLOSEST_NOTE_EN
                note = closest + "\n\n" + note if note else closest
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
    builder.add_node("validate_plan", check_plan, destinations=("planner", "present_plan"))
    builder.add_node("present_plan", present_plan)
    builder.add_edge(START, "route_intent")
    builder.add_conditional_edges("route_intent", choose, ["parse_request", "other"])
    gates = ["ask_style", "ask_question", "load_context", "unclear"]
    builder.add_conditional_edges("parse_request", after_parse, gates)
    builder.add_conditional_edges("ask_style", after_parse, gates)
    builder.add_edge("ask_question", "parse_request")
    builder.add_edge("load_context", "planner")
    builder.add_edge("planner", "validate_plan")
    builder.add_edge("present_plan", END)
    builder.add_edge("other", END)
    builder.add_edge("unclear", END)
    return builder.compile(checkpointer=checkpointer)
