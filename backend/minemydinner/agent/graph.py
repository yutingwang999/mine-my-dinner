import dataclasses
import inspect
import json
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel
from sqlalchemy import select

from minemydinner.agent.card_text import (
    counts_text,
    is_zh,
    next_actions,
    no_plan_reason,
    reason_text,
    side_phrases,
)
from minemydinner.agent.diners import (
    DinerAnswer,
    SettledDiners,
    answer_summary,
    apply_answer,
    build_card,
    card_needed,
    constraints_from,
    guest_key,
    not_yet_known,
    profile_rules,
    settled_friend_deleted,
    settled_rules,
    stated_friend_rules,
    stated_to_rules,
)
from minemydinner.agent.labels import (
    Friend,
    LabelTable,
    current_labels,
    current_table,
    find_friends,
    labels_scope,
    load_label_context,
)
from minemydinner.agent.middleware import dump_message
from minemydinner.agent.plan_card import (
    build_dishes_card,
    card_base,
    meal_constraints,
)
from minemydinner.agent.planner import TOOLS_VERSION, PlanProposal, build_planner
from minemydinner.agent.questions import parse_messages
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import (
    PROMPT_VERSION,
    REPLAN_MESSAGE,
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
from minemydinner.planning.composition import composition_gap, side_ranges
from minemydinner.planning.context import diner_codes, load_planning_context
from minemydinner.planning.feasibility import Shortfall, check_feasibility, needed_dish_types
from minemydinner.planning.inventory import meal_preferences
from minemydinner.planning.search import ExcludedRecipe, unknown_allergen_exclusions
from minemydinner.planning.shortlist import ShortlistEntry, build_shortlist_result
from minemydinner.planning.stock import compute_missing
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


def _model_call_event(client: Any, step: str, messages: list, result: Any) -> dict:
    """A route or parse call as a planning-record event, in the planner's `model_call` shape.
    Every string goes through the pre-hooks, so saved nicknames are recorded as codes."""
    return {
        "kind": "model_call",
        "step": step,
        "model_name": result.model_name,
        "provider": getattr(result, "provider", None),
        "fallback_used": result.fallback_used,
        "served_model": getattr(result, "served_model", None),
        "messages": [dump_message(m) for m in client.prepare_messages(messages)],
        "response": map_strings(result.parsed.model_dump(), client.prepare_text),
    }


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


def _matched_friend_ids(
    texts: Sequence[str],
    friends: Sequence[Friend],
    food_terms: Collection[str],
    parsed: ParsedRequest,
    labels: LabelTable,
) -> list[int]:
    """Saved friends named in the current request's own words, then those the parse gave a
    stated rule by label, in that order, once each."""
    found = [
        friend.diner_id
        for text in texts
        for _, _, friend in find_friends(text, friends, food_terms)
    ]
    found += list(stated_friend_rules(parsed, labels))
    existing = {friend.diner_id for friend in friends}
    return [diner_id for diner_id in dict.fromkeys(found) if diner_id in existing]


def _reuses_parse(state: ChatState) -> bool:
    """True when the latest message is the fixed "plan again" message (spec 5.7) and the
    thread holds a parsed request: code reuses that request instead of a route and a parse
    call. The fixed text as a thread's first message takes the normal path."""
    last = state["messages"][-1]
    return (
        state.get("request") is not None
        and isinstance(last, HumanMessage)
        and isinstance(last.content, str)
        and last.content.strip() in REPLAN_MESSAGE.values()
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
            f"The plan has {counts_text(detail, False)}; "
            f"the user wants {counts_text(detail, False, wanted=True)}."
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
    """The meat and vegetable sides for the request summary, from their tightened ranges
    (design 5.11). A spec saved before bounds existed is read by PlanSpec."""
    parsed = PlanSpec.model_validate(spec)
    sides = side_phrases(side_ranges(parsed), parsed.dishes, False).values()
    return ", ".join(sides) if sides else "none (any mix of meat and vegetable dishes)"


def _merge_notes(
    notes: dict | None,
    titles: list[str],
    paused: bool,
    excluded: Sequence[ExcludedRecipe] = (),
) -> dict:
    """Search notes with `titles` and `excluded` added (once each) and `paused` or-ed in.
    `left_out_details` holds the ingredient lines per recipe; notes saved before it existed
    have titles only."""
    notes = notes or {}
    left_out = list(notes.get("left_out_unknown_allergens", []))
    details = list(notes.get("left_out_details", []))
    known = {d["id"] for d in details}
    for item in excluded:
        if item.id not in known:
            known.add(item.id)
            details.append({"id": item.id, "title": item.title, "lines": list(item.lines)})
    titles = [*titles, *(item.title for item in excluded)]
    left_out += [t for t in dict.fromkeys(titles) if t not in left_out]
    return {
        "left_out_unknown_allergens": left_out,
        "left_out_details": details,
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
        # Each new message is a new request, which may be asked about once.
        fresh = {"asked_question": False}
        if _reuses_parse(state):
            # "Plan again": the previous parsed request stands as this turn's parse, with
            # no model call; choose sends the turn on as after a parse.
            return {**fresh, "intent": "plan", "model_calls": [{"kind": "parse_reused"}]}
        messages = [SystemMessage(load_route_prompt()), *state["messages"]]
        result = deps.client.invoke_structured(deps.task, messages, Intent)
        return {
            **fresh,
            "intent": result.parsed.intent,
            # A new turn starts the buffer fresh, so events left by a turn that failed
            # before load_context are never recorded under this run.
            "model_calls": [_model_call_event(deps.client, "route", messages, result)],
        }

    def other(state: ChatState) -> dict:
        # The turn ends without planning, so its route call is not recorded anywhere.
        return {
            "messages": [AIMessage(other_reply(state.get("locale", "en")))],
            "model_calls": [],
        }

    def choose(state: ChatState) -> str:
        if state["intent"] != "plan":
            return "other"
        return after_parse(state) if _reuses_parse(state) else "parse_request"

    def parse_request(state: ChatState) -> dict:
        # The parse also reads the assistant's questions, so a short answer such as "两个"
        # sits next to the question it answers (spec 5.6). The route input is unchanged.
        conversation = parse_messages(
            state["messages"], state.get("questions") or [], state.get("locale", "en")
        )
        messages = [SystemMessage(load_prompt("parse")), *conversation]
        result = deps.client.invoke_structured(deps.task, messages, ParsedRequest)
        return {
            "request": _restore_parsed(result.parsed.model_dump()),
            "model_calls": [
                *(state.get("model_calls") or []),
                _model_call_event(deps.client, "parse", messages, result),
            ],
        }

    def _restore_parsed(parsed: dict) -> dict:
        """The parse runs without the post-hook so friend_rules codes stay labels for code to
        resolve; every other field gets its labels restored here, so no label reaches a
        text shown to the user (a named ingredient, the query, a guest's name)."""
        ctx = current_labels.get()
        if ctx is None:
            return parsed
        codes = [item["code"] for item in parsed.get("friend_rules") or []]
        with labels_scope(dataclasses.replace(ctx, restore=True)):
            restored = map_strings(parsed, deps.client.restore_text)
        for item, code in zip(restored.get("friend_rules") or [], codes, strict=True):
            item["code"] = code
        return restored

    def unclear(state: ChatState) -> dict:
        reply = UNCLEAR_REPLY_ZH if is_zh(state.get("locale", "en")) else UNCLEAR_REPLY_EN
        return {"messages": [AIMessage(reply)], "model_calls": []}

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
        # "Plan again" reuses the request the diners were settled for: the card shows only
        # when a friend in the settled set was deleted since (never drop a person silently).
        if _reuses_parse(state):
            if friend_deleted_since(state):
                return "select_diners"
        elif diner_card_needed(state, parsed):
            return "select_diners"
        return "load_context"

    def friend_deleted_since(state: ChatState) -> bool:
        """A friend in the settled set no longer exists; read from the database."""
        settled = state.get("settled_diners")
        if not settled:
            return False
        with deps.session_factory() as session:
            self_id = session.scalar(select(Diner.id).where(Diner.is_self))
            existing = set(session.scalars(select(Diner.id).where(Diner.is_self.is_(False))))
        return settled_friend_deleted(settled, existing, self_id)

    def diner_card_needed(state: ChatState, parsed: ParsedRequest) -> bool:
        """Spec 5.1, read from the database; no model call."""
        settled = state.get("settled_diners")
        with deps.session_factory() as session:
            self_id = session.scalar(select(Diner.id).where(Diner.is_self))
            table = LabelTable.from_state(state.get("diner_labels"), self_id)
            ctx = load_label_context(session, table, state.get("locale", "en"), restore=False)
            matched = _matched_friend_ids(
                _current_request(state["messages"]), ctx.friends, ctx.food_terms, parsed, table
            )
            by_friend = stated_friend_rules(parsed, table)
            saved = profile_rules(session, [i for i in (self_id, *by_friend) if i is not None])
            new_self_rules = []
            if self_id is not None:
                new_self_rules = not_yet_known(
                    stated_to_rules(session, parsed.self_rules),
                    [*saved[self_id], *settled_rules(settled, f"diner:{self_id}")],
                )
            # Stated rules of the others that neither a profile nor the settled set holds.
            uncovered = []
            for diner_id, items in by_friend.items():
                known = [*saved[diner_id], *settled_rules(settled, f"diner:{diner_id}")]
                for item in items:
                    uncovered += not_yet_known(stated_to_rules(session, item), known)
            for guest in parsed.guests:
                key = guest_key(settled, guest.name)
                known = settled_rules(settled, key) if key else []
                uncovered += not_yet_known(stated_to_rules(session, guest), known)
        return card_needed(
            parsed,
            matched,
            new_self_rules,
            settled,
            {friend.diner_id for friend in ctx.friends},
            self_id=self_id,
            uncovered_rules=uncovered,
            self_diet_words=parsed.self_rules.diet_words if parsed.self_rules else (),
        )

    def select_diners(state: ChatState) -> dict:
        """The diner card (spec 5.2, 5.3). A confirmed answer (a dict) is applied by code and
        becomes the conversation's settled diner set; guests get labels in the table. A
        skipped card (None or text) plans as before the card existed: the user alone with
        the parse's headcount, the rest unconfirmed, and no settled set."""
        parsed = ParsedRequest.model_validate(state["request"])
        ctx = current_labels.get()
        texts = _current_request(state["messages"])
        with deps.session_factory() as session:
            matched = _matched_friend_ids(texts, ctx.friends, ctx.food_terms, parsed, ctx.table)
            card = build_card(session, parsed, ctx.table, matched, "\n".join(texts))
        answer = interrupt(card)
        update: dict[str, Any] = {"settled_diners": None}
        if not isinstance(answer, dict):
            asked = {"kind": "diners", "summary": None}
            update["questions"] = [*(state.get("questions") or []), _question(state, asked)]
            return update
        chosen = DinerAnswer.model_validate(answer)
        with deps.session_factory() as session:
            # Everything that can fail runs before apply_answer commits: a node re-run after
            # the commit would create a saved guest twice.
            asked = {"kind": "diners", "summary": answer_summary(session, chosen)}
            update["questions"] = [*(state.get("questions") or []), _question(state, asked)]
            settled = apply_answer(session, chosen, ctx.table, stated_headcount=parsed.headcount)
            update["settled_diners"] = settled.to_state()
        return update

    def _question(state: ChatState, value: dict) -> dict:
        # Shown in the chat and rendered into the parse input (parse_messages): `at` is the
        # message count when the question was asked.
        return {"at": len(state["messages"]), "value": value}

    def ask_style(state: ChatState) -> dict:
        asked = {"kind": "request_style"}
        value = interrupt(asked)
        record = {"questions": [*(state.get("questions") or []), _question(state, asked)]}
        if value in REQUEST_STYLES:
            with deps.session_factory() as session:
                set_request_style(session, value)
            return record
        # Skipped: the question is asked once, and skipping means plan then adjust (design 6.2).
        with deps.session_factory() as session:
            set_request_style(session, "plan_then_adjust")
        return record

    def ask_question(state: ChatState) -> dict:
        parsed = ParsedRequest.model_validate(state["request"])
        problem = request_problem(parsed)
        update: dict[str, Any] = {}
        if problem:
            asked = {"kind": "question", "fields": ["composition"], "problem": problem}
            answer = interrupt(asked)
            update["problem_asked_at"] = len(state["messages"])
        else:
            # Only the ask-first question is limited to once per request.
            update["asked_question"] = True
            fields = needs_question(parsed, "ask_first")
            asked = {"kind": "question", "fields": fields}
            answer = interrupt(asked)
        update["questions"] = [*(state.get("questions") or []), _question(state, asked)]
        if isinstance(answer, str) and answer.strip():
            update["messages"] = [HumanMessage(answer)]
        return update

    def load_context(state: ChatState, config: RunnableConfig) -> dict:
        parsed = ParsedRequest.model_validate(state["request"])
        with deps.session_factory() as session:
            self_id = session.scalar(select(Diner.id).where(Diner.is_self))
        # The settled set stands until a confirmed card replaces it or a skipped card clears
        # it; a change in who eats always shows the card (spec 5.1).
        data = state.get("settled_diners")
        settled = SettledDiners.from_state(data) if data else None
        spec, constraints, defaults_used, headcount = apply_defaults(
            parsed, self_id, settled.headcount if settled else None
        )
        if settled is not None:
            constraints = constraints_from(settled)
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
            # Guests' names cannot be anonymized (design 6, 7.6): true when this run's parse
            # listed a guest (their names reached the provider) or an unsaved guest eats.
            has_temporary_guests=bool(parsed.guests or (settled and settled.guest_names)),
        )
        # Bound the cache: a run that failed after its planner started (for example in a
        # validation or feasibility re-check) never reached present_plan, so drop every
        # other run's vectors when a new run starts.
        for stale in [r for r in deps.run_query_vectors if r != run_id]:
            del deps.run_query_vectors[stale]
        # The route and parse calls made before the run existed, in call order.
        for event in state.get("model_calls") or []:
            deps.recorder.add_event(run_id, event)
        return {
            "model_calls": [],
            "spec": spec.model_dump(),
            "constraints": {
                "diner_ids": list(constraints.diner_ids),
                "guest_rules": [asdict(rule) for rule in constraints.guest_rules],
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
            "search_notes": {
                "left_out_unknown_allergens": [],
                "left_out_details": [],
                "semantic_paused": False,
            },
            "planner_opening": None,
            "shortlist_ids": [],
        }

    def feasibility_step(state: ChatState) -> Command:
        """No model call: when the request cannot be met, skip the planner and say why."""
        spec = PlanSpec.model_validate(state["spec"])
        with deps.session_factory() as session:
            ctx = load_planning_context(session, meal_constraints(state))
            found: list[Shortfall] = check_feasibility(session, ctx, spec)
            # The planner never searches on a stop, so the reply names the dishes left out
            # for unknown allergens among the types the request needs.
            notes = (
                _merge_notes(
                    state.get("search_notes"),
                    [],
                    False,
                    unknown_allergen_exclusions(
                        session, ctx, dish_types=needed_dish_types(spec)
                    ),
                )
                if found
                else None
            )
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
        return Command(update={"shortfalls": dumped, "search_notes": notes}, goto="present_plan")

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
            "Every count is exact unless it says at least, at most, or a range.",
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
        codes = diner_codes(meal_constraints(state), current_table())
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
        constraints = meal_constraints(state)
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
                summary = request_summary(
                    state, meal_preferences(session, ctx, current_table())
                )
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
                        notes, [], found.semantic_paused, found.left_out_unknown_allergens
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
            ctx = load_planning_context(session, meal_constraints(state))
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
        constraints = meal_constraints(state)
        with deps.session_factory() as session:
            base = card_base(state, session)
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
            "servings_notes": [],
            **base,
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
            note = EARLY_STOP_NOTE_ZH if is_zh(locale) else EARLY_STOP_NOTE_EN
        elif shown is None:
            # Design 6.3: a plan breaking H2 or H5 is never shown; the note is fixed text.
            plan["unmet"] = [
                {"code": code, "recipe_id": None, "text": no_plan_reason(code, locale)}
                for code in state.get("blocked_codes") or []
            ]
            plan["next_actions"] = next_actions(spec, [], list(state.get("blocked_codes") or []))
            if plan["next_actions"]:
                note = NO_PLAN_NOTE_ZH if is_zh(locale) else NO_PLAN_NOTE_EN
            else:
                note = NO_PLAN_NOTE_BARE_ZH if is_zh(locale) else NO_PLAN_NOTE_BARE_EN
        else:
            with deps.session_factory() as session:
                ctx = load_planning_context(session, constraints)
                plan = build_dishes_card(
                    session,
                    ctx,
                    recipe_ids=shown["proposal"]["recipe_ids"],
                    validation=shown["validation"],
                    base=base,
                    locale=locale,
                )
            note = shown["proposal"]["note"]
            if not shown["validation"]["passed"]:
                # The fixed closest note always leads; the model's own note may follow it.
                closest = CLOSEST_NOTE_ZH if is_zh(locale) else CLOSEST_NOTE_EN
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
        reply_id = str(uuid.uuid4())
        return {
            "current_plan": plan,
            "plan_versions": [*(state.get("plan_versions") or []), plan],
            "reply_plans": {**(state.get("reply_plans") or {}), reply_id: plan},
            "messages": [AIMessage(note, id=reply_id)],
        }

    def with_labels(node: Callable, restore: bool = True) -> Callable:
        """Run `node` with the conversation's label table as the current label context, so
        the client hooks replace saved nicknames in every model call the node makes. The
        scope is set inside the node's own run, in the thread that makes its model calls;
        LangGraph copies the context into any thread it starts from there. The node's update
        gets `diner_labels` when the table changed (it only grows). A thread saved before
        the table existed starts one with the user as "Diner 1"."""
        takes_config = "config" in inspect.signature(node).parameters

        def run(state: ChatState, config: RunnableConfig):
            with deps.session_factory() as session:
                self_id = session.scalar(select(Diner.id).where(Diner.is_self))
                table = LabelTable.from_state(state.get("diner_labels"), self_id)
                ctx = load_label_context(session, table, state.get("locale", "en"), restore)
            with labels_scope(ctx):
                result = node(state, config) if takes_config else node(state)
            labels = table.to_state()
            if labels == state.get("diner_labels"):
                return result
            if isinstance(result, Command):
                update = {**(result.update or {}), "diner_labels": labels}
                return dataclasses.replace(result, update=update)
            return {**(result or {}), "diner_labels": labels}

        return run

    builder = StateGraph(ChatState)
    builder.add_node("route_intent", with_labels(route_intent))
    builder.add_node("other", with_labels(other))
    # The parse output keeps labels as labels (friend_rules codes are resolved by code), so
    # no nickname reaches the state's request through the post-hook.
    builder.add_node("parse_request", with_labels(parse_request, restore=False))
    builder.add_node("ask_style", with_labels(ask_style))
    builder.add_node("ask_question", with_labels(ask_question))
    builder.add_node("select_diners", with_labels(select_diners))
    builder.add_node("unclear", with_labels(unclear))
    builder.add_node("load_context", with_labels(load_context))
    builder.add_node("planner", with_labels(planner))
    builder.add_node(
        "check_feasibility",
        with_labels(feasibility_step),
        destinations=("planner", "present_plan"),
    )
    builder.add_node(
        "validate_plan",
        with_labels(check_plan),
        destinations=("check_feasibility", "present_plan"),
    )
    builder.add_node("present_plan", with_labels(present_plan))
    builder.add_edge(START, "route_intent")
    gates = ["ask_style", "ask_question", "select_diners", "load_context", "unclear"]
    builder.add_conditional_edges("route_intent", choose, ["parse_request", "other", *gates])
    builder.add_conditional_edges("parse_request", after_parse, gates)
    builder.add_conditional_edges("ask_style", after_parse, gates)
    builder.add_edge("ask_question", "parse_request")
    builder.add_edge("select_diners", "load_context")
    builder.add_edge("load_context", "check_feasibility")
    builder.add_edge("planner", "validate_plan")
    builder.add_edge("present_plan", END)
    builder.add_edge("other", END)
    builder.add_edge("unclear", END)
    return builder.compile(checkpointer=checkpointer)
