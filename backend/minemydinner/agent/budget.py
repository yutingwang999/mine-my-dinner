"""The input token budget for one planner attempt (design 5.7).

After each model call, the input tokens the provider reported for that call are compared
with the budget. When the call reached it and the model asked for tools, the attempt ends
before the next model call, as when the call limit is hit: the run returns without a
proposal and validation fails it. Nothing is trimmed or summarized, so the history the
provider has cached is never rewritten. The stop is noted in the planning record only
(a "budget_stop" event), never in the message history.
"""

from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from minemydinner.agent.records import PlanningRecorder
from minemydinner.llm.client import normalize_usage


class TokenBudgetMiddleware(AgentMiddleware):
    """Ends the planner run once one model call's input reached `budget` tokens."""

    def __init__(self, budget: int, recorder: PlanningRecorder | None = None) -> None:
        super().__init__()
        self.budget = budget
        self.recorder = recorder

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: AgentState, runtime: Runtime | None) -> dict[str, Any] | None:
        # A final answer from this call is kept, whatever its size.
        if state.get("structured_response") is not None:
            return None
        last = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
        if last is None or not last.tool_calls:
            return None
        usage = normalize_usage(last.usage_metadata)
        if usage is None or usage["input_tokens"] < self.budget:
            return None
        run_id = state.get("run_id")
        if self.recorder is not None and run_id is not None:
            self.recorder.add_event(
                run_id, {"kind": "budget_stop", "input_tokens": usage["input_tokens"]}
            )
        # Only the jump: no message is added and no earlier message is touched.
        return {"jump_to": "end"}
