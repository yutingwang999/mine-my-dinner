"""SPIKE (branch spike/one-reader, evaluation only): one model reads the whole follow-up turn and
decides the new plan itself, in place of the route, parse and revise calls. Enabled only when
`GraphDeps.one_reader_task` is set (the planning evaluation's `--one-reader` flag); the app
never sets it, so the normal path is unchanged. Not production code."""

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from minemydinner.agent.budget import TokenBudgetMiddleware
from minemydinner.agent.middleware import CallLayerMiddleware
from minemydinner.agent.planner import PLANNER_INPUT_TOKEN_BUDGET, PlannerState
from minemydinner.llm.registry import build_chat_model
from minemydinner.planning.tools import PlannerDeps, search_recipes_tool

if TYPE_CHECKING:
    from minemydinner.agent.graph import GraphDeps

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "one_reader_v0.md"
# At most 3 searches per call; after that the tools are blocked and the model must answer.
TOOL_CALL_LIMIT = 3
MODEL_CALL_LIMIT = 5
# The reader's answer kind as the evaluation's route expectation.
ROUTE_OF_ANSWER = {
    "plan": "revise",
    "nothing_fits": "revise",
    "new_meal": "plan",
    "question": "unsure",
}


class OneReaderAnswer(BaseModel):
    """The one-reader's decision for this follow-up turn."""

    kind: Literal["plan", "question", "new_meal", "nothing_fits"] = Field(
        description=(
            "plan: the new dish list for this meal. question: one short question back to the "
            "user. new_meal: the user wants a different meal planned from scratch. "
            "nothing_fits: nothing in the library can do what was asked."
        )
    )
    recipe_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Only for plan: the FULL new dish list of this meal (kept dishes and new ones), as "
            "recipe ids from the current plan, the candidate list or search results. Empty "
            "for every other kind."
        ),
    )
    message: str = Field(
        default="",
        description=(
            "In the reply locale, for the user. plan: one or two sentences about the change. "
            "question: the question. nothing_fits: a short reason. new_meal: empty. Never "
            "internal words (LOCKED, CONFLICT, Diner N) or recipe ids."
        ),
    )


def load_one_reader_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_one_reader(deps: "GraphDeps"):
    """The reader agent: the search tool only, capped, on the reader's own task."""
    task_name = deps.one_reader_task
    config = deps.client.config
    task = config.tasks[task_name]
    placeholder = build_chat_model(config.models[task.primary], task.timeout_seconds)
    return create_agent(
        model=placeholder,
        tools=[search_recipes_tool],
        system_prompt=load_one_reader_prompt(),
        middleware=[
            CallLayerMiddleware(deps.client, task_name, deps.recorder, step="one_reader"),
            TokenBudgetMiddleware(PLANNER_INPUT_TOKEN_BUDGET, deps.recorder),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT),
            ToolCallLimitMiddleware(run_limit=TOOL_CALL_LIMIT),
        ],
        response_format=ToolStrategy(OneReaderAnswer),
        context_schema=PlannerDeps,
        state_schema=PlannerState,
    )


def conversation_text(messages: list) -> str:
    """The whole conversation before the newest user message, as plain lines. Saved
    nicknames are replaced by the client's pre-hooks when the text is sent."""
    lines = []
    for message in messages:
        if not isinstance(message.content, str) or not message.content.strip():
            continue
        if isinstance(message, HumanMessage):
            lines.append(f"User: {message.content}")
        elif isinstance(message, AIMessage):
            lines.append(f"Assistant: {message.content}")
    return "\n".join(lines) or "(nothing before this turn)"


def reader_input(
    request_block: str,
    who_eats: str,
    plan_text: str,
    history: str,
    newest: str,
    button: bool,
) -> str:
    """The reader's one input: candidates and request conditions (with diners' rules), who
    eats, the current plan, the conversation, then the newest message."""
    if button:
        said = (
            f"The user pressed the button: {newest}\n"
            "(The request conditions above already include this button's change.)"
        )
    else:
        said = f"The user's newest message: {newest}"
    return (
        f"{request_block}\n{who_eats}\n\n{plan_text}\n\n"
        f"Conversation so far (oldest first):\n{history}\n\n{said}"
    )
