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
from pydantic import BaseModel, ValidationError
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
from minemydinner.agent.change_step import (
    UNFINISHED,
    button_reading,
    change_actions,
    change_unmet,
    conditions_changed,
    fallback_pick,
    needs_picker,
    result_spec,
    relax_split_spec,
    slot_results,
    unfinished_items,
    unfinished_reading,
    unused_must,
    withhold_count_drops,
    without_picks,
    without_unmet_wish_picks,
)
from minemydinner.agent.edits import locked_ids, revised_card
from minemydinner.agent.follow_up import (
    FollowUpReading,
    ReadConditions,
    reader_messages,
    request_after,
)
from minemydinner.agent.frame import (
    RULE_BREAKS,
    Frame,
    breaking_from,
    build_frame,
    fold,
    hidden_by,
    lookup_named,
)
from minemydinner.agent.middleware import dump_message
from minemydinner.agent.plan_card import (
    build_dishes_card,
    card_base,
    meal_constraints,
    with_meal_avoid,
)
from minemydinner.agent.picker import (
    PICK_MAX_ATTEMPTS,
    PickerAnswer,
    apply_picks,
    choice_slots,
    conditions_line,
    must_use_misses,
    picker_messages,
)
from minemydinner.agent.planner import (
    PICK_MODEL_CALL_LIMIT,
    PICK_TOOLS_VERSION,
    PICK_TURN_MODEL_CALLS,
    TOOLS_VERSION,
    PlanProposal,
    build_picker,
    build_planner,
)
from minemydinner.agent.questions import parse_messages
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import (
    COUNT_FIELDS,
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
    card_search_notes,
    carried_overrides,
    change_sentences,
    change_wish_unmet,
    conditions_only_note,
    current_plan_text,
    deleted_note,
    dish_facts,
    empty_plan_question,
    failed_note,
    from_version,
    kept_locked,
    locked_changed_text,
    must_unmet_names,
    open_conflict_ids,
    partial_text,
    reading_sentence,
    revision_defaults,
    rule_reason,
    unchanged_card,
    unchanged_note,
    unfinished_sentence,
    which_dish_question,
    wish_matched_nothing,
    wish_targets_locked,
    wish_unmet_actions,
    wish_unmet_entries,
    without_deleted,
)
from minemydinner.agent.revision import carry_unstated, revision_meal_avoid, revision_spec
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
from minemydinner.planning.validate import PlanSpec, ValidationResult, validate_plan

# Retries after the first proposal fails validation (design 7.3: at most N times).
MAX_RETRIES = 2
# The record's reason for a picked dish left out because it misses a must-use ingredient.
MUST_USE_MISS = "does not use the must-use ingredient"
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
# The problem the picker is told when a reply carried no answer (plan 3b-2 Task 11 fix 1).
PICK_NO_ANSWER = "You gave no answer. Answer with the PickerAnswer tool."
# No plan because every attempt came back with no dishes (nothing was blocked by a rule).
EMPTY_PLAN_NOTE_ZH = "这次没能为这个要求配出菜，所以没有出方案。可以再发一次请求重试。"
EMPTY_PLAN_NOTE_EN = (
    "I could not put any dishes together for this request, so no plan is shown. "
    "Send the request again to try once more."
)


# What a message asks for, as the route prompt (route_v6) answers it (plan 3b-1 spec 5.1). The
# route runs only when the thread has no plan with dishes (plan 3b-2): "revise" stays a
# follow-up of the current plan (no dishes) when there is one, else plans; "unsure" plans.
IntentName = Literal["plan", "revise", "unsure", "other"]


class Intent(BaseModel):
    intent: IntentName


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
    request. Such a turn never makes a route call (the button's change is known in code). On a
    card that keeps its request (`_button_card`) code
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


def _has_dishes(state: ChatState) -> bool:
    """The thread has a current plan with at least one dish. An early stop or a no-plan reply
    is a current plan with no dishes: there is nothing to read a follow-up against."""
    plan = state.get("current_plan")
    return bool(plan and plan.get("recipes"))


def _reads_follow_up(state: ChatState) -> bool:
    """Plan 3b-2 spec 5.1: a message on a thread whose plan has dishes is read once by the
    reader instead of the route. The fixed messages ("plan again" and the next-action
    buttons) keep their own paths and never reach the reader. A thread created with no
    message yet (the API's initial checkpoint) is routed."""
    if not state.get("messages"):
        return False
    last = state["messages"][-1]
    return (
        _has_dishes(state)
        and isinstance(last, HumanMessage)
        and isinstance(last.content, str)
        and not _reuses_parse(state)
        and not _is_action_message(state)
    )


# The intent each reading kind continues with: a change takes the change path (no parse); a
# people message is parsed as a follow-up (the plan line, `carry_unstated`) and always shows
# the diner card (`after_parse`); a new meal plans anew.
READING_INTENTS = {"change": "revise", "people": "revise", "new_meal": "plan", "other": "other"}

# The count fields of a request, as the reader's conditions name them.
_READER_COUNTS = (
    "dishes",
    "meat_dishes",
    "vegetable_dishes",
    "soups",
    "staples",
    "desserts",
    "snacks",
)


def current_meal_avoid(plan: dict, request: dict) -> list[str]:
    """This meal's one-meal avoidance as the plan stands: the plan card's own list (it is the
    one a revision keeps), else the request's for a card saved before cards carried it."""
    if "meal_avoid" in plan:
        return list(plan.get("meal_avoid") or [])
    return list(request.get("meal_avoid") or [])


def reader_conditions(
    request: dict, plan: dict, headcount: int | None, meal_avoid: list[str]
) -> str:
    """The meal's current conditions for the reader (spec 5.3): the counts the user stated,
    the mode, the time limit, the named ingredients, this meal's one-meal avoidance and the
    taste wish. A value the request leaves unstated falls back to the plan's spec."""
    spec = plan.get("spec") or {}
    counts = []
    for name in _READER_COUNTS:
        if request.get(name) is None:
            continue
        bound = request.get(f"{name.split('_')[0]}_bound") if name.endswith("_dishes") else None
        counts.append(f"{name} {request[name]}" + (f" ({bound})" if bound else ""))
    mode = request.get("mode") or spec.get("mode") or "not stated"
    limit = request.get("time_limit_min") or spec.get("time_limit_min")
    lines = [
        f"Headcount: {headcount if headcount is not None else 'not stated'}",
        "Counts the user stated: " + (", ".join(counts) or "none (the plan as shown)"),
        f"Mode: {mode}",
        f"Time limit: {f'{limit} minutes' if limit else 'none'}",
        "Must use: " + (", ".join(request.get("must_use") or []) or "nothing"),
        "Prefer using: " + (", ".join(request.get("prefer_use") or []) or "nothing"),
        "Leave out of this meal: " + (", ".join(meal_avoid) or "nothing"),
        f"Taste wish: {request.get('query') or 'none'}",
    ]
    return "Current conditions:\n" + "\n".join(f"- {line}" for line in lines)


def live_plan(session, current: dict) -> tuple[dict, list[int]]:
    """The current plan without the dishes deleted from the library since it was shown, and
    the ids of those dishes."""
    shown_ids = [r["id"] for r in current["recipes"]]
    existing = set(session.scalars(select(Recipe.id).where(Recipe.id.in_(shown_ids))))
    deleted = [i for i in shown_ids if i not in existing]
    return without_deleted(current, deleted), deleted


def meal_avoid_ids(session, plan: dict, constraints: dict) -> list[int]:
    """The plan's dishes that break this meal's one-meal avoidance held in `constraints`
    (plan 3b-1b spec 5.4), in plan order."""
    shown = [r["id"] for r in plan["recipes"]]
    ctx = load_planning_context(session, meal_constraints({"constraints": constraints}))
    found = validate_plan(
        session, ctx, PlanSpec.model_validate(plan["spec"]), shown, carried_overrides(plan, shown)
    )
    return list(
        dict.fromkeys(
            v.recipe_id
            for v in found.violations
            if v.code == "H2" and v.detail.get("diner_key") == MEAL_KEY
        )
    )


def _newest_card(state: ChatState) -> tuple[int, dict] | None:
    """The newest reply that has a card, as (its index in `messages`, the card): the card the
    chat keeps actionable, also after later replies without a card (an "other" reply, a
    question, the empty-plan question). None when no reply has a card."""
    plans = state.get("reply_plans") or {}
    messages = state["messages"]
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, AIMessage) and (message.id or "") in plans:
            return index, plans[message.id]
    return None


