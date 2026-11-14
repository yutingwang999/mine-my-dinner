from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    locale: str
    intent: str | None
    request: dict | None
    defaults_used: list[str]
    run_id: int | None
    proposal: dict | None
    validation: dict | None
    retries: int
    current_plan: dict | None
    plan_versions: list[dict]
