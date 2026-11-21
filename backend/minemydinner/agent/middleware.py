"""The call-layer middleware for the planner agent.

Every planner model call goes through `LLMClient.run_task`, so it gets failover, the
monthly-cap rule, an `llm_calls` row, and the text hooks. Planning records keep what was
sent to the model (after the pre-hooks); the agent state keeps the restored form.
"""

import json
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import BaseModel

from minemydinner.agent.records import PlanningRecorder
from minemydinner.llm.client import LLMClient, map_strings, normalize_usage, served_model_name
from minemydinner.llm.config import ModelConfig


def _map_content(content: str | list, transform: Callable[[str], str]) -> str | list:
    if isinstance(content, str):
        return transform(content)
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(transform(part))
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append({**part, "text": transform(part["text"])})
        else:
            parts.append(part)
    return parts


def _tool_calls(message: AIMessage) -> list[dict[str, Any]]:
    return [{"name": c["name"], "args": c["args"], "id": c["id"]} for c in message.tool_calls]


def dump_message(message: BaseMessage) -> dict[str, Any]:
    """A JSON-safe view of one message for the planning record."""
    item: dict[str, Any] = {"type": message.type, "content": message.content}
    if isinstance(message, AIMessage) and message.tool_calls:
        item["tool_calls"] = _tool_calls(message)
    if isinstance(message, ToolMessage):
        item.update(tool_call_id=message.tool_call_id, name=message.name, status=message.status)
    return item


def _usage(response: ModelResponse) -> dict[str, int] | None:
    for message in reversed(response.result):
        if isinstance(message, AIMessage):
            return normalize_usage(message.usage_metadata)
    return None


def _json_or_text(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except ValueError:
            return content
    return content


class CallLayerMiddleware(AgentMiddleware):
    """Sends each planner model call through `LLMClient` and records calls and tool use
    under the run's `run_id` (from the agent state)."""

    def __init__(
        self, client: LLMClient, task: str, recorder: PlanningRecorder, step: str = "planner"
    ) -> None:
        super().__init__()
        self.client = client
        self.task = task
        self.recorder = recorder
        # The `step` of each recorded model call: "planner", or "revise" for the revise step.
        self.step = step

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        messages = self.client.prepare_messages(request.messages)
        # The system prompt is fixed text and is sent and recorded as written.
        system = request.system_message
        used: dict[str, str] = {}

        def attempt(
            cfg: ModelConfig, model: BaseChatModel
        ) -> tuple[ModelResponse, dict[str, int] | None]:
            response = handler(
                request.override(
                    model=model,
                    messages=messages,
                    system_message=system,
                    model_settings={**request.model_settings, **cfg.tool_call_options},
                )
            )
            if isinstance(response, AIMessage):
                response = ModelResponse(result=[response])
            used["provider"] = cfg.provider
            return response, _usage(response)

        outcome = self.client.run_task(self.task, attempt)
        response = outcome.value
        run_id = request.state.get("run_id")
        if run_id is not None:
            sent = ([system] if system is not None else []) + messages
            reply = next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)
            self.recorder.add_event(
                run_id,
                {
                    "kind": "model_call",
                    "step": self.step,
                    "model_name": outcome.model_name,
                    "provider": used["provider"],
                    "fallback_used": outcome.fallback_used,
                    # The model name the provider reports having served, if it reports one.
                    "served_model": served_model_name(reply),
                    "messages": [dump_message(m) for m in sent],
                    "response": {
                        "content": reply.content if reply else None,
                        "tool_calls": _tool_calls(reply) if reply else [],
                    },
                },
            )
        return self._restore(response)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = handler(request)
        state = request.state if isinstance(request.state, dict) else {}
        run_id = state.get("run_id")
        if run_id is None or not isinstance(result, ToolMessage):
            return result
        call = request.tool_call
        # Records keep the form the model saw: arguments as it sent them, results replaced.
        event: dict[str, Any] = {
            "name": call["name"],
            "args": map_strings(call["args"], self.client.prepare_text),
        }
        payload = _json_or_text(result.content)
        if result.status == "error":
            # The tool never ran: unknown tool or arguments that failed validation.
            event["kind"] = "invalid_tool_call"
            event["error"] = map_strings(result.content, self.client.prepare_text)
        elif isinstance(payload, dict) and "error" in payload:
            # The tool ran and returned an error payload, such as tool_failed or not_found.
            event["kind"] = "invalid_tool_call"
            event["error"] = map_strings(payload["error"], self.client.prepare_text)
        else:
            event["kind"] = "tool_call"
            event["result"] = map_strings(payload, self.client.prepare_text)
        self.recorder.add_event(run_id, event)
        return result

    def _restore(self, response: ModelResponse) -> ModelResponse:
        restore = self.client.restore_text
        result: list[BaseMessage] = []
        for message in response.result:
            if isinstance(message, AIMessage):
                calls = [{**c, "args": map_strings(c["args"], restore)} for c in message.tool_calls]
                message = message.model_copy(
                    update={"content": _map_content(message.content, restore), "tool_calls": calls}
                )
            elif isinstance(message, ToolMessage):
                message = message.model_copy(
                    update={"content": _map_content(message.content, restore)}
                )
            result.append(message)
        structured = response.structured_response
        if isinstance(structured, BaseModel):
            structured = type(structured).model_validate(
                map_strings(structured.model_dump(), restore)
            )
        return ModelResponse(result=result, structured_response=structured)
