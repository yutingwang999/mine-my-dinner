"""The planner: an inner agent that calls the planning tools and proposes one meal."""

from typing import TYPE_CHECKING

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentState,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from minemydinner.agent.budget import TokenBudgetMiddleware
from minemydinner.agent.middleware import CallLayerMiddleware
from minemydinner.agent.picker import PickerAnswer
from minemydinner.agent.request import load_prompt
from minemydinner.agent.revision import RevisionProposal
from minemydinner.llm.registry import build_chat_model
from minemydinner.planning.tools import (
    PLANNER_TOOLS,
    PlannerDeps,
    search_recipes_tool,
    tools_version,
)

if TYPE_CHECKING:
    from minemydinner.agent.graph import GraphDeps

# The most input tokens one planner model call may carry; a call that reaches it ends the
# attempt before the next call (TokenBudgetMiddleware).
PLANNER_INPUT_TOKEN_BUDGET = 32000
# Per attempt. With the shortlist in the opening a typical plan takes one model call.
MODEL_CALL_LIMIT = 6
TOOL_CALL_LIMIT = 10
# The picker (plan 3b-2 spec 5.7), per attempt: at most two search calls, then its answer.
PICK_SEARCH_LIMIT = 2
PICK_MODEL_CALL_LIMIT = PICK_SEARCH_LIMIT + 1
# The picker only searches; code checks stock, cookware, time and rules of its dishes.
PICK_TOOLS: list[BaseTool] = [search_recipes_tool]


class PlanProposal(BaseModel):
    """The planner's final answer: one meal from the user's recipe library."""

    recipe_ids: list[int] = Field(description="Ids of the chosen recipes, from tool results only.")
    note: str = Field(description="One or two sentences to the user, in the reply locale.")
    wish_met: bool | None = Field(
        default=None,
        description=(
            "Whether the chosen dishes meet the request's taste wish (the line 'The user "
            "feels like: ...'). None when the request has no such line."
        ),
    )
    wish_unmet_reason: str = Field(
        default="",
        description="When wish_met is false: a short reason why no dish fits the wish.",
    )


# Covers the tools and the final-answer schema, the model-facing surface of the planner.
TOOLS_VERSION = tools_version(PLANNER_TOOLS, [PlanProposal])
# The revise step's surface: the same tools with its own final-answer schema.
REVISE_TOOLS_VERSION = tools_version(PLANNER_TOOLS, [RevisionProposal])
# The picker's surface: the search tool and its answer schema.
PICK_TOOLS_VERSION = tools_version(PICK_TOOLS, [PickerAnswer])


class PlannerState(AgentState):
    run_id: int


def _build_agent(
    deps: "GraphDeps",
    prompt: str,
    answer: type[BaseModel],
    step: str,
    *,
    tools: list[BaseTool] = PLANNER_TOOLS,
    model_calls: int = MODEL_CALL_LIMIT,
    tool_calls: int = TOOL_CALL_LIMIT,
):
    """The planner's agent setup with `prompt`, final-answer schema `answer`, `tools` and the
    per-attempt call limits. Its model is only a placeholder: CallLayerMiddleware swaps in the
    model of each attempt that `LLMClient.run_task` makes."""
    config = deps.client.config
    task = config.tasks[deps.task]
    placeholder = build_chat_model(config.models[task.primary], task.timeout_seconds)
    return create_agent(
        model=placeholder,
        tools=tools,
        system_prompt=prompt,
        middleware=[
            CallLayerMiddleware(deps.client, deps.task, deps.recorder, step=step),
            TokenBudgetMiddleware(PLANNER_INPUT_TOKEN_BUDGET, deps.recorder),
            ModelCallLimitMiddleware(run_limit=model_calls),
            ToolCallLimitMiddleware(run_limit=tool_calls),
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


def build_picker(deps: "GraphDeps", prompt: str | None = None) -> CompiledStateGraph:
    """The dish picker (plan 3b-2 spec 5.5, 5.7): the planner's setup with the search tool only,
    at most `PICK_SEARCH_LIMIT` searches and `PICK_MODEL_CALL_LIMIT` model calls per attempt,
    and a `PickerAnswer`. `prompt` defaults to the current "pick" prompt, loaded only here."""
    text = prompt if prompt is not None else load_prompt("pick")
    return _build_agent(
        deps,
        text,
        PickerAnswer,
        "pick",
        tools=PICK_TOOLS,
        model_calls=PICK_MODEL_CALL_LIMIT,
        tool_calls=PICK_SEARCH_LIMIT,
    )
