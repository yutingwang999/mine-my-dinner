"""Chat API: threads, streamed planning turns, and resumable questions.

The graph and its checkpointer are synchronous, so the stream is a sync generator that
Starlette iterates in a threadpool.
"""

import json
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field

from minemydinner.agent.diners import CardOutOfDate, DinerAnswer
from minemydinner.agent.edits import EditError, newest_reply_id
from minemydinner.agent.graph import GraphDeps, build_graph
from minemydinner.agent.plan_card import client_card
from minemydinner.agent.records import PlanningRecorder
from minemydinner.api.deps import ApiError, get_llm_client
from minemydinner.db import SessionFactory
from minemydinner.llm.client import AllModelsFailed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat")

SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}

# Top-level graph nodes and the progress step each one reports when it finishes.
NODE_STEPS = {
    "parse_request": "parse",
    "load_context": "context",
    "check_feasibility": "context",
    "validate_plan": "check",
    "present_plan": "present",
    # The revise step (plan 3b-1) reuses the planner's steps.
    "start_revision": "context",
    "check_revised": "check",
    "present_revision": "present",
}
# The nodes whose update carries `retries` when they send the run back for another attempt.
RETRY_NODES = ("validate_plan", "check_revised")
# Planner tool names and the step each one reports.
TOOL_STEPS = {
    "get_inventory": "context",
    "get_preferences": "context",
    "search_recipes": "search",
    "get_recipe": "search",
    "compute_missing": "check",
    "check_cookware": "check",
    "validate_plan": "check",
}
# The `token` event is reserved: this slice never emits it, because its model calls are
# structured output or the planner's tool loop. Streaming it later needs the "messages"
# stream mode.


@dataclass
class ChatRuntime:
    graph: CompiledStateGraph
    recorder: PlanningRecorder


def get_chat_runtime(request: Request) -> ChatRuntime:
    """The app's graph, built once on the app's checkpointer."""
    runtime = getattr(request.app.state, "chat_runtime", None)
    if runtime is None:
        recorder = PlanningRecorder(SessionFactory)
        deps = GraphDeps(SessionFactory, get_llm_client(), recorder)
        runtime = ChatRuntime(build_graph(deps, request.app.state.checkpointer), recorder)
        request.app.state.chat_runtime = runtime
    return runtime


RuntimeDep = Annotated[ChatRuntime, Depends(get_chat_runtime)]


class MessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    locale: Literal["zh", "en"]


class ResumeBody(BaseModel):
    # Text or null answers a question (null skips it); the diner card answers with an object.
    value: Annotated[str, Field(max_length=4000)] | DinerAnswer | None = None
    locale: Literal["zh", "en"]


def _config(thread_id: str) -> dict[str, Any]:
    try:
        parsed = uuid.UUID(thread_id)
    except ValueError:
        raise ApiError(404, "thread_not_found") from None
    return {"configurable": {"thread_id": str(parsed)}}


def _snapshot(runtime: ChatRuntime, thread_id: str):
    """The thread's state, or 404 when it has no checkpoint."""
    config = _config(thread_id)
    state = runtime.graph.get_state(config)
    if not state.config["configurable"].get("checkpoint_id"):
        raise ApiError(404, "thread_not_found")
    return config, state


def _pending(state) -> list:
    return list(state.interrupts)


def edit_refusal(state) -> str | None:
    """Why the newest plan card cannot be edited now, or None when it can: `no_plan` when no
    reply has a plan (a new thread, too, whose first step is still ahead), `thread_busy`
    while a run or a question is pending (the saved state's next steps and interrupts tell,
    so a failed or disconnected run counts until the next message)."""
    try:
        newest_reply_id(state.values)
    except EditError as error:
        return error.code
    if state.next or _pending(state):
        return "thread_busy"
    return None


def event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _visible_messages(messages: list) -> list[dict[str, str]]:
    shown = []
    for message in messages:
        if not isinstance(message.content, str) or not message.content:
            continue
        if isinstance(message, HumanMessage):
            shown.append({"role": "user", "text": message.content})
        elif isinstance(message, AIMessage) and not message.tool_calls:
            shown.append({"role": "assistant", "text": message.content})
    return shown


def _items(values: dict) -> list[dict[str, Any]]:
    """The conversation in order for display: messages, each reply's plan, and the questions.

    Old checkpoints lack reply_plans and questions, so both read as empty.
    """
    messages = values.get("messages") or []
    reply_plans = values.get("reply_plans") or {}
    questions = list(values.get("questions") or [])
    items: list[dict[str, Any]] = []
    next_question = 0

    def add_questions_through(count: int) -> None:
        nonlocal next_question
        while next_question < len(questions) and questions[next_question]["at"] <= count:
            items.append({"type": "question", "value": questions[next_question]["value"]})
            next_question += 1

    for index, message in enumerate(messages):
        add_questions_through(index)
        if not isinstance(message.content, str):
            continue
        if isinstance(message, HumanMessage) and message.content:
            items.append({"type": "user", "text": message.content})
        elif isinstance(message, AIMessage) and not message.tool_calls:
            plan = client_card(reply_plans.get(message.id)) if message.id else None
            if message.content or plan is not None:
                items.append(
                    {"type": "assistant", "id": message.id, "text": message.content, "plan": plan}
                )
    add_questions_through(len(messages))
    return items


