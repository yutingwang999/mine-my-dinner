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

from minemydinner.agent.actions import action_of, apply_action, on_plan_counts, spec_changes
from minemydinner.agent.card_text import (
    NEXT_ACTION_ORDER,
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
from minemydinner.agent.edits import locked_ids, revised_card
from minemydinner.agent.middleware import dump_message
from minemydinner.agent.plan_card import (
    build_dishes_card,
    card_base,
    meal_constraints,
    with_meal_avoid,
)
from minemydinner.agent.planner import (
    REVISE_TOOLS_VERSION,
    TOOLS_VERSION,
    PlanProposal,
    build_planner,
    build_reviser,
)
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
from minemydinner.agent.revise_step import (
    WISH_UNMET,
    attempt_spec,
    card_search_notes,
    carried_overrides,
    count_fix_hint,
    deleted_note,
    dish_facts,
    empty_plan_question,
    failed_note,
    from_version,
    kept_locked,
    must_unmet_names,
    open_conflict_ids,
    pairing_detail,
    replaceable_dishes,
    reply_text,
    revision_actions,
    revision_defaults,
    revision_opening,
    split_violations,
    unchanged_card,
    unchanged_note,
    unmet_entries,
    untouched_locked,
    wish_unmet_actions,
    without_deleted,
)
from minemydinner.agent.revision import (
    RevisionProblem,
    RevisionProposal,
    asks_back,
    changed_text,
    check_revision,
    counts_changed,
    revised_ids,
    revision_meal_avoid,
    revision_spec,
)
from minemydinner.agent.state import ChatState
from minemydinner.llm.client import map_strings
from minemydinner.models import Diner, Recipe
from minemydinner.planning.composition import composition_gap, count_categories, side_ranges
from minemydinner.planning.context import MEAL_KEY, diner_codes, load_planning_context
from minemydinner.planning.feasibility import Shortfall, check_feasibility, needed_dish_types
from minemydinner.planning.inventory import meal_preferences
from minemydinner.planning.search import ExcludedRecipe, unknown_allergen_exclusions
from minemydinner.planning.shortlist import (
    ShortlistEntry,
    build_shortlist_result,
    fallback_ids,
)
from minemydinner.planning.stock import compute_missing
from minemydinner.planning.tools import PlannerDeps
from minemydinner.planning.validate import Finding, PlanSpec, ValidationResult, validate_plan

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
# A new plan whose taste wish the planner could not meet (plan 3b-1b spec 5.6); {wish} is the
# request's query, the "The user feels like" line of the planner's opening.
WISH_UNMET_NOTE_ZH = "菜谱库里没有符合“{wish}”的菜，先按其他要求给你配了这份。"
WISH_UNMET_NOTE_EN = "Nothing in your library fits “{wish}”, so this plan follows your other requests."
# The note of a plan code picked from the shortlist when the planner gave no answer and the
# request has no wish.
FALLBACK_NOTE_ZH = "这份方案是按你的要求从菜谱库里直接选出的。"
FALLBACK_NOTE_EN = "This plan was picked from your library to fit your request."
# No plan because every attempt came back with no dishes (nothing was blocked by a rule).
EMPTY_PLAN_NOTE_ZH = "这次没能为这个要求配出菜，所以没有出方案。可以再发一次请求重试。"
EMPTY_PLAN_NOTE_EN = (
    "I could not put any dishes together for this request, so no plan is shown. "
    "Send the request again to try once more."
)


# What a message asks for (plan 3b-1 spec 5.1). A new intent is added here and in the route
# prompt only. "revise" and "unsure" need a current plan; without one code reads them as "plan".
IntentName = Literal["plan", "revise", "unsure", "other"]


class Intent(BaseModel):
    intent: IntentName


# The answers of the choice card shown for "unsure"; anything else continues as "revise".
REVISE_OR_PLAN = ("revise", "plan")

_PLAN_LINE_CATEGORIES = (
    ("soups", "soup", "soups"),
    ("staples", "staple food", "staple foods"),
    ("desserts", "dessert", "desserts"),
    ("snacks", "snack", "snacks"),
)


# Marks the plan line as the app's, so the parse does not take it for the user's newest words.
PLAN_LINE_PREFIX = "[App note, not written by the user] Current plan: "


def current_plan_line(plan: dict) -> str:
    """The current plan as one fixed line for the parse input of a revise turn (spec 5.3), in
    the parse prompt's terms: "dishes" are the meat plus vegetable dishes. Titles only; never
    a diner."""
    if not plan["recipes"]:
        return f"{PLAN_LINE_PREFIX}no dishes"
    counts = count_categories(r["dish_type"] for r in plan["recipes"])
    parts = []
    if counts["dishes"]:
        noun = "dish" if counts["dishes"] == 1 else "dishes"
        parts.append(
            f"{counts['dishes']} {noun} ({counts['meat_dishes']} meat, "
            f"{counts['vegetable_dishes']} vegetable)"
        )
    for name, one, many in _PLAN_LINE_CATEGORIES:
        if counts[name]:
            parts.append(f"{counts[name]} {one if counts[name] == 1 else many}")
    titles = ", ".join(r["title"] for r in plan["recipes"])
    # A dish type outside the categories is listed by title only.
    return f"{PLAN_LINE_PREFIX}{', '.join(parts) or 'other dishes'}: {titles}"


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


def _is_action_message(state: ChatState) -> bool:
    """True when the latest message is a fixed next-action button message and the thread has a
    request. Such a turn never makes a route call (a no-plan reply counts as a current plan, so
    a route would send it to revise). On a card that keeps its request (`_button_card`) code
    changes that request and nothing is parsed (plan 3b-1b spec 5.5); on an older card the
    parse relaxes the request (3b-1). As a thread's first message the text takes the normal
    path."""
    last = state["messages"][-1]
    return (
        state.get("request") is not None
        and isinstance(last, HumanMessage)
        and isinstance(last.content, str)
        and action_of(last.content) is not None
    )


def _button_card(state: ChatState) -> dict | None:
    """The card that offered the button of this turn: the card of the newest assistant reply,
    when it keeps the request it was built from. None when that reply has no card or the card
    was saved before cards kept their request (or was replaced by a hand edit)."""
    for message in reversed(state["messages"][:-1]):
        if isinstance(message, AIMessage):
            card = (state.get("reply_plans") or {}).get(message.id or "")
            return card if card is not None and card.get("request") is not None else None
    return None


def _button_turn(state: ChatState) -> bool:
    """This turn is a button whose request code changed (route_intent set `button`)."""
    return bool(state.get("button")) and _is_action_message(state)


def _reply_card(card: dict, request: dict | None, plan_kept: bool = False) -> dict:
    """A reply card as `reply_plans` keeps it: with the request of the turn it was built from,
    so a button on it changes that request (spec 5.5), and `plan_kept` on a revision card that
    left the plan as it was (failed, unclear, nothing fits), so the button revises again."""
    shown = {**card, "request": request}
    if plan_kept:
        shown["plan_kept"] = True
    return shown


def _revises(state: ChatState) -> bool:
    """This turn changes the current plan (intent "revise" with a plan to change)."""
    return state.get("intent") == "revise" and state.get("current_plan") is not None


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
    if code == "H2" and detail.get("diner_key") == MEAL_KEY:
        # This meal's one-meal avoidance (spec 5.4), never worded as a diner's limit.
        subject = detail.get("line") or detail.get("text") or "an ingredient"
        return (
            f"Recipe {recipe_id}: {subject} is left out of this meal (the user's wish for "
            "this meal only), so the dish cannot be used."
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


def _notes_from_searches(notes: dict | None, messages: list) -> dict | None:
    """Search notes with what each search_recipes result in `messages` left out added."""
    for message in messages:
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
    return notes


def _empty_search_notes() -> dict:
    return {"left_out_unknown_allergens": [], "left_out_details": [], "semantic_paused": False}


def build_graph(deps: GraphDeps, checkpointer) -> CompiledStateGraph:
    planner_agent: CompiledStateGraph | None = None
    reviser_agent: CompiledStateGraph | None = None

    def get_planner() -> CompiledStateGraph:
        # Built on first use, so conversations that never plan never build a model.
        nonlocal planner_agent
        if planner_agent is None:
            planner_agent = build_planner(deps)
        return planner_agent

    def get_reviser() -> CompiledStateGraph:
        nonlocal reviser_agent
        if reviser_agent is None:
            reviser_agent = build_reviser(deps)
        return reviser_agent

    def route_intent(state: ChatState) -> dict:
        # Each new message is a new request, which may be asked about once. The thread's
        # request is kept before this turn's parse replaces it: a revision compares the two.
        fresh = {
            "asked_question": False,
            "previous_request": state.get("request"),
            "button": None,
        }
        if _reuses_parse(state):
            # "Plan again": the previous parsed request stands as this turn's parse, with
            # no model call; choose sends the turn on as after a parse.
            return {**fresh, "intent": "plan", "model_calls": [{"kind": "parse_reused"}]}
        if _is_action_message(state):
            card = _button_card(state)
            if card is None:
                # A card without its request (3b-1): no route call; choose parses the message.
                return {**fresh, "intent": "plan", "model_calls": []}
            # Spec 5.5: the card's request with the button's change, in code. On a revision
            # card that kept the plan the turn revises the current plan again; on any other
            # card it plans from scratch.
            action = action_of(state["messages"][-1].content)
            revises = bool(card.get("plan_kept")) and state.get("current_plan") is not None
            base = card["request"]
            if revises and not base.get("counts_stated_now"):
                # The card's turn stated no count: the counts are the plan the user sees
                # (3b-1 spec 5.2), so "one fewer" is one fewer than the dishes shown. A turn
                # that stated counts (a failed "再加一道") keeps them.
                base = on_plan_counts(base, state["current_plan"])
            return {
                **fresh,
                "intent": "revise" if revises else "plan",
                "request": apply_action(base, action, card.get("spec")),
                "button": action,
                "model_calls": [],
            }
        messages = [SystemMessage(load_route_prompt()), *state["messages"]]
        result = deps.client.invoke_structured(deps.task, messages, Intent)
        intent = result.parsed.intent
        if intent in ("revise", "unsure") and state.get("current_plan") is None:
            # Nothing to revise yet (spec 5.1). A no-plan or early-stop reply is a plan.
            intent = "plan"
        return {
            **fresh,
            "intent": intent,
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
        # A revise turn is parsed like a new plan; after_parse then sends it to the revise
        # step. "unsure" asks first with the choice card.
        if state["intent"] == "unsure":
            return "ask_revise_or_plan"
        if state["intent"] not in ("plan", "revise"):
            return "other"
        if _button_turn(state):
            # The request is complete: no parse and no question (spec 5.5). Who eats is the
            # card's, so the diner card shows only when a settled friend was deleted since.
            if _revises(state):
                return "start_revision"
            return "select_diners" if friend_deleted_since(state) else "load_context"
        return after_parse(state) if _reuses_parse(state) else "parse_request"

    def ask_revise_or_plan(state: ChatState) -> dict:
        """The choice card (spec 5.1): change the current plan or plan a new meal. Shown once
        per message, before the parse. A skipped card (None or other text) revises: it keeps
        the user's work, and the reply shows what changed."""
        last = state["messages"][-1]
        message = last.content if isinstance(last.content, str) else ""
        asked = {"kind": "revise_or_plan", "message": message}
        answer = interrupt(asked)
        choice = answer if answer in REVISE_OR_PLAN else None
        recorded = {**asked, "choice": choice}
        return {
            "intent": choice or "revise",
            "questions": [*(state.get("questions") or []), _question(state, recorded)],
        }

    def parse_request(state: ChatState) -> dict:
        # The parse also reads the assistant's questions, so a short answer such as "两个"
        # sits next to the question it answers (spec 5.6). The route input is unchanged.
        conversation = parse_messages(
            state["messages"], state.get("questions") or [], state.get("locale", "en")
        )
        if _revises(state):
            # A relative count ("再加一道") needs the plan's size (spec 5.3). The line is
            # marked as the app's, not the user's; parse_v6 counts from it.
            conversation.append(HumanMessage(current_plan_line(state["current_plan"])))
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
        if _revises(state):
            # A revision never asks the ask-first question (spec 5.3): unstated fields come
            # from the current plan. A change in who eats plans anew after the diner card
            # (spec 5.1); load_context then marks the turn as a plan.
            # That fallback also skips the ask-first question and plans from this parse.
            return "select_diners" if diner_card_needed(state, parsed) else "start_revision"
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
        constraints_state = {
            "diner_ids": list(constraints.diner_ids),
            "guest_rules": [asdict(rule) for rule in constraints.guest_rules],
            "unconfirmed_diners": constraints.unconfirmed_diners,
            "headcount": headcount,
        }
        with deps.session_factory() as session:
            # A one-meal avoidance is this meal's own rule, never a person's (spec 5.4).
            constraints_state = with_meal_avoid(session, constraints_state, parsed.meal_avoid)
        request = {
            "messages": [deps.client.prepare_text(t) for t in _current_request(state["messages"])],
            "parsed": map_strings(parsed.model_dump(), deps.client.prepare_text),
            # The spec carries the named ingredients, which may hold a replaced name.
            "spec": map_strings(spec.model_dump(), deps.client.prepare_text),
            "headcount": headcount,
            "locale": state.get("locale", "en"),
        }
        if _button_turn(state):
            # The request came from code, not from a parse (spec 5.5).
            request["button"] = state["button"]
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
            # Planning from scratch, also for a revise turn that changed who eats.
            "intent": "plan",
            "model_calls": [],
            "spec": spec.model_dump(),
            "constraints": constraints_state,
            "defaults_used": defaults_used,
            "run_id": run_id,
            "retries": 0,
            "proposal": None,
            "validation": None,
            "first_passed": None,
            "best": None,
            "blocked_codes": [],
            "blocked_h2_keys": [],
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

    def request_summary(
        state: ChatState, preferences: list[str], heading: str = "Plan this meal."
    ) -> str:
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
        return f"{heading}\n" + "\n".join(lines)

    def retry_message(state: ChatState, session, ctx) -> str:
        """The violations of the previous attempt, then a short summary of it."""
        ids = state["proposal"]["recipe_ids"]
        if state["proposal"].get("fallback"):
            # The planner gave no answer; code picked these dishes (spec 5.6).
            header = (
                f"You gave no final answer, so code picked {ids} from the shortlist, and that "
                "failed these checks. Answer with a plan that fixes each one:"
            )
        else:
            header = (
                f"Your proposal {ids} failed these checks. Propose a new plan that fixes each one:"
            )
        return retry_text(state, session, ctx, header, ids, [])

    def retry_text(
        state: ChatState, session, ctx, header: str, ids: list[int], problems: list[str]
    ) -> str:
        """`header`, then `problems` and the previous attempt's violations, then a short
        summary of that attempt (`ids` are the dishes it proposed)."""
        codes = diner_codes(meal_constraints(state), current_table())
        problems = [
            *problems,
            *(_violation_for_model(v, codes) for v in state["validation"]["violations"]),
        ]
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
            f"{header}\n"
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

    def build_opening(
        state: ChatState, session, ctx, spec: PlanSpec, vectors: dict, heading: str
    ) -> tuple[str, dict | None, list[int]]:
        """The shortlist (when used), then the request summary under `heading`. Returns the
        text, the search notes with the shortlist's left-out dishes, and the shortlist ids."""
        notes = state.get("search_notes")
        summary = request_summary(
            state, meal_preferences(session, ctx, current_table()), heading
        )
        entries = None
        complete: frozenset[str] = frozenset()
        if deps.use_shortlist:
            parsed = ParsedRequest.model_validate(state["request"])
            found = build_shortlist_result(session, deps.client, ctx, spec, parsed, vectors)
            entries = found.entries
            complete = found.complete
            notes = _merge_notes(
                notes, [], found.semantic_paused, found.left_out_unknown_allergens
            )
        ids = [entry.id for items in (entries or {}).values() for entry in items]
        return planner_opening(entries, summary, complete), notes, ids

    def planner_context(state: ChatState, spec: PlanSpec, vectors: dict) -> PlannerDeps:
        """The tools' runtime context for one attempt of the planner or the revise step."""
        return PlannerDeps(
            deps.session_factory,
            deps.client,
            meal_constraints(state),
            spec.mode,
            query_vectors=vectors,
            must_use=list(spec.must_use),
            all_use=list(spec.all_use),
            prefer_use=list(spec.prefer_use),
            time_limit_min=spec.time_limit_min,
            exclude_recent_days=state["request"].get("exclude_recent_days"),
            query=state["request"].get("query"),
        )

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
                opening, notes, shortlist_ids = build_opening(
                    state, session, ctx, spec, vectors, "Plan this meal."
                )
                update["planner_opening"] = opening
                update["shortlist_ids"] = shortlist_ids
            messages = [HumanMessage(opening)]
            validation = state.get("validation")
            if validation is not None and not validation["passed"]:
                messages.append(HumanMessage(retry_message(state, session, ctx)))
        out = get_planner().invoke(
            {"messages": messages, "run_id": state["run_id"]},
            context=planner_context(state, spec, vectors),
        )
        notes = _notes_from_searches(notes, out["messages"])
        proposal = out.get("structured_response")
        if proposal is not None:
            dumped = proposal.model_dump()
        else:
            # A run cut off by the call limits or the token budget has no answer: code picks
            # a plan from the shortlist instead, checked like any attempt (spec 5.6).
            dumped = fallback_proposal(state, spec, vectors)
        return {
            **update,
            "proposal": dumped,
            "search_notes": _merge_notes(notes, [], False),
        }

    def fallback_proposal(state: ChatState, spec: PlanSpec, vectors: dict) -> dict:
        """The proposal of an attempt with no answer: the shortlist's usable dishes that fill
        the counts (`fallback_ids`). With a wish in the request, the planner never confirmed
        it, so it counts as not met; otherwise the note is fixed text. `fallback` marks it,
        so the retry and the record tell it from the planner's own answer."""
        parsed = ParsedRequest.model_validate(state["request"])
        with deps.session_factory() as session:
            ctx = load_planning_context(session, meal_constraints(state))
            found = build_shortlist_result(session, deps.client, ctx, spec, parsed, vectors)
        wish = bool(parsed.query)
        zh = is_zh(state.get("locale", "en"))
        proposal = PlanProposal(
            recipe_ids=fallback_ids(found.entries, spec),
            note="" if wish else (FALLBACK_NOTE_ZH if zh else FALLBACK_NOTE_EN),
            wish_met=False if wish else None,
        )
        return {**proposal.model_dump(), "fallback": True}

    def check_plan(state: ChatState) -> Command:
        spec = PlanSpec.model_validate(state["spec"])
        ids = state["proposal"]["recipe_ids"]
        with deps.session_factory() as session:
            ctx = load_planning_context(session, meal_constraints(state))
            result: ValidationResult = validate_plan(session, ctx, spec, ids)
        dumped = result.model_dump(mode="json")
        event = {
            "kind": "validation",
            "attempt": state["retries"],
            "recipe_ids": ids,
            "result": map_strings(dumped, deps.client.prepare_text),
        }
        if state["proposal"].get("fallback"):
            # Code picked these dishes; the planner gave no answer (spec 5.6).
            event["fallback"] = True
        deps.recorder.add_event(state["run_id"], event)
        update: dict[str, Any] = {"validation": dumped}
        if state.get("first_passed") is None:
            # A passing code fallback is not the planner's pass (spec 5.6), so evaluation never
            # counts an attempt with no answer as a first-pass success.
            update["first_passed"] = result.passed and not state["proposal"].get("fallback")
        blocked = [code for code in NEVER_SHOWN if any(v.code == code for v in result.violations)]
        if blocked:
            seen = state.get("blocked_codes") or []
            update["blocked_codes"] = [c for c in NEVER_SHOWN if c in seen or c in blocked]
            # Whose rules the H2 findings broke, so a meal-wish-only block is worded as the
            # meal's wish (spec 5.4), never as a diner's rule.
            keys = {
                v.detail.get("diner_key", "*") for v in result.violations if v.code == "H2"
            }
            update["blocked_h2_keys"] = sorted(set(state.get("blocked_h2_keys") or []) | keys)
        elif ids:
            # An empty proposal (no answer and nothing to fall back on) is never the closest
            # plan: an empty card would give "0 dishes" as its reason (spec 5.6).
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
            "locked": [],
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
            keys = state.get("blocked_h2_keys") or []
            meal = (
                state["constraints"].get("meal_avoid") or []
                if keys and all(key == MEAL_KEY for key in keys)
                else []
            )
            plan["unmet"] = [
                {"code": code, "recipe_id": None, "text": no_plan_reason(code, locale, meal)}
                for code in state.get("blocked_codes") or []
            ]
            plan["next_actions"] = next_actions(spec, [], list(state.get("blocked_codes") or []))
            if not plan["unmet"]:
                # No attempt was blocked: every attempt was empty (no dish was put together),
                # so there is no reason to list (spec 5.6: never "0 dishes").
                note = EMPTY_PLAN_NOTE_ZH if is_zh(locale) else EMPTY_PLAN_NOTE_EN
            elif plan["next_actions"]:
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
                plan["locked"] = []
            parts = [shown["proposal"]["note"]]
            if not shown["validation"]["passed"]:
                # The fixed closest note always leads; the model's own note may follow it.
                parts.insert(0, CLOSEST_NOTE_ZH if is_zh(locale) else CLOSEST_NOTE_EN)
            wish = state["request"].get("query")
            if wish and shown["proposal"].get("wish_met") is False:
                # The plan follows the other conditions; the unmet wish is always said
                # first (spec 5.6), with the next actions that fit it.
                template = WISH_UNMET_NOTE_ZH if is_zh(locale) else WISH_UNMET_NOTE_EN
                parts.insert(0, template.format(wish=wish))
                plan["unmet"] = [
                    *plan["unmet"],
                    *unmet_entries([{"code": WISH_UNMET, "recipe_id": None}], [], {}, locale),
                ]
                chosen = {*plan["next_actions"], *wish_unmet_actions(spec)}
                plan["next_actions"] = [a for a in NEXT_ACTION_ORDER if a in chosen]
            note = "\n\n".join(part for part in parts if part)
        # The card keeps the request it was built from, for a button on it (spec 5.5).
        plan = _reply_card(plan, state["request"])
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
            "plan_request": state["request"],
            "plan_versions": [*(state.get("plan_versions") or []), plan],
            "reply_plans": {**(state.get("reply_plans") or {}), reply_id: plan},
            "messages": [AIMessage(note, id=reply_id)],
        }

    # ---- the revise step (plan 3b-1 spec 5.2-5.6) ----

    def start_revision(state: ChatState, config: RunnableConfig) -> dict:
        """Start a planning run that changes the current plan: the revision's spec (the plan's
        counts, the message's changes on top) and the run's record, marked with the version
        it starts from. Who eats is the current plan's (a change in who eats plans anew)."""
        index = from_version(state["current_plan"], state.get("plan_versions") or [])
        shown_ids = [r["id"] for r in state["current_plan"]["recipes"]]
        with deps.session_factory() as session:
            existing = set(session.scalars(select(Recipe.id).where(Recipe.id.in_(shown_ids))))
        deleted = [i for i in shown_ids if i not in existing]
        # A dish deleted from the library since the plan was shown leaves the revision.
        plan = without_deleted(state["current_plan"], deleted)
        parsed = ParsedRequest.model_validate(state["request"])
        # The baseline is the request the plan was built from, not the thread's last parse: a
        # turn that left the plan as it was (failed, unclear, nothing fits) still replaced
        # `request`, and its wishes are not in the plan.
        baseline = state.get("plan_request") or state.get("previous_request")
        spec = revision_spec(plan, baseline, state["request"])
        button = state["button"] if _button_turn(state) else None
        if button is not None:
            # A cleared time limit or emptied named list is no statement to revision_spec.
            spec = spec.model_copy(update=spec_changes(state["request"], button))
        # This meal's one-meal avoidance joins the revision like the other conditions (spec
        # 5.4); the plan's dishes that break it are named by it (`avoid_ids`).
        meal_avoid = revision_meal_avoid(plan, baseline, state["request"])
        shown = [r["id"] for r in plan["recipes"]]
        with deps.session_factory() as session:
            constraints = with_meal_avoid(session, state["constraints"], meal_avoid)
            ctx = load_planning_context(session, meal_constraints({"constraints": constraints}))
            found = validate_plan(
                session,
                ctx,
                PlanSpec.model_validate(plan["spec"]),
                shown,
                carried_overrides(plan, shown),
            )
        avoid_ids = list(
            dict.fromkeys(
                v.recipe_id
                for v in found.violations
                if v.code == "H2" and v.detail.get("diner_key") == MEAL_KEY
            )
        )
        defaults_used = revision_defaults(plan.get("defaults_used") or [], plan["spec"], spec)
        texts = _current_request(state["messages"])
        settled_data = state.get("settled_diners")
        settled = SettledDiners.from_state(settled_data) if settled_data else None
        request = {
            "messages": [deps.client.prepare_text(t) for t in texts],
            "parsed": map_strings(parsed.model_dump(), deps.client.prepare_text),
            "spec": map_strings(spec.model_dump(), deps.client.prepare_text),
            "headcount": state["constraints"]["headcount"],
            "locale": state.get("locale", "en"),
            "revision": {"from_version": index},
        }
        if button is not None:
            request["button"] = button
        run_id = deps.recorder.start(
            thread_id=config["configurable"]["thread_id"],
            request=request,
            defaults_used=defaults_used,
            prompt_version=PROMPT_VERSION,
            tools_version=REVISE_TOOLS_VERSION,
            has_temporary_guests=bool(parsed.guests or (settled and settled.guest_names)),
        )
        for stale in [r for r in deps.run_query_vectors if r != run_id]:
            del deps.run_query_vectors[stale]
        for event in state.get("model_calls") or []:
            deps.recorder.add_event(run_id, event)
        return {
            "model_calls": [],
            "spec": spec.model_dump(),
            "constraints": constraints,
            "defaults_used": defaults_used,
            "run_id": run_id,
            "retries": 0,
            "proposal": None,
            "validation": None,
            "first_passed": None,
            "best": None,
            "blocked_codes": [],
            "blocked_h2_keys": [],
            "shortfalls": [],
            "search_notes": _empty_search_notes(),
            "planner_opening": None,
            "shortlist_ids": [],
            "revision": {
                "from_version": index,
                "message": "\n".join(texts),
                "recipe_ids": shown,
                "problems": [],
                "deleted": deleted,
                "avoid_ids": avoid_ids,
            },
        }

    def revision_plan(state: ChatState) -> dict:
        """The plan the revision changes: the current plan without its deleted dishes."""
        return without_deleted(
            state["current_plan"], (state.get("revision") or {}).get("deleted") or []
        )

    def reviser(state: ChatState) -> dict:
        try:
            return revise_attempt(state)
        except BaseException:
            deps.run_query_vectors.pop(state["run_id"], None)
            raise

    def revise_retry_message(state: ChatState, session, ctx) -> str:
        """The previous attempt's revision problems and violations, in the planner's format."""
        data = state["proposal"]
        if data is None:
            header = "You gave no revision. Answer with the RevisionProposal tool:"
        else:
            removed = [r["id"] for r in data["remove"]]
            header = (
                f"Your revision (remove {removed}, add {data['add']}) failed these checks. "
                "Propose a new revision of the current plan that fixes each one:"
            )
        problems = [p["text"] for p in state["revision"]["problems"]]
        ids = state["revision"]["recipe_ids"]
        text = retry_text(state, session, ctx, header, ids, problems)
        # Which categories are off and how to fix them, after the H4 line (task 8b): the
        # reviser swapped a vegetable dish for a meat dish and then gave up. Plan 3b-1b
        # spec 5.2: the hint names the dishes of the category that is over. Without a stated
        # count the attempt is checked against its own counts, so the type pairing (R7)
        # stands in for H4 and gets the same hint.
        plan = revision_plan(state)
        replaceable = replaceable_dishes(plan)
        h4 = [v for v in state["validation"]["violations"] if v["code"] == "H4"]
        codes = {p["code"] for p in state["revision"]["problems"]}
        if h4:
            text += "\n\nTo fix the counts: " + count_fix_hint(h4[0]["detail"], replaceable)
        elif "R7" in codes and data is not None:
            proposal = RevisionProposal.model_validate(data)
            new = [i for i in proposal.add if i not in {r["id"] for r in plan["recipes"]}]
            types: dict[int, str] = {}
            if new:
                rows = session.execute(
                    select(Recipe.id, Recipe.dish_type).where(Recipe.id.in_(new))
                ).all()
                types = {recipe_id: dish_type for recipe_id, dish_type in rows}
            detail = pairing_detail(plan, proposal, types)
            # Only a general wish may pick other dishes to replace; a named or scoped change
            # must keep its dishes, so it gets the same-type line alone.
            names = replaceable if proposal.target == "general" else {}
            text += "\n\nTo fix the dish types: " + count_fix_hint(detail, names)
        return text

    def revise_attempt(state: ChatState) -> dict:
        spec = PlanSpec.model_validate(state["spec"])
        vectors = deps.run_query_vectors.setdefault(state["run_id"], {})
        update: dict[str, Any] = {}
        notes = state.get("search_notes")
        opening = state.get("planner_opening")
        plan = revision_plan(state)
        with deps.session_factory() as session:
            ctx = load_planning_context(session, meal_constraints(state))
            if opening is None:
                base, notes, shortlist_ids = build_opening(
                    state, session, ctx, spec, vectors, "Revise this meal."
                )
                _, mains = dish_facts(session, [r["id"] for r in plan["recipes"]])
                opening = revision_opening(
                    base,
                    plan,
                    mains,
                    state["revision"]["message"],
                    state["revision"].get("avoid_ids") or [],
                )
                update["planner_opening"] = opening
                update["shortlist_ids"] = shortlist_ids
            messages = [HumanMessage(opening)]
            validation = state.get("validation")
            if validation is not None and not validation["passed"]:
                messages.append(HumanMessage(revise_retry_message(state, session, ctx)))
        out = get_reviser().invoke(
            {"messages": messages, "run_id": state["run_id"]},
            context=planner_context(state, spec, vectors),
        )
        # A run cut off by the call limits or the token budget has no answer; the check
        # then fails the attempt.
        answer = out.get("structured_response")
        notes = _notes_from_searches(notes, out["messages"])
        return {
            **update,
            "proposal": answer.model_dump() if answer is not None else None,
            "search_notes": _merge_notes(notes, [], False),
        }

    def empty_plan_update(
        state: ChatState, revision: dict, current_ids: list[int], problems: list
    ) -> dict:
        """The state update of an attempt whose change would leave no dish (R9): recorded as a
        failed validation, the plan's own dishes kept, and marked `empty` for the reply."""
        problem_dumps = [p.model_dump() for p in problems]
        deps.recorder.add_event(
            state["run_id"],
            {
                "kind": "validation",
                "attempt": state["retries"],
                "recipe_ids": [],
                "passed": False,
                "problems": problem_dumps,
                "result": None,
            },
        )
        update: dict[str, Any] = {
            "validation": {"passed": False, "violations": [], "allowed": [], "warnings": []},
            "revision": {
                **revision,
                "recipe_ids": current_ids,
                "problems": problem_dumps,
                "empty": True,
            },
        }
        if state.get("first_passed") is None:
            update["first_passed"] = False
        return update

    def check_revised(state: ChatState) -> Command:
        """The revision's own rules (revision.check_revision), then the hard checks with the
        carried overrides. H1, H3, H6 and H7 caused only by locked dishes are warnings."""
        plan = revision_plan(state)
        spec = PlanSpec.model_validate(state["spec"])
        revision = dict(state["revision"])
        current_ids = [r["id"] for r in plan["recipes"]]
        data = state["proposal"]
        proposal = RevisionProposal.model_validate(data) if data is not None else None
        if proposal is not None and asks_back(proposal):
            # Unclear: the plan stays and the model's question is the reply. Any other empty
            # change ("nothing fits") is checked like a change: an open conflict still fails
            # H2 and a changed count still fails H4.
            update: dict[str, Any] = {
                "revision": {**revision, "recipe_ids": current_ids, "problems": []}
            }
            if state.get("first_passed") is None:
                update["first_passed"] = False
            return Command(update=update, goto="present_revision")
        with deps.session_factory() as session:
            if proposal is None:
                ids = current_ids
                checked = spec
                problems = [
                    RevisionProblem(
                        code="R6", text="No revision was given: answer with RevisionProposal."
                    )
                ]
            else:
                ids = revised_ids(plan, proposal)
                # The added dishes need facts too: their dish type for the type pairing (R7)
                # and their words for a named replacement's quote. An id that is not in the
                # library has none and reads as an unknown type.
                facts, _ = dish_facts(session, [*current_ids, *proposal.add], plan=plan)
                baseline = state.get("plan_request") or state.get("previous_request")
                counts_stated = counts_changed(state["request"], baseline)
                # The plan's dishes that break this meal's avoidance (spec 5.4), found when
                # the revision started.
                avoid_ids = revision.get("avoid_ids") or []
                problems = check_revision(
                    plan,
                    proposal,
                    revision["message"],
                    facts,
                    spec=spec,
                    avoid_ids=avoid_ids,
                    counts_stated=counts_stated,
                )
                types = {i: f.dish_type for i, f in facts.items()}
                checked = attempt_spec(spec, plan, proposal, types, counts_stated)
            ctx = load_planning_context(session, meal_constraints(state))
            result = validate_plan(session, ctx, checked, ids, carried_overrides(plan, ids))
            missed_before: set[str] = set()
            if must_unmet_names(result.violations):
                # A "must use" name the current plan already missed (under its own spec) is
                # not this revision's doing.
                before = validate_plan(
                    session,
                    ctx,
                    PlanSpec.model_validate(plan["spec"]),
                    current_ids,
                    carried_overrides(plan, current_ids),
                )
                missed_before = must_unmet_names(before.violations)
        codes = {p.code for p in problems}
        if "R9" in codes and not codes & {"R1", "R2", "R6"}:
            # The change would leave no dish (spec 5.1): no retry; the plan stays and the
            # reply asks whether to plan a new meal. Drops the message does not justify (a
            # quote or scope problem, a malformed list) are retried as usual instead.
            return Command(
                update=empty_plan_update(state, revision, current_ids, problems),
                goto="present_revision",
            )
        locked = (locked_ids(plan) - open_conflict_ids(plan)) & set(ids)
        failing, allowed = split_violations(result.violations, locked, missed_before)
        passed = not problems and not failing
        dumped = result.model_dump(mode="json")
        problem_dumps = [p.model_dump() for p in problems]
        deps.recorder.add_event(
            state["run_id"],
            {
                "kind": "validation",
                "attempt": state["retries"],
                "recipe_ids": ids,
                "passed": passed,
                "problems": problem_dumps,
                "result": map_strings(dumped, deps.client.prepare_text),
            },
        )
        update = {
            "validation": {
                "passed": passed,
                "violations": [v.model_dump(mode="json") for v in failing],
                "allowed": [v.model_dump(mode="json") for v in allowed],
                "warnings": dumped["warnings"],
            },
            # The attempt's own spec: a passed revision's card carries it, so the new
            # version's counts match its dishes.
            "revision": {
                **revision,
                "recipe_ids": ids,
                "problems": problem_dumps,
                "spec": checked.model_dump(),
            },
        }
        if state.get("first_passed") is None:
            update["first_passed"] = passed
        if not passed and state["retries"] < MAX_RETRIES:
            update["retries"] = state["retries"] + 1
            return Command(update=update, goto="reviser")
        return Command(update=update, goto="present_revision")

    def present_revision(state: ChatState) -> dict:
        """A passed revision becomes a new model version and the current plan. Otherwise
        (every attempt failed, unclear, or nothing to change) the plan stays as it was: the
        reply's card shows the current dishes, not passed, and the state's spec goes back to
        the plan's, so a manual edit of that card checks against the plan's own request."""
        locale = state.get("locale", "en")
        plan = state["current_plan"]
        versions = list(state.get("plan_versions") or [])
        revision = state["revision"]
        deleted = revision.get("deleted") or []
        # What the revision worked on; an unchanged reply card still shows the plan as it was.
        live = without_deleted(plan, deleted)
        validation = state.get("validation")
        data = state["proposal"]
        proposal = RevisionProposal.model_validate(data) if data is not None else None
        changed = proposal is not None and bool(proposal.remove or proposal.add)
        passed = validation is not None and validation["passed"]
        update: dict[str, Any] = {}
        # Only a card in `reply_plans` follows the reply; the empty-plan question has none.
        shows_card = True
        with deps.session_factory() as session:
            if revision.get("empty"):
                # The change would leave no dish (spec 5.1): nothing changes, and the reply
                # is a plain question with no card.
                card = unchanged_card(plan, [], [])
                note = empty_plan_question(locale)
                shows_card = False
            elif proposal is not None and asks_back(proposal):
                card = unchanged_card(live, [], [])
                card["notes"] = deleted_note(plan, deleted)
                note = proposal.note.strip() or unchanged_note(locale)
            elif changed and passed:
                ctx = load_planning_context(session, meal_constraints(state))
                ids = revision["recipe_ids"]
                result = ValidationResult(
                    violations=[Finding(**v) for v in validation["allowed"]],
                    warnings=[Finding(**w) for w in validation["warnings"]],
                    passed=False,
                )
                # The attempt's own spec (its counts follow the change list), so the new
                # version's counts match its dishes.
                checked = revision.get("spec") or state["spec"]
                card = revised_card(
                    session,
                    ctx,
                    {**state, "spec": checked},
                    recipe_ids=ids,
                    result=result,
                    overrides=carried_overrides(live, ids),
                    locked=kept_locked(live, ids),
                    index=len(versions),
                    locale=locale,
                )
                card["revision"] = {"from_version": revision["from_version"]}
                card["notes"] = deleted_note(plan, deleted)
                note = reply_text(
                    live,
                    proposal,
                    card,
                    locale,
                    avoid_ids=revision.get("avoid_ids") or [],
                    meal_avoid=state["constraints"].get("meal_avoid") or [],
                )
                update.update(
                    current_plan=card,
                    plan_versions=[*versions, card],
                    plan_request=state["request"],
                    spec=checked,
                )
            elif passed:
                # The checks passed with no change: the model found nothing that fits the
                # wish. The plan stays; the reply says so with next actions (spec 5.4).
                unmet = unmet_entries(
                    [{"code": WISH_UNMET, "recipe_id": None}], [], {}, locale
                )
                actions = wish_unmet_actions(state["spec"])
                card = unchanged_card(live, unmet, actions)
                card["notes"] = deleted_note(plan, deleted)
                titles_now = {r["id"]: r["title"] for r in live["recipes"]}
                hand, kept = untouched_locked(live, proposal, titles_now)
                parts = [
                    failed_note(locale, True),
                    changed_text([], [], hand, locale, kept),
                    proposal.note.strip(),
                ]
                note = "\n\n".join(part for part in parts if part)
            else:
                titles = {r["id"]: r["title"] for r in plan["recipes"]}
                extra = [i for i in revision["recipe_ids"] if i not in titles]
                if extra:
                    titles.update(
                        session.execute(
                            select(Recipe.id, Recipe.title).where(Recipe.id.in_(extra))
                        ).all()
                    )
                violations = (validation or {}).get("violations", [])
                actions = revision_actions(
                    state["spec"],
                    [p["code"] for p in revision["problems"]],
                    [v["code"] for v in violations],
                )
                unmet = unmet_entries(
                    revision["problems"],
                    violations,
                    titles,
                    locale,
                    state["constraints"].get("meal_avoid") or [],
                )
                card = unchanged_card(live, unmet, actions)
                card["notes"] = deleted_note(plan, deleted)
                note = failed_note(locale, bool(actions))
        if "current_plan" not in update:
            # The plan stays, but `request` keeps this turn's parse (decided for plan 3b-1):
            # "plan again" then plans the latest request from scratch (spec 5.1). The meal's
            # one-meal avoidance goes back to the plan's own, like the spec.
            with deps.session_factory() as session:
                constraints = with_meal_avoid(
                    session, state["constraints"], plan.get("meal_avoid") or []
                )
            update.update(
                spec=plan["spec"],
                constraints=constraints,
                defaults_used=plan.get("defaults_used") or [],
                search_notes=card_search_notes(plan),
            )
        # The card keeps this turn's request, for a button on it (spec 5.5); a passed
        # revision's card is also the new current plan.
        kept = "current_plan" not in update
        card = _reply_card(card, state["request"], plan_kept=kept)
        if not kept:
            update.update(current_plan=card, plan_versions=[*versions, card])
        deps.run_query_vectors.pop(state["run_id"], None)
        deps.recorder.finish(
            state["run_id"],
            retries=state["retries"],
            first_passed=bool(state.get("first_passed")),
            # Only a revision that became the current plan passed.
            final_passed=not kept,
            final_plan=map_strings(card, deps.client.prepare_text),
        )
        reply_id = str(uuid.uuid4())
        reply_plans = dict(state.get("reply_plans") or {})
        if shows_card:
            reply_plans[reply_id] = card
        return {
            **update,
            "reply_plans": reply_plans,
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
    builder.add_node("ask_revise_or_plan", with_labels(ask_revise_or_plan))
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
    builder.add_node("start_revision", with_labels(start_revision))
    builder.add_node("reviser", with_labels(reviser))
    builder.add_node(
        "check_revised",
        with_labels(check_revised),
        destinations=("reviser", "present_revision"),
    )
    builder.add_node("present_revision", with_labels(present_revision))
    builder.add_edge(START, "route_intent")
    gates = [
        "ask_style",
        "ask_question",
        "select_diners",
        "load_context",
        "unclear",
        "start_revision",
    ]
    builder.add_conditional_edges(
        "route_intent", choose, ["parse_request", "other", "ask_revise_or_plan", *gates]
    )
    builder.add_edge("ask_revise_or_plan", "parse_request")
    builder.add_edge("start_revision", "reviser")
    builder.add_edge("reviser", "check_revised")
    builder.add_edge("present_revision", END)
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
