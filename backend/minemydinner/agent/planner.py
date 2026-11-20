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
from minemydinner.agent.revision import RevisionProposal
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
# The revise step's surface: the same tools with its own final-answer schema.
REVISE_TOOLS_VERSION = tools_version(PLANNER_TOOLS, [RevisionProposal])


class PlannerState(AgentState):
    run_id: int


def _build_agent(deps: "GraphDeps", prompt: str, answer: type[BaseModel], step: str):
    """The planner's agent setup with `prompt` and final-answer schema `answer`. Its model is
    only a placeholder: CallLayerMiddleware swaps in the model of each attempt that
    `LLMClient.run_task` makes."""
    config = deps.client.config
    task = config.tasks[deps.task]
    placeholder = build_chat_model(config.models[task.primary], task.timeout_seconds)
    return create_agent(
        model=placeholder,
        tools=PLANNER_TOOLS,
        system_prompt=prompt,
        middleware=[
            CallLayerMiddleware(deps.client, deps.task, deps.recorder, step=step),
            TokenBudgetMiddleware(PLANNER_INPUT_TOKEN_BUDGET, deps.recorder),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT),
            ToolCallLimitMiddleware(run_limit=TOOL_CALL_LIMIT),
        ],
        response_format=ToolStrategy(answer),
        context_schema=PlannerDeps,
        state_schema=PlannerState,
    )


def build_planner(deps: "GraphDeps") -> CompiledStateGraph:
    """The planner agent: proposes one meal from scratch."""
    return _build_agent(deps, load_prompt("planner"), PlanProposal, "planner")


def build_reviser(deps: "GraphDeps") -> CompiledStateGraph:
    """The revise step's agent (plan 3b-1 spec 5.4): the planner's setup, tools and limits,
    with the revise prompt and a change list as its answer."""
    return _build_agent(deps, load_prompt("revise"), RevisionProposal, "revise")
