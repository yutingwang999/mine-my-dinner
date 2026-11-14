from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    locale: str
    intent: str | None
    # The parsed request (ParsedRequest as a dict).
    request: dict | None
    defaults_used: list[str]
    # PlanSpec as a dict, and the meal constraints: diner_ids, unconfirmed_diners, headcount.
    spec: dict | None
    constraints: dict | None
    # Set when the user skipped the request-style question in this conversation.
    # Set once an ask-first question was asked for the current request.
    asked_question: bool
    # Message count when a contradiction question was last asked; a new message re-enables it.
    problem_asked_at: int
    run_id: int | None
    proposal: dict | None
    validation: dict | None
    first_passed: bool | None
    # The attempt closest to passing among those with no H2 or H5 violation.
    best: dict | None
    # H2 and H5, in that order, when any attempt of the run had them.
    blocked_codes: list[str]
    # From the planner's searches: left_out_unknown_allergens and semantic_paused.
    search_notes: dict | None
    retries: int
    current_plan: dict | None
    plan_versions: list[dict]