def _button_card(state: ChatState) -> dict | None:
    """The card that offered the button of this turn: the newest card (`_newest_card`), when
    it keeps the request it was built from. None when no reply has a card or the card was
    saved before cards kept their request (or was replaced by a hand edit)."""
    found = _newest_card(state)
    if found is None:
        return None
    card = found[1]
    return card if card.get("request") is not None else None


# The card field that keeps a kept revision card's turn words (`revision_words`), so a button
# pressed on that card later, after turns without a card, still sees that turn's wish.
WISH_WORDS = "wish_words"


def revision_words(revision: dict) -> str:
    """All the user's words a revision is checked against: on a button revision the offering
    turn's wish (`earlier`), then this turn's message. A revision stored before `earlier`
    existed has both in `message` already."""
    return "\n".join(w for w in (revision.get("earlier"), revision.get("message")) if w)


def _offering_words(state: ChatState) -> list[str]:
    """On a button revision, the user's words of the turn whose card offered the button (the
    newest card). That card keeps them (`WISH_WORDS`, `revision_words` of its turn, which
    already hold the words before an earlier button in a chain). A card saved before it kept
    them: when it is the newest reply, the state's `revision` is still its turn's; otherwise
    (or with no revision) the user's messages just before the card's reply."""
    found = _newest_card(state)
    if found is None:
        return []
    reply_index, card = found
    if card.get(WISH_WORDS):
        return [card[WISH_WORDS]]
    messages = state["messages"][:-1]
    newest_reply = max(
        (i for i, m in enumerate(messages) if isinstance(m, AIMessage)), default=None
    )
    if newest_reply == reply_index:
        words = revision_words(state.get("revision") or {})
        if words:
            return [words]
    return _current_request(messages[:reply_index])


def _fresh_turn(state: ChatState) -> dict:
    """The state a new message starts from, whether it is routed or read: a new request may be
    asked about once, and the thread's request is kept before this turn replaces it (a change
    compares the two). The reader sets `reading` itself."""
    return {
        "asked_question": False,
        "previous_request": state.get("request"),
        "button": None,
        "reading": None,
    }


def _button_turn(state: ChatState) -> bool:
    """This turn is a button whose request code changed (route_intent set `button`)."""
    return bool(state.get("button")) and _is_action_message(state)


