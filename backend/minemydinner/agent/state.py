from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def _latest(_old: str, new: str) -> str:
    return new


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    # The latest value wins: a resume retried after a failed run writes the locale again in
    # the step where the failed resume's write is still pending.
    locale: Annotated[str, _latest]
    intent: str | None
    # This turn's follow-up reading (FollowUpReading as a dict, plan 3b-2 spec 5.2) when the
    # thread had a plan with dishes; None on a routed turn.
    reading: dict | None
    # The parsed request (ParsedRequest as a dict).
    request: dict | None
    # The request the current plan was built from: set by a new plan and by a passed revision,
    # never by a turn that leaves the plan as it was. A revision compares the new request with
    # this one.
    plan_request: dict | None
    # The next-action button of this turn when code changed the request of the card that
    # offered it (plan 3b-1b spec 5.5): no parse ran, and the run's record notes it. Reset by
    # every new message. A reply card in `reply_plans` keeps the request it was built from
    # (`request`) and, on a revision that left the plan as it was, `plan_kept` and that turn's
    # words (`wish_words`).
    button: str | None
    defaults_used: list[str]
    # PlanSpec as a dict, and the meal constraints: diner_ids, guest_rules (RuleRef dicts),
    # unconfirmed_diners, headcount.
    spec: dict | None
    constraints: dict | None
    # Set when the user skipped the request-style question in this conversation.
    # Set once an ask-first question was asked for the current request.
    asked_question: bool
    # Message count when a contradiction question was last asked; a new message re-enables it.
    problem_asked_at: int
    # Route and parse calls (planning-record events) waiting for the planning run to exist;
    # written when it starts, then cleared.
    model_calls: list[dict]
    run_id: int | None
    # The planner's last proposal (PlanProposal dump).
    proposal: dict | None
    validation: dict | None
    first_passed: bool | None
    # The attempt closest to passing among those with no H2 or H5 violation.
    best: dict | None
    # H2 and H5, in that order, when any attempt of the run had them.
    blocked_codes: list[str]
    # The diner keys of the run's H2 findings ("meal" for this meal's one-meal avoidance), so a
    # block caused only by the meal's wish is worded as the meal's (plan 3b-1b spec 5.4).
    blocked_h2_keys: list[str]
    # The feasibility shortfalls (dicts); non-empty means the planner is skipped.
    shortfalls: list[dict]
    # From the shortlist and the planner's searches: left_out_unknown_allergens and
    # semantic_paused.
    search_notes: dict | None
    # The planner's first message, built once per run (shortlist, then request summary) and
    # sent unchanged by every attempt, so retries share a cached prompt prefix.
    planner_opening: str | None
    # The recipe ids the shortlist offered, in opening order, for the retry message.
    shortlist_ids: list[int]
    retries: int
    # The change in progress (plan 3b-2 spec 5.4-5.6): `from_version`, `deleted`, `plan_ids`
    # (the plan it changes), the `frame` (Frame dump), the `reading` it was built from (on a
    # button, the one made from the offering card's unfinished parts), `words` (the user's
    # words it is checked against), `breaking` ([id, reason] pairs), `named` (a named dish's
    # words to the recipe id placed for it) and, after the picker, `outcome`.
    change: dict | None
    current_plan: dict | None
    plan_versions: list[dict]
    # reply_plans is display only, never sent to a model: each plan by the id of the assistant
    # reply it belongs to. questions are the questions the assistant asked ({"at": message
    # count, "value": dict}): shown in the chat and also rendered into the parse input
    # (agent/questions.py); the route and the planner never see them.
    reply_plans: dict[str, dict]
    questions: list[dict]
    # The conversation's label table (LabelTable.to_state()): diner keys to "Diner N". It only
    # grows; a conversation starts one with the user as "Diner 1".
    diner_labels: dict | None
    # The confirmed diner set (SettledDiners.to_state()), reused by later requests that do
    # not change who eats; replaced by the next confirmed card, None after a skipped card. A
    # skipped card on a people turn (plan 3b-2 (two) spec 5.1) leaves it as it was.
    settled_diners: dict | None
    # The parse's own headcount, before `carry_unstated` fills it from the plan's request: the
    # number the message states, which the follow-up card's total starts from. Set by every
    # parse and kept across turns (a new message does not reset it): a turn that reuses the
    # last parse ("plan again") reuses its headcount too, which is correct.
    parsed_headcount: int | None
    # This turn's diner card was skipped on a people turn (the confirmed set stays). Reset by
    # every new message.
    diners_skipped: bool
    # This turn's confirmed card changed the confirmed set (who eats, their meal-only rules,
    # the unmatched people or the headcount): on a people turn the plan then gets a new
    # version even when no dish changes, so its diners line shows the new set. Reset by every
    # new message.
    diners_changed: bool