def _steps_for(namespace: tuple, node: str, update: Any) -> list[str]:
    if not isinstance(update, dict):
        return []
    if namespace:
        # Inside the planner: its tool calls show what it is doing.
        steps = []
        for message in update.get("messages") or []:
            for call in getattr(message, "tool_calls", None) or []:
                step = TOOL_STEPS.get(call["name"])
                if step and step not in steps:
                    steps.append(step)
        return steps
    steps = [NODE_STEPS[node]] if node in NODE_STEPS else []
    if node in RETRY_NODES and "retries" in update:
        steps.append("retry")
    return steps


def _events_for_update(namespace: tuple, node: str, update: Any) -> Iterator[str]:
    for step in _steps_for(namespace, node, update):
        yield event("progress", {"step": step})
    if namespace or not isinstance(update, dict):
        return
    # Each reply's card follows the reply, from `reply_plans`: a revision that leaves the plan
    # unchanged writes its card there only, not to `current_plan`.
    reply_plans = update.get("reply_plans") or {}
    for message in update.get("messages") or []:
        if not isinstance(message, AIMessage) or message.tool_calls:
            continue
        if message.content:
            yield event("message", {"role": "assistant", "text": message.content})
        if message.id is not None and message.id in reply_plans:
            yield event("plan", client_card(reply_plans[message.id]))


def _fail_record(runtime: ChatRuntime, config: dict) -> None:
    """Finish the planning record of an interrupted run, if it has one."""
    try:
        values = runtime.graph.get_state(config).values
        if values.get("run_id") is not None:
            runtime.recorder.fail(values["run_id"], values.get("retries") or 0)
    except Exception:
        logger.exception("Finishing the planning record failed")


def _stream(runtime: ChatRuntime, graph_input: Any, config: dict) -> Iterator[str]:
    # False until the run ends normally or its failure is handled. A client that
    # disconnects closes this generator (GeneratorExit at a yield); the finally block then
    # closes the planning record, and nothing is yielded afterwards.
    settled = False
    try:
        for namespace, _mode, data in runtime.graph.stream(
            graph_input, config, stream_mode=["updates"], subgraphs=True
        ):
            for node, update in data.items():
                yield from _events_for_update(namespace, node, update)
        pending = _pending(runtime.graph.get_state(config))
        settled = True
        if pending:
            yield event("interrupt", pending[0].value)
    except AllModelsFailed:
        settled = True
        _fail_record(runtime, config)
        yield event("error", {"code": "models_unavailable"})
    except CardOutOfDate:
        # The diner card names someone or something deleted since it was shown.
        settled = True
        _fail_record(runtime, config)
        yield event("error", {"code": "card_out_of_date"})
    except Exception:
        settled = True
        logger.exception("Chat stream failed")
        _fail_record(runtime, config)
        yield event("error", {"code": "internal"})
    finally:
        if not settled:
            _fail_record(runtime, config)
    yield event("done", {})


def _response(stream: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/threads")
def create_thread(runtime: RuntimeDep) -> dict[str, str]:
    thread_id = str(uuid.uuid4())
    # An initial checkpoint, so the thread exists before its first message.
    runtime.graph.update_state(
        _config(thread_id), {"messages": [], "locale": "en"}, as_node="__start__"
    )
    return {"thread_id": thread_id}


@router.post("/threads/{thread_id}/messages")
def post_message(thread_id: str, body: MessageBody, runtime: RuntimeDep) -> StreamingResponse:
    config, _ = _snapshot(runtime, thread_id)
    graph_input = {"messages": [HumanMessage(body.text)], "locale": body.locale}
    return _response(_stream(runtime, graph_input, config))


@router.post("/threads/{thread_id}/resume")
def resume(thread_id: str, body: ResumeBody, runtime: RuntimeDep) -> StreamingResponse:
    config, state = _snapshot(runtime, thread_id)
    pending = _pending(state)
    if not pending:
        raise ApiError(409, "nothing_pending")
    value = body.value
    # The diner card takes an answer object or null; every other question text or null.
    card = isinstance(pending[0].value, dict) and pending[0].value.get("kind") == "diners"
    if value is not None and card != isinstance(value, DinerAnswer):
        raise ApiError(422, "wrong_answer_type")
    if isinstance(value, DinerAnswer):
        if value.headcount_too_low:
            # The card's total cannot be below the people ticked and added on it.
            raise ApiError(422, "headcount_too_low")
        value = value.model_dump(mode="json")
    # LangGraph ignores a bare resume of None, so answer by interrupt id (None means skip).
    command = Command(resume={pending[0].id: value}, update={"locale": body.locale})
    return _response(_stream(runtime, command, config))


@router.get("/threads/{thread_id}")
def read_thread(thread_id: str, runtime: RuntimeDep) -> dict[str, Any]:
    _, state = _snapshot(runtime, thread_id)
    pending = _pending(state)
    return {
        "items": _items(state.values),
        "messages": _visible_messages(state.values.get("messages") or []),
        # Cards reach the client without their server-only fields (the request's labels).
        "current_plan": client_card(state.values.get("current_plan")),
        "pending": pending[0].value if pending else None,
        # Whether the newest plan card takes edits now: the edit endpoints' own rule.
        "editable": edit_refusal(state) is None,
    }