def _reply_card(
    card: dict, request: dict | None, plan_kept: bool = False, words: str = ""
) -> dict:
    """A reply card as `reply_plans` keeps it: with the request of the turn it was built from,
    so a button on it changes that request (spec 5.5), and `plan_kept` on a revision card that
    left the plan as it was (failed, unclear, nothing fits), so the button revises again, with
    that turn's words (`WISH_WORDS`) as the earlier wish. All three are server-only fields."""
    shown = {**card, "request": request}
    if plan_kept:
        shown["plan_kept"] = True
        if words:
            shown[WISH_WORDS] = words
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

    def get_planner() -> CompiledStateGraph:
        # Built on first use, so conversations that never plan never build a model.
        nonlocal planner_agent
        if planner_agent is None:
            planner_agent = build_planner(deps)
        return planner_agent

    picker_agent: CompiledStateGraph | None = None

    def get_picker() -> CompiledStateGraph:
        nonlocal picker_agent
        if picker_agent is None:
            picker_agent = build_picker(deps)
        return picker_agent

    def route_intent(state: ChatState) -> dict:
        fresh = _fresh_turn(state)
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
            # A card whose change was done only in part (plan 3b-2 spec 5.5) revises too: the
            # button re-runs the unfinished parts against the version it shows.
            unfinished = (card.get(UNFINISHED) or {}).get("reading")
            revises = bool(card.get("plan_kept") or unfinished) and (
                state.get("current_plan") is not None
            )
            base = card["request"]
            if revises and not base.get("counts_stated_now"):
                # The card's turn stated no count: the counts are the plan the user sees
                # (3b-1 spec 5.2), so "one fewer" is one fewer than the dishes shown. A turn
                # that stated counts (a failed "再加一道") keeps them.
                base = on_plan_counts(base, state["current_plan"])
            card_spec = relax_split_spec(card) if action == "relax_split" else card.get("spec")
            return {
                **fresh,
                "intent": "revise" if revises else "plan",
                "request": apply_action(base, action, card_spec),
                "button": action,
                "model_calls": [],
            }
        messages = [SystemMessage(load_route_prompt()), *state["messages"]]
        result = deps.client.invoke_structured(deps.task, messages, Intent)
        intent = result.parsed.intent
        if intent == "revise" and state.get("current_plan") is None:
            # Nothing to revise yet (spec 5.1).
            intent = "plan"
        elif intent == "unsure":
            # The choice card was removed (plan 3b-2 spec 5.1, Task 8). A follow-up on a plan
            # with dishes is read, not routed, so here there is no dish to change: plan.
            intent = "plan"
        # A "revise" after an early stop or a no-plan reply (no dishes) is parsed as a
        # follow-up: the plan line, the plan's conditions carried (`carry_unstated`), no
        # ask-first question; `after_parse` then plans from that parse.
        return {
            **fresh,
            "intent": intent,
            # A new turn starts the buffer fresh, so events left by a turn that failed
            # before load_context are never recorded under this run.
            "model_calls": [_model_call_event(deps.client, "route", messages, result)],
        }

    def read_follow_up(state: ChatState) -> dict:
        """Plan 3b-2 spec 5.1, 5.3: a follow-up on a plan with dishes is read once, with no
        route call. The reader sees the plan's lines with their marks, the diners' rules as
        anonymous labels (Diner 1 is the user), the meal's current conditions (this meal's
        one-meal avoidance included) and the whole conversation. Its kind picks the path
        (`READING_INTENTS`); the reading is kept in the state and recorded with the run."""
        # The request the plan was built from: the conditions the plan meets.
        base = state.get("plan_request") or state.get("request") or {}
        constraints = state.get("constraints") or {
            "diner_ids": [],
            "guest_rules": [],
            "unconfirmed_diners": 0,
            "headcount": None,
        }
        with deps.session_factory() as session:
            plan, _ = live_plan(session, state["current_plan"])
            meal_avoid = current_meal_avoid(plan, base)
            avoid_ids = meal_avoid_ids(
                session, plan, with_meal_avoid(session, constraints, meal_avoid)
            )
            _, mains = dish_facts(session, [r["id"] for r in plan["recipes"]])
            # The people's rules only: this meal's avoidance is a condition, not a diner, so
            # it is taken out of the constraints and gives no rule line.
            people = with_meal_avoid(session, constraints, [])
            ctx = load_planning_context(session, meal_constraints({"constraints": people}))
            diner_lines = meal_preferences(session, ctx, current_table())
        messages = [
            SystemMessage(load_prompt("follow_up")),
            *reader_messages(
                state["messages"],
                current_plan_text(plan, mains, avoid_ids),
                diner_lines,
                reader_conditions(base, plan, constraints.get("headcount"), meal_avoid),
                state.get("locale", "en"),
            ),
        ]
        result = deps.client.invoke_structured(deps.task, messages, FollowUpReading)
        reading = result.parsed
        return {
            **_fresh_turn(state),
            "intent": READING_INTENTS[reading.kind],
            "reading": reading.model_dump(),
            # A new turn starts the buffer fresh; the run records it when it starts.
            "model_calls": [_model_call_event(deps.client, "read_follow_up", messages, result)],
        }

    def read_paused_turn(state: ChatState) -> dict:
        """`read_follow_up` for an older thread's revise turn whose parse already ran (it was
        paused on a question before plan 3b-2): `request` is that parse, so the turn keeps the
        `previous_request` route_intent set for it."""
        return {**read_follow_up(state), "previous_request": state.get("previous_request")}

    def after_reading(state: ChatState) -> str:
        """A change goes straight to the change path, with no parse call (spec 5.1); a people
        message (which then shows the diner card) and a new meal take the parse; "other" gets
        the fixed reply with the plan left as it is."""
        kind = (state.get("reading") or {}).get("kind")
        if kind == "change":
            return "build_change"
        return "other" if state["intent"] == "other" else "parse_request"

    def other(state: ChatState) -> dict:
        # The turn ends without planning, so its route or reader call is not recorded anywhere.
        return {
            "messages": [AIMessage(other_reply(state.get("locale", "en")))],
            "model_calls": [],
        }

    def choose(state: ChatState) -> str:
        if state["intent"] not in ("plan", "revise"):
            return "other"
        if _button_turn(state):
            # The request is complete: no parse and no question (spec 5.5). Who eats is the
            # card's, so the diner card shows only when a settled friend was deleted since.
            if _revises(state):
                # Plan 3b-2: a button that revises builds its frame from its request.
                return "build_change"
            return "select_diners" if friend_deleted_since(state) else "load_context"
        return after_parse(state) if _reuses_parse(state) else "parse_request"

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
        parsed = _restore_parsed(result.parsed.model_dump())
        if _revises(state):
            # A null is no statement on a revise turn either: the request behind the plan fills
            # it before the diner-card check reads it, so a parse that left out the headcount
            # is not "only me", and a fallback new plan keeps the plan's conditions (dev run 3).
            parsed = carry_unstated(
                parsed, state.get("plan_request") or state.get("previous_request")
            )
        return {
            "request": parsed,
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
            # A follow-up never asks the ask-first question: unstated fields come from the
            # current plan. A reading of kind "people" always shows the diner card and plans
            # anew (plan 3b-2 spec 5.1); load_context then marks the turn as a plan.
            if (state.get("reading") or {}).get("kind") == "people":
                return "select_diners"
            # A revise turn with no reading is an older thread's, paused on a question before
            # plan 3b-2: the reader reads it now (the old revise step is gone). With no dish
            # to change (after an early stop) it plans from this parse.
            if _has_dishes(state):
                return "read_paused_turn"
            return "select_diners" if diner_card_needed(state, parsed) else "load_context"
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
            "change": None,
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
        return retry_text(state, session, ctx, header, ids)

    def retry_text(state: ChatState, session, ctx, header: str, ids: list[int]) -> str:
        """`header`, then the previous attempt's violations, then a short summary of that
        attempt (`ids` are the dishes it proposed)."""
        codes = diner_codes(meal_constraints(state), current_table())
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
        state: ChatState, session, ctx, spec: PlanSpec, vectors: dict
    ) -> tuple[str, dict | None, list[int]]:
        """The shortlist (when used), then the request summary. Returns the text, the search
        notes with the shortlist's left-out dishes, and the shortlist ids."""
        notes = state.get("search_notes")
        summary = request_summary(state, meal_preferences(session, ctx, current_table()))
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
        """The tools' runtime context for one attempt of the planner or the picker."""
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
                opening, notes, shortlist_ids = build_opening(state, session, ctx, spec, vectors)
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
                plan["unmet"] = [*plan["unmet"], *wish_unmet_entries(locale)]
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

    # ---- the change path (plan 3b-2 spec 5.4-5.6) ----

    def build_change(state: ChatState, config: RunnableConfig) -> Command:
        """The frame of a change, in code (spec 5.4). On a reader turn the new request is the
        plan's request after the reading (`request_after`); on a button turn it is the
        button's request, and the reading is the offering card's unfinished parts
        (`button_reading`). The dishes that break the new request come from `validate_plan`
        on the kept plan (overrides carried, so a dish kept against a rule by the user's
        confirmation is not breaking). Starts the planning run, recording the frame."""
        button = state["button"] if _button_turn(state) else None
        base = state.get("plan_request") or state.get("previous_request") or {}
        wish_choice = False
        if button is None:
            reading = FollowUpReading.model_validate(state["reading"])
            request = request_after(base, reading)
            words = "\n".join(_current_request(state["messages"]))
            unfinished = None
        else:
            reading = None
            request = state["request"]
            card = _button_card(state) or {}
            unfinished = card.get(UNFINISHED)
            if UNFINISHED not in card and card.get("plan_kept") and request.get("query"):
                # A kept card saved before cards kept their unfinished parts: its wish is
                # what was not done. It names no dish, so the picker chooses which dishes
                # it touches, inside a limit code sets.
                unfinished = {"reading": {"wish": request["query"]}}
                wish_choice = True
            words = (unfinished or {}).get("words") or "\n".join(_offering_words(state))
        parsed = ParsedRequest.model_validate(request)
        if request_problem(parsed):
            # Stated counts that contradict each other: nothing changes (no question, spec
            # 5.6); the reply asks for the counts again.
            return Command(update={"model_calls": []}, goto="unclear")
        index = from_version(state["current_plan"], state.get("plan_versions") or [])
        with deps.session_factory() as session:
            plan, deleted = live_plan(session, state["current_plan"])
        # A reading's counts are this message's own (request_after clears the count group),
        # so a side it states counts even when it equals the plan's request ("还是要两个荤菜"
        # after a turn that could not add one); the other fields still compare with it.
        previous = base if button is not None else {
            k: v for k, v in base.items() if k not in COUNT_FIELDS
        }
        try:
            spec = revision_spec(plan, previous, request)
        except ValidationError:
            return Command(update={"model_calls": []}, goto="unclear")
        if button is not None:
            spec = spec.model_copy(update=spec_changes(request, button))
            meal_avoid = revision_meal_avoid(plan, base, request)
            used = button_reading(
                unfinished, request, spec, any_side=button == "relax_split"
            )
        else:
            if reading.conditions.clear_time_limit:
                spec = spec.model_copy(update={"time_limit_min": None})
            # The reading's whole list: a withdrawn avoidance is simply missing.
            meal_avoid = list(request.get("meal_avoid") or [])
            used = reading
        ids = [r["id"] for r in plan["recipes"]]
        named: dict[str, int] = {}
        with deps.session_factory() as session:
            constraints = with_meal_avoid(session, state["constraints"], meal_avoid)
            ctx = load_planning_context(session, meal_constraints({"constraints": constraints}))
            found = validate_plan(session, ctx, spec, ids, carried_overrides(plan, ids))
            breaking = breaking_from(found)
            before = {fold(n) for n in PlanSpec.model_validate(plan["spec"]).must_use}
            uncovered = sorted(
                n for n in must_unmet_names(found.violations) if fold(n) not in before
            )
            facts, _ = dish_facts(session, ids)

            def lookup(name: str):
                # The new request's mode, time limit and rules decide what hides a dish.
                result = lookup_named(session, name, spec.model_dump(), ctx=ctx)
                if result.status == "found" and result.blocked is None:
                    named[name] = result.recipe_id
                return result

            frame = build_frame(
                ids,
                locked_ids(plan) - open_conflict_ids(plan),
                facts,
                plan["spec"],
                spec,
                used,
                breaking,
                lookup,
                words,
                uncovered_must=uncovered,
                wish_choice=wish_choice,
            )
        prepare = deps.client.prepare_text
        record = {
            "messages": [prepare(t) for t in _current_request(state["messages"])],
            "parsed": map_strings(request, prepare),
            "spec": map_strings(spec.model_dump(), prepare),
            "headcount": constraints["headcount"],
            "locale": state.get("locale", "en"),
            "revision": {"from_version": index},
            "reading": map_strings(state["reading"], prepare) if button is None else None,
        }
        if button is not None:
            record["button"] = button
        settled_data = state.get("settled_diners")
        settled = SettledDiners.from_state(settled_data) if settled_data else None
        defaults_used = revision_defaults(plan.get("defaults_used") or [], plan["spec"], spec)
        run_id = deps.recorder.start(
            thread_id=config["configurable"]["thread_id"],
            request=record,
            defaults_used=defaults_used,
            prompt_version=PROMPT_VERSION,
            tools_version=PICK_TOOLS_VERSION,
            has_temporary_guests=bool(parsed.guests or (settled and settled.guest_names)),
        )
        for stale in [r for r in deps.run_query_vectors if r != run_id]:
            del deps.run_query_vectors[stale]
        for event in state.get("model_calls") or []:
            deps.recorder.add_event(run_id, event)
        pairs = [[i, reason] for i, reason in breaking.items()]
        deps.recorder.add_event(
            run_id,
            {
                "kind": "frame",
                "reading": map_strings(used.model_dump(), prepare),
                "breaking": pairs,
                "uncovered_must": map_strings(uncovered, prepare),
                "frame": map_strings(frame.model_dump(), prepare),
            },
        )
        change = {
            "from_version": index,
            "deleted": deleted,
            "plan_ids": ids,
            "frame": frame.model_dump(),
            "reading": used.model_dump(),
            "words": words,
            "breaking": pairs,
            "named": named,
            "outcome": None,
        }
        update = {
            "request": request,
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
            # An older thread's revise-step state is cleared, so it never reads as this turn's.
            "revision": None,
            "change": change,
        }
        goto = "pick_dishes" if needs_picker(frame) and not frame.empty_plan else "present_change"
        return Command(update=update, goto=goto)

    def picker_shortlist(
        session,
        ctx,
        spec: PlanSpec,
        frame: Frame,
        facts: dict,
        request: dict,
        vectors: dict,
        plan_ids: list[int],
    ) -> tuple[str, Any]:
        """The shortlist for the dish types the picker may fill: an add's own type; for a
        replace slot, a replace choice or an add of any type, which take a dish of any type,
        the replaced dishes' types and the meat and vegetable dishes. A named add has no type:
        the picker searches for it. The plan's dishes (`plan_ids`) and the dishes placed by
        name are left out, since the picker may not choose them. Returns the text and the
        shortlist result."""
        sides = ["meat dish", "vegetable dish"]
        types: list[str] = []
        for slot in frame.slots:
            if slot.dish_type:
                types.append(slot.dish_type)
            elif slot.replaces is not None:
                types += [facts[slot.replaces].dish_type] if slot.replaces in facts else []
                types += sides
            elif not slot.named:
                types += sides
        for choice in frame.choices:
            if choice.mode == "replace":
                types += [facts[i].dish_type for i in choice.candidates if i in facts] + sides
        types = [t for t in dict.fromkeys(types) if t]
        none = "Shortlist: none given; search the library when a slot needs a dish."
        if not deps.use_shortlist or not types:
            return none, None
        counts = count_categories(dict.fromkeys(types))
        try:
            wanted = PlanSpec.model_validate(
                {**spec.model_dump(), **counts, "meat_bound": "exact", "vegetable_bound": "exact"}
            )
        except ValidationError:
            return none, None
        found = build_shortlist_result(
            session, deps.client, ctx, wanted, ParsedRequest.model_validate(request), vectors,
            exclude_ids=[*plan_ids, *frame.placed],
        )
        return f"{SHORTLIST_HEADER}\n{shortlist_json(found.entries, found.complete)}", found

    def picker_wish(state: ChatState, change: dict) -> str:
        """The wish the picker reads, in the user's words: the reading's, else the request's
        taste wish; on a button turn with the offering turn's words as its context."""
        wish = (change["reading"].get("wish") or "").strip() or state["request"].get("query") or ""
        if not _button_turn(state) or not change.get("words"):
            return wish
        earlier = f"the user's earlier message: {change['words']}"
        return f"{wish} ({earlier})" if wish else earlier

    def check_pick(state: ChatState, plan: dict, frame: Frame, answer, spec: PlanSpec, codes):
        """The picker's answer against the frame (`apply_picks`), then the hard checks of its
        new dishes (rules, stock, cookware, time) and of a must-use choice or add (its
        ingredient, `must_use_misses`). Returns the outcome (None without an answer), the
        problems for a retry, the new dishes that broke a check (each with why, for the
        records), the facts, and the named slots whose dish a hard condition hides (slot
        number to the dish and the condition).

        A named slot's dish is the one the user named, as the picker read the words: when a
        hard condition hides it, that is no retry problem but the slot's real reason (spec
        5.4: "in the library, but ..." and the matching next action, never "not in the
        library")."""
        ids = state["change"]["plan_ids"]
        if answer is None:
            return None, [PICK_NO_ANSWER], {}, {}, {}
        chosen = [s.recipe_id for s in answer.slots if s.recipe_id is not None]
        hidden: dict[int, dict] = {}
        with deps.session_factory() as session:
            facts, _ = dish_facts(session, [*ids, *frame.placed, *chosen])
            outcome = apply_picks(ids, frame, answer, facts)
            if outcome.problems:
                return outcome, list(outcome.problems), {}, facts, {}
            new = set(outcome.recipe_ids) - set(ids) - set(frame.placed)
            ctx = load_planning_context(session, meal_constraints(state))
            fills = {s.index: s.recipe_id for s in reversed(answer.slots)}
            for index, slot in enumerate(choice_slots(frame, answer)):
                recipe_id = fills.get(index)
                if not slot.named or recipe_id not in new:
                    continue
                recipe = session.get(Recipe, recipe_id)
                why = hidden_by(session, recipe, spec.model_dump(), ctx) if recipe else None
                if why is not None:
                    hidden[index] = {
                        "recipe_id": recipe_id,
                        "title": recipe.title,
                        "blocked": why,
                        "time_min": recipe.time_min,
                    }
            result = validate_plan(
                session, ctx, spec, outcome.recipe_ids,
                carried_overrides(plan, outcome.recipe_ids),
            )
        named_hidden = {d["recipe_id"] for d in hidden.values()}
        bad = [
            v for v in result.violations
            if v.recipe_id in new
            and v.recipe_id not in named_hidden
            and v.code in ("H1", "H2", "H3", "H6")
        ]
        problems = [_violation_for_model(v.model_dump(mode="json"), codes) for v in bad]
        # Only a must-use name this turn made a choice for is checked; a name the plan
        # missed before is not the picker's to meet.
        missed, unused = must_use_misses(frame, answer, must_unmet_names(result.violations))
        left_out = {i: MUST_USE_MISS for i in unused}
        left_out.update({v.recipe_id: "breaks a rule" for v in bad})
        return outcome, problems + missed, left_out, facts, hidden

    def pick_dishes(state: ChatState) -> dict:
        try:
            return pick_attempts(state)
        except BaseException:
            deps.run_query_vectors.pop(state["run_id"], None)
            raise

    def pick_attempts(state: ChatState) -> dict:
        """The picker fills the frame's slots and makes its choices (spec 5.5, 5.7): at most
        PICK_MAX_ATTEMPTS attempts, each a separate call built from fresh messages; a retry
        adds the previous attempt's problems as text. When the last attempt still has
        problems, its dishes that broke a hard check are left out, or (no usable answer) code
        makes only the drop choices (`fallback_pick`)."""
        change = state["change"]
        frame = Frame.model_validate(change["frame"])
        spec = PlanSpec.model_validate(state["spec"])
        plan = without_deleted(state["current_plan"], change["deleted"])
        ids = change["plan_ids"]
        locale = state.get("locale", "en")
        run_id = state["run_id"]
        vectors = deps.run_query_vectors.setdefault(run_id, {})
        avoid = [i for i, reason in change["breaking"] if reason == "meal_avoid"]
        with deps.session_factory() as session:
            ctx = load_planning_context(session, meal_constraints(state))
            facts, mains = dish_facts(session, ids)
            plan_lines = current_plan_text(plan, mains, avoid)
            rules = meal_preferences(session, ctx, current_table())
            shortlist, found = picker_shortlist(
                session, ctx, spec, frame, facts, state["request"], vectors, ids
            )
        notes = state.get("search_notes")
        if found is not None:
            notes = _merge_notes(notes, [], found.semantic_paused, found.left_out_unknown_allergens)
        rules_text = "Diner and meal rules:\n" + "\n".join(
            f"- {line}" for line in rules or ["none recorded"]
        )
        wish = picker_wish(state, change)
        codes = diner_codes(meal_constraints(state), current_table())
        context = planner_context(state, spec, vectors)
        problems: list[str] = []
        attempts = 0
        answer = outcome = None
        bad: dict[int, str] = {}
        hidden: dict[int, dict] = {}
        first_passed = None
        # The picker's model calls so far this turn (`PICK_TURN_MODEL_CALLS`).
        calls_used = 0

        def fits() -> bool:
            return calls_used + PICK_MODEL_CALL_LIMIT <= PICK_TURN_MODEL_CALLS

        def model_calls(out: dict) -> int:
            # A model's reply carries usage or response metadata; the message the call-limit
            # middleware adds when it ends a run carries neither.
            return sum(
                1
                for m in out["messages"]
                if isinstance(m, AIMessage) and (m.usage_metadata or m.response_metadata)
            )

        for attempt in range(PICK_MAX_ATTEMPTS):
            if attempt and not fits():
                # The turn's budget is spent: the last answer's problems stand, and code's
                # fallback below takes over.
                break
            attempts = attempt + 1
            # Fresh messages every attempt: never the previous attempt's tool results.
            messages = picker_messages(
                frame, plan_lines, rules_text, wish, shortlist, locale, conditions_line(spec)
            )
            if problems:
                messages.append(
                    HumanMessage(
                        "Your previous answer had these problems. Answer again with each "
                        "one fixed:\n" + "\n".join(f"- {p}" for p in problems)
                    )
                )
            out = get_picker().invoke({"messages": messages, "run_id": run_id}, context=context)
            calls_used += model_calls(out)
            notes = _notes_from_searches(notes, out["messages"])
            answer = out.get("structured_response")
            empty_replies = 0
            if answer is None and fits():
                # A reply with no answer (no answer tool call, or one whose arguments could not
                # be read; the tool choice is already forced) is asked again at once, once,
                # without using up an attempt, while the turn's budget allows a whole attempt.
                empty_replies = 1
                again = [*messages, HumanMessage(PICK_NO_ANSWER)]
                out = get_picker().invoke({"messages": again, "run_id": run_id}, context=context)
                calls_used += model_calls(out)
                notes = _notes_from_searches(notes, out["messages"])
                answer = out.get("structured_response")
            outcome, problems, bad, facts, hidden = check_pick(
                state, plan, frame, answer, spec, codes
            )
            if first_passed is None:
                first_passed = not problems
            deps.recorder.add_event(
                run_id,
                {
                    "kind": "pick_attempt",
                    "attempt": attempt,
                    "answer": (
                        map_strings(answer.model_dump(), deps.client.prepare_text)
                        if answer is not None
                        else None
                    ),
                    "problems": map_strings(problems, deps.client.prepare_text),
                    "recipe_ids": outcome.recipe_ids if outcome and not problems else None,
                    # Replies with no answer asked again within this attempt.
                    "empty_replies": empty_replies,
                },
            )
            if not problems:
                break
        own = not problems
        used_frame = frame
        # The slots the picker itself answered null ("nothing fits"); none when code's fallback
        # replaced its answer. Only such a named slot is said to be not in the library.
        picker_nulls: set[int] = set()
        # Code's fallback replaced the picker's answer: no slot was really tried.
        fell_back = False
        if answer is not None:
            fills: dict[int, int | None] = {}
            for item in answer.slots:
                fills.setdefault(item.index, item.recipe_id)
            picker_nulls = {i for i, recipe_id in fills.items() if recipe_id is None}
        if problems:
            if answer is not None and outcome is not None and not outcome.problems:
                for reason in set(bad.values()):
                    picks = [i for i, why in bad.items() if why == reason]
                    answer = without_picks(answer, picks, reason)
            else:
                used_frame, answer = fallback_pick(frame)
                hidden = {}
                picker_nulls = set()
                fell_back = True
        if hidden:
            # A named dish a hard condition hides is not placed; its slot says why.
            answer = without_picks(
                answer, [d["recipe_id"] for d in hidden.values()], "hidden by a hard condition"
            )
        # A stated count whose added dish was not found keeps the dishes it would have
        # dropped (spec 5.5: the plan never shrinks unasked).
        withheld, answer = withhold_count_drops(used_frame, answer)
        if withheld is not used_frame:
            # The picker's note may describe a drop code withheld.
            own = False
        # A dish chosen only for the wish that does not meet it is not placed: the dish it
        # was to replace stays (user decision, 2026-11-23).
        answer, wish_unmet_slots = without_unmet_wish_picks(withheld, answer)
        if wish_unmet_slots:
            # The note may describe the dish that is not placed.
            own = False
        if problems or hidden or withheld is not used_frame or wish_unmet_slots:
            used_frame = withheld
            with deps.session_factory() as session:
                chosen = [s.recipe_id for s in answer.slots if s.recipe_id is not None]
                facts, _ = dish_facts(session, [*ids, *frame.placed, *chosen])
            outcome = apply_picks(ids, used_frame, answer, facts)
        # Only this turn's wish (on a button, the offering turn's) is ever reported unmet.
        has_wish = bool((change["reading"].get("wish") or "").strip())
        slots = slot_results(used_frame, answer)
        for index, slot in enumerate(slots):
            slot["picker_null"] = index in picker_nulls
        for index, detail in hidden.items():
            if index < len(slots):
                slots[index]["hidden"] = detail
        for index in wish_unmet_slots:
            slots[index]["wish_unmet"] = True
        for slot in slots:
            slot["not_done"] = fell_back
        result = {
            "recipe_ids": outcome.recipe_ids,
            "slots": slots,
            # A changed answer is no longer the picker's: its note and wish_met may name a
            # dish that is not placed. A hidden named dish changes only the note.
            "wish_met": answer.wish_met if own else (False if has_wish else None),
            "note": answer.note.strip() if own and not hidden else "",
            "choices": [c.model_dump() for c in used_frame.choices],
            "withheld_drops": [i for i in frame.count_drops if i not in used_frame.drops],
        }
        return {
            "change": {**change, "outcome": result},
            "retries": attempts - 1,
            "first_passed": first_passed,
            "search_notes": _merge_notes(notes, [], False),
        }

    def change_done_text(
        live: dict,
        result_ids: list[int],
        change: dict,
        slots: list[dict],
        titles: dict[int, str],
        breaking: dict[int, str],
        meal_avoid: list[str],
        locale: str,
        removed: Collection[int] = (),
    ) -> str:
        """What the change did, written by code (spec 5.6): replacements, drops and adds of
        unlocked dishes, then one sentence per locked dish that changed, with its reason (this
        meal's avoidance, a diner's rule, or the user's words). A dish removed for a rule with
        no replacement is said with what could not be done."""
        before = [r["id"] for r in live["recipes"]]
        kept = set(result_ids)
        gone = [i for i in before if i not in kept]
        pairs: dict[int, int] = {}
        for slot in slots:
            if slot["replaces"] is not None and slot["recipe_id"] in kept:
                pairs[slot["replaces"]] = slot["recipe_id"]
        for item in change["reading"].get("remove") or []:
            new = change["named"].get(item.get("with_name") or "")
            if item["id"] in gone and new in kept:
                pairs[item["id"]] = new
        ruled_out = {
            s["replaces"] for s in slots
            if s["recipe_id"] is None and s["replaces"] is not None and s["reason"] == "breaks_rule"
        } | set(removed)
        locked = locked_ids(live) - open_conflict_ids(live)
        hand = set(live.get("locked") or [])
        taken = set(pairs.values())
        swaps = [(titles[i], titles[pairs[i]]) for i in gone if i in pairs and i not in locked]
        dropped = [
            titles[i] for i in gone if i not in pairs and i not in locked and i not in ruled_out
        ]
        added = [titles[i] for i in result_ids if i not in before and i not in taken]
        parts = [change_sentences(swaps, dropped, added, locale)]
        for i in gone:
            if i in locked and i not in ruled_out:
                replacement = titles.get(pairs[i]) if i in pairs else None
                reason = rule_reason(breaking.get(i, ""), meal_avoid, locale)
                parts.append(locked_changed_text([(titles[i], replacement, i in hand)], locale, reason))
        return ("" if is_zh(locale) else " ").join(p for p in parts if p)

    def present_change(state: ChatState) -> dict:
        """The change's result (spec 5.5, 5.6): a new version when the dishes or the meal's
        conditions changed, else the kept plan; partial results apply what could be done. The
        reply: code's change sentences, the reading sentence on an ambiguous message, the
        picker's note, what could not be done, and the unmet wish. The card offers next
        actions for the unfinished parts only and keeps them (`UNFINISHED`) for a button. A
        change that leaves no dish asks the empty-plan question; an ambiguous reference that
        matches no dish asks which dish was meant; neither has a card."""
        locale = state.get("locale", "en")
        change = state["change"]
        frame = Frame.model_validate(change["frame"])
        current = state["current_plan"]
        versions = list(state.get("plan_versions") or [])
        deleted = change["deleted"]
        live = without_deleted(current, deleted)
        ids = change["plan_ids"]
        used = FollowUpReading.model_validate(change["reading"])
        button = _button_turn(state)
        shown = None if button else FollowUpReading.model_validate(state["reading"])
        breaking = {i: reason for i, reason in change["breaking"]}
        outcome = change.get("outcome")
        if outcome is None:
            # Nothing to choose: the frame's drops and named dishes alone.
            done = apply_picks(ids, frame, PickerAnswer(), {})
            outcome = {"recipe_ids": done.recipe_ids, "slots": [], "wish_met": None, "note": ""}
        result_ids = outcome["recipe_ids"]
        spec = state["spec"]
        meal_avoid = list(state["constraints"].get("meal_avoid") or [])
        with deps.session_factory() as session:
            titles = dict(
                session.execute(
                    select(Recipe.id, Recipe.title).where(Recipe.id.in_({*ids, *result_ids}))
                ).all()
            )
            must_missed = None
            if any(c.must_use for c in frame.choices) or any(s.must_use for s in frame.slots):
                # Only a must-use name the result still misses is said unused: another new
                # dish may use it even when its own slot found nothing.
                ctx = load_planning_context(session, meal_constraints(state))
                found = validate_plan(
                    session, ctx, PlanSpec.model_validate(spec), result_ids,
                    carried_overrides(live, result_ids),
                )
                must_missed = must_unmet_names(found.violations)
        titles.update({r["id"]: r["title"] for r in live["recipes"]})
        items = unfinished_items(frame, outcome["slots"], titles, breaking, must_missed)
        said = {i["recipe_id"] for i in items if i["kind"] == "removed"}
        paired = {s["replaces"] for s in outcome["slots"] if s["recipe_id"] is not None}
        named_gone = {
            r["id"] for r in change["reading"].get("remove") or []
            if change["named"].get(r.get("with_name") or "") in result_ids
        }
        for dish_id in ids:
            if (
                dish_id not in result_ids
                and breaking.get(dish_id) in RULE_BREAKS
                and dish_id not in said | paired | named_gone
            ):
                items.append(
                    {
                        "kind": "removed",
                        "recipe_id": dish_id,
                        "title": titles.get(dish_id, ""),
                        "why": breaking[dish_id],
                    }
                )
        # The wish that guided this turn's picks: the reading's, or on a button the offering
        # turn's unfinished wish; an older turn's wish is never reported unmet here.
        wish = used.wish.strip()
        wish_unmet = bool(wish) and outcome["wish_met"] is False
        limit = spec.get("time_limit_min")
        sentences = [unfinished_sentence(i, limit, meal_avoid, locale) for i in items]
        actions = change_actions(items, spec, wish_unmet, stated=frame.count_ranges)
        unmet = change_unmet(items, sentences, limit, locale, wish_unmet)
        unmet_wish_text = change_wish_unmet(wish, locale) if wish_unmet else ""
        # Spec 5.4: a wish that touches no dish of the plan and guides no new dish changes
        # nothing, and the reply says so.
        guided = frame.slots or any(c.mode == "replace" for c in frame.choices)
        targets = list(used.wish_targets)
        locked_targets = {
            item.get("wish_target")
            for item in frame.ignored
            if item.get("reason") == "locked" and "wish_target" in item
        }
        nothing_text = ""
        if wish and targets and set(targets) <= locked_targets:
            # Every dish the wish touches was added or kept by hand: they stay.
            nothing_text = wish_targets_locked(wish, locale)
        elif wish and not targets and not guided and not frame.placed:
            nothing_text = wish_matched_nothing(wish, locale)
        read_text = (
            reading_sentence(shown.reading, locale)
            if shown is not None and shown.ambiguous and shown.reading.strip()
            else ""
        )
        dishes_changed = result_ids != [r["id"] for r in live["recipes"]]
        # A must-use ingredient no dish could use is said once, by the reply: it is not a
        # change of conditions on its own (with no other change the plan stays, spec 5.5), and
        # a new version neither carries it nor shows it again as unmet. The request keeps it,
        # so a button on the card tries it again.
        unused = unused_must(items)
        compared = spec
        if unused:
            compared = {
                **spec,
                "must_use": [n for n in spec.get("must_use") or [] if fold(n) not in unused],
            }
        plan_request = state["request"]
        if unused:
            plan_request = {
                **plan_request,
                "must_use": [
                    n for n in plan_request.get("must_use") or [] if fold(n) not in unused
                ],
            }
        new_conditions = conditions_changed(live, compared, meal_avoid)
        frame_empty = not (
            frame.drops or frame.slots or frame.choices or frame.placed or frame.missing
        )
        asks_which = (
            shown is not None
            and shown.ambiguous
            and frame_empty
            and not shown.add
            and not shown.wish.strip()
            and not any(v is not None for v in shown.counts.model_dump().values())
            and shown.conditions == ReadConditions()
            and not new_conditions
        )
        update: dict[str, Any] = {}
        shows_card = True
        plan_kept = True
        with deps.session_factory() as session:
            if frame.empty_plan or not result_ids:
                card = unchanged_card(current, [], [])
                note = empty_plan_question(locale)
                shows_card = False
            elif asks_which:
                card = unchanged_card(live, [], [])
                note = which_dish_question(shown.reading, locale)
                shows_card = False
            elif dishes_changed or new_conditions:
                ctx = load_planning_context(session, meal_constraints(state))
                checked = PlanSpec.model_validate(compared)
                overrides = carried_overrides(live, result_ids)
                found = validate_plan(session, ctx, checked, result_ids, overrides)
                if any(v.code == "H4" for v in found.violations):
                    # The counts follow the change list, so the version's counts match it.
                    types = dict(
                        session.execute(
                            select(Recipe.id, Recipe.dish_type).where(Recipe.id.in_(result_ids))
                        ).all()
                    )
                    checked = result_spec(checked, [types[i] for i in result_ids])
                    found = validate_plan(session, ctx, checked, result_ids, overrides)
                card_spec = checked.model_dump()
                card = revised_card(
                    session,
                    ctx,
                    {**state, "spec": card_spec},
                    recipe_ids=result_ids,
                    result=found,
                    overrides=overrides,
                    locked=kept_locked(live, result_ids),
                    index=len(versions),
                    locale=locale,
                )
                card["revision"] = {"from_version": change["from_version"]}
                card["notes"] = deleted_note(current, deleted)
                card["unmet"] = [*card["unmet"], *unmet]
                card["next_actions"] = actions
                done = change_done_text(
                    live, result_ids, change, outcome["slots"], titles, breaking, meal_avoid,
                    locale, removed=[i["recipe_id"] for i in items if i["kind"] == "removed"],
                )
                if not done and not dishes_changed:
                    done = conditions_only_note(locale)
                parts = [
                    done,
                    read_text,
                    nothing_text,
                    outcome["note"],
                    partial_text(sentences, locale, lead=bool(done)),
                    unmet_wish_text,
                ]
                note = "\n\n".join(p for p in parts if p)
                plan_kept = False
                update.update(plan_request=plan_request, spec=card_spec)
            else:
                card = unchanged_card(live, unmet, actions)
                card["notes"] = deleted_note(current, deleted)
                lead = failed_note(locale, bool(actions)) if items or wish_unmet else (
                    unchanged_note(locale)
                )
                if lead == unchanged_note(locale) and nothing_text:
                    # The wish matched nothing: that sentence says why the plan stays.
                    lead, nothing_text = nothing_text, ""
                parts = [
                    lead,
                    read_text,
                    nothing_text,
                    outcome["note"],
                    partial_text(sentences, locale, lead=False),
                    unmet_wish_text,
                ]
                note = "\n\n".join(p for p in parts if p)
        if plan_kept:
            # The plan stays; `request` keeps this turn's (a button on its card starts from
            # it), and the spec and the meal's avoidance go back to the plan's own.
            with deps.session_factory() as session:
                constraints = with_meal_avoid(
                    session, state["constraints"], current.get("meal_avoid") or []
                )
            update.update(
                spec=current["spec"],
                constraints=constraints,
                defaults_used=current.get("defaults_used") or [],
                search_notes=card_search_notes(current),
            )
        words = change.get("words") or ""
        card = _reply_card(card, state["request"], plan_kept=plan_kept, words=words)
        left = (
            unfinished_reading(used, frame, outcome["slots"], outcome["wish_met"], result_ids)
            if shows_card
            else None
        )
        if shows_card:
            card[UNFINISHED] = {"reading": left, "words": words}
        if not plan_kept:
            update.update(current_plan=card, plan_versions=[*versions, card])
        prepare = deps.client.prepare_text
        deps.recorder.add_event(
            state["run_id"],
            {
                "kind": "change_outcome",
                "recipe_ids": result_ids,
                "plan_kept": plan_kept,
                "empty_plan": bool(frame.empty_plan or not result_ids),
                "asks_which_dish": bool(asks_which),
                "slots": map_strings(outcome["slots"], prepare),
                "wish_met": outcome["wish_met"],
                "unfinished": map_strings(items, prepare),
            },
        )
        deps.run_query_vectors.pop(state["run_id"], None)
        deps.recorder.finish(
            state["run_id"],
            retries=state.get("retries") or 0,
            first_passed=state.get("first_passed") is not False,
            final_passed=not plan_kept,
            final_plan=map_strings(card, prepare),
        )
        reply_id = str(uuid.uuid4())
        reply_plans = dict(state.get("reply_plans") or {})
        if shows_card:
            reply_plans[reply_id] = card
        # Which question the reply asks, if any, for evaluation and debugging.
        if frame.empty_plan or not result_ids:
            asked = "empty_plan"
        else:
            asked = "which_dish" if asks_which else None
        return {
            **update,
            "change": {**change, "outcome": outcome, "asked": asked},
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
    builder.add_node("read_follow_up", with_labels(read_follow_up))
    builder.add_node("read_paused_turn", with_labels(read_paused_turn))
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
    builder.add_node(
        "build_change",
        with_labels(build_change),
        destinations=("pick_dishes", "present_change", "unclear"),
    )
    builder.add_node("pick_dishes", with_labels(pick_dishes))
    builder.add_node("present_change", with_labels(present_change))
    builder.add_conditional_edges(
        START,
        lambda state: "read_follow_up" if _reads_follow_up(state) else "route_intent",
        ["read_follow_up", "route_intent"],
    )
    for reader in ("read_follow_up", "read_paused_turn"):
        builder.add_conditional_edges(
            reader, after_reading, ["build_change", "parse_request", "other"]
        )
    gates = [
        "ask_style",
        "ask_question",
        "select_diners",
        "load_context",
        "unclear",
        "read_paused_turn",
    ]
    builder.add_conditional_edges(
        "route_intent",
        choose,
        ["parse_request", "other", "build_change", *gates],
    )
    builder.add_edge("pick_dishes", "present_change")
    builder.add_edge("present_change", END)
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
