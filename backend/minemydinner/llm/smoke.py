"""Make one small real call to each configured model. Costs a fraction of a cent."""

import sys

from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sqlalchemy import select

from minemydinner.db import SessionFactory, configure_engine
from minemydinner.llm.client import LLMClient
from minemydinner.llm.config import LLMConfig, load_llm_config
from minemydinner.llm.registry import KEY_VARIABLES
from minemydinner.models import LlmCall
from minemydinner.settings import get_settings

PROMPT = "Reply as JSON with the single field answer set to the word ok."


class SmokeReply(BaseModel):
    answer: str


def _scrub(text: str) -> str:
    """Remove key values, because provider errors can quote the key they received."""
    settings = get_settings()
    for variable in KEY_VARIABLES.values():
        key = getattr(settings, variable.lower())
        if key is not None and key.get_secret_value():
            text = text.replace(key.get_secret_value(), "[key removed]")
    return text


def _report(name: str, success: bool, detail: str = "") -> None:
    with SessionFactory() as session:
        call = session.scalars(select(LlmCall).order_by(LlmCall.id.desc()).limit(1)).first()
    print(
        f"{name}: provider={call.provider} model={call.model} success={success} "
        f"latency_ms={call.latency_ms} cost_usd={call.cost_estimate} {detail}".rstrip()
    )


def _failure_detail(error: Exception) -> str:
    cause = error.__cause__ or error
    return _scrub(f"error={type(cause).__name__}: {cause}")


def _client_for_chat_model(config: LLMConfig, name: str) -> LLMClient:
    smoke_task = config.tasks["smoke"].model_copy(update={"primary": name, "fallback": None})
    tasks = {**config.tasks, "smoke": smoke_task}
    return LLMClient(
        config.model_copy(update={"tasks": tasks}),
        SessionFactory,
        get_settings().llm_monthly_cap_usd,
    )


def main() -> int:
    settings = get_settings()
    configure_engine(settings.database_url)
    config = load_llm_config(model_overrides=settings.model_overrides())
    failed = False
    for name, model in config.models.items():
        if "embeddings" in model.capabilities:
            continue
        client = _client_for_chat_model(config, name)
        try:
            client.invoke_structured("smoke", [HumanMessage(PROMPT)], SmokeReply)
            _report(name, True)
        except Exception as error:
            failed = True
            _report(name, False, _failure_detail(error))

    name = config.tasks["embeddings"].primary
    client = LLMClient(config, SessionFactory, settings.llm_monthly_cap_usd)
    try:
        vectors = client.embed(["番茄炒蛋"])
        _report(name, True, f"vector_length={len(vectors[0])}")
    except Exception as error:
        failed = True
        _report(name, False, _failure_detail(error))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
