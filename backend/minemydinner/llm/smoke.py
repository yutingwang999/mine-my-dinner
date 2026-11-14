"""Make one small real call to each configured model. Costs a fraction of a cent."""

import argparse
import base64
import struct
import sys
import zlib
from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel
from sqlalchemy import select

from minemydinner.db import SessionFactory, configure_engine
from minemydinner.llm.client import Attempt, AttemptError, LLMClient, normalize_usage
from minemydinner.llm.config import LLMConfig, ModelConfig, load_llm_config
from minemydinner.llm.registry import KEY_VARIABLES
from minemydinner.models import LlmCall
from minemydinner.settings import get_settings

TOOL_PROMPT = "What time is it in Paris? Use the tool."
VISION_PROMPT = "What color is this image? Answer with one word."
VISION_MODEL_NAME = "deepseek"

PROMPT = "Reply as JSON with the single field answer set to the word ok."


class SmokeReply(BaseModel):
    answer: str


@tool
def get_time(city: str) -> str:
    """Return the current time in a city."""
    return "12:00"


def solid_red_png(size: int = 8) -> bytes:
    """A solid red size x size PNG, built in code so no file is needed."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + b"\xff\x00\x00" * size
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )


def red_image_message() -> HumanMessage:
    encoded = base64.b64encode(solid_red_png()).decode("ascii")
    return HumanMessage(
        [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
        ]
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", action="store_true", help="check tool calling for planning")
    parser.add_argument(
        "--deepseek-vision", action="store_true", help="send a tiny PNG to deepseek-flash"
    )
    return parser.parse_args(argv)


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


def _run_check(label: str, client: LLMClient, attempt: Attempt[str]) -> bool:
    """Run one check through LLMClient, so it is logged and counted like any call."""
    try:
        outcome = client.run_task("smoke", attempt)
    except Exception as error:
        _report(label, False, _failure_detail(error))
        return False
    _report(label, True, f"reply={outcome.value}")
    return True


def check_tools(config: LLMConfig) -> bool:
    """Bind one trivial tool for each planning candidate and require a tool call back."""
    ok = True
    for name in config.tasks["planning"].candidates:
        client = _client_for_chat_model(config, name)

        def attempt(
            model: ModelConfig, chat_model: BaseChatModel
        ) -> tuple[str, dict[str, int] | None]:
            bound = chat_model.bind_tools([get_time], **model.tool_call_options)
            reply = bound.invoke([HumanMessage(TOOL_PROMPT)])
            if not reply.tool_calls:
                raise AttemptError("no tool call returned", normalize_usage(reply.usage_metadata))
            return reply.tool_calls[0]["name"], normalize_usage(reply.usage_metadata)

        ok = _run_check(f"tools[{name}]", client, attempt) and ok
    return ok


def check_deepseek_vision(config: LLMConfig) -> bool:
    """Send a solid red square to deepseek-flash; success means the reply says red."""
    client = _client_for_chat_model(config, VISION_MODEL_NAME)

    def attempt(
        model: ModelConfig, chat_model: BaseChatModel
    ) -> tuple[str, dict[str, int] | None]:
        reply = chat_model.invoke([red_image_message()])
        text = str(reply.content)
        if "red" not in text.lower():
            raise AttemptError(f"reply does not say red: {text[:40]!r}", normalize_usage(reply.usage_metadata))
        return repr(text[:40]), normalize_usage(reply.usage_metadata)

    return _run_check("deepseek_vision", client, attempt)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
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
    if args.tools:
        failed = not check_tools(config) or failed
    if args.deepseek_vision:
        failed = not check_deepseek_vision(config) or failed
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
