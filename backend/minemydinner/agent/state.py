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
    # The parsed request (ParsedRequest as a dict).
    request: dict | None
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
    proposal: dict | None
    validation: dict | None
    first_passed: bool | None
    # The attempt closest to passing among those with no H2 or H5 violation.
    best: dict | None
    # H2 and H5, in that order, when any attempt of the run had them.
    blocked_codes: list[str]
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
    # The revision in progress (plan 3b-1): `from_version` (the plan_versions index of the plan
    # it changes), `message` (the user's words it is checked against), and the last attempt's
    # `recipe_ids` and `problems` (RevisionProblem dumps). `proposal` then holds the
    # RevisionProposal dump, or None when an attempt gave no answer.
    revision: dict | None
    current_plan: dict | None
    plan_versions: list[dict]
    # reply_plans is display only, never sent to a model: each plan by the id of the assistant
    # reply it belongs to. questions are the questions the assistant asked ({"at": message
    # count, "value": dict}): shown in the chat and also rendered into the parse input
    # (agent/questions.py); the route and the planner never see them.
    reply_plans: dict[str, dict]
    questions: list[dict]
    # The conversation's label table (LabelTable.to_state()): diner keys to "Diner N". It only
    # grows; a thread saved before it existed starts one with the user as "Diner 1".
    diner_labels: dict | None
    # The confirmed diner set (SettledDiners.to_state()), reused by later requests that do
    # not change who eats; replaced by the next confirmed card, None after a skipped card.
    settled_diners: dict | None
