"""The planner: an inner agent that calls the planning tools and proposes one meal."""

from typing import TYPE_CHECKING

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentState,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.structured_output import ToolStrategy
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from minemydinner.agent.budget import TokenBudgetMiddleware
from minemydinner.agent.middleware import CallLayerMiddleware
from minemydinner.agent.request import load_prompt
from minemydinner.llm.registry import build_chat_model
from minemydinner.planning.tools import PLANNER_TOOLS, PlannerDeps, tools_version

if TYPE_CHECKING:
    from minemydinner.agent.graph import GraphDeps

# The most input tokens one planner model call may carry; a call that reaches it ends the
# attempt before the next call (TokenBudgetMiddleware).
PLANNER_INPUT_TOKEN_BUDGET = 32000
# Per attempt. With the shortlist in the opening a typical plan takes one model call.
MODEL_CALL_LIMIT = 6
TOOL_CALL_LIMIT = 10


class PlanProposal(BaseModel):
    """The planner's final answer: one meal from the user's recipe library."""

    recipe_ids: list[int] = Field(description="Ids of the chosen recipes, from tool results only.")
    note: str = Field(description="One or two sentences to the user, in the reply locale.")


# Covers the tools and the final-answer schema, the model-facing surface of the planner.
TOOLS_VERSION = tools_version(PLANNER_TOOLS, [PlanProposal])


class PlannerState(AgentState):
    run_id: int


def build_planner(deps: "GraphDeps") -> CompiledStateGraph:
    """The planner agent. Its model is only a placeholder: CallLayerMiddleware swaps in the
    model of each attempt that `LLMClient.run_task` makes."""
    config = deps.client.config
    task = config.tasks[deps.task]
    placeholder = build_chat_model(config.models[task.primary], task.timeout_seconds)
    return create_agent(
        model=placeholder,
        tools=PLANNER_TOOLS,
        system_prompt=load_prompt("planner.md"),
        middleware=[
            CallLayerMiddleware(deps.client, deps.task, deps.recorder),
            TokenBudgetMiddleware(PLANNER_INPUT_TOKEN_BUDGET, deps.recorder),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT),
            ToolCallLimitMiddleware(run_limit=TOOL_CALL_LIMIT),
        ],
        response_format=ToolStrategy(PlanProposal),
        context_schema=PlannerDeps,
        state_schema=PlannerState,
    )
