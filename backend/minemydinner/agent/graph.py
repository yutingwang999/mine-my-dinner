from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.state import ChatState

PROMPT_VERSION = "plan_v1"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts" / PROMPT_VERSION

OTHER_REPLY_EN = (
    "I can help you plan dinners from your recipes and what you have at home. "
    "Tell me what you would like to cook, for how many people, and I will suggest a plan."
)
OTHER_REPLY_ZH = "我可以根据你的菜谱和家里现有的食材帮你安排晚餐。告诉我你想做什么、几个人吃，我来给你出方案。"
PLAN_PLACEHOLDER = "Planning is not available yet."


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
    return (PROMPT_DIR / "route.md").read_text(encoding="utf-8")


def other_reply(locale: str) -> str:
    return OTHER_REPLY_ZH if locale == "zh" else OTHER_REPLY_EN


def build_graph(deps: GraphDeps, checkpointer) -> CompiledStateGraph:
    def route_intent(state: ChatState) -> dict:
        messages = [SystemMessage(load_route_prompt()), *state["messages"]]
        result = deps.client.invoke_structured(deps.task, messages, Intent)
        return {"intent": result.parsed.intent}

    def plan(state: ChatState) -> dict:
        return {"messages": [AIMessage(PLAN_PLACEHOLDER)]}

    def other(state: ChatState) -> dict:
        return {"messages": [AIMessage(other_reply(state.get("locale", "en")))]}

    def choose(state: ChatState) -> str:
        return "plan" if state["intent"] == "plan" else "other"

    builder = StateGraph(ChatState)
    builder.add_node("route_intent", route_intent)
    builder.add_node("plan", plan)
    builder.add_node("other", other)
    builder.add_edge(START, "route_intent")
    builder.add_conditional_edges("route_intent", choose, ["plan", "other"])
    builder.add_edge("plan", END)
    builder.add_edge("other", END)
    return builder.compile(checkpointer=checkpointer)
