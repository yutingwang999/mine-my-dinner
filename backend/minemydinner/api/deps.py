from functools import lru_cache
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from minemydinner.db import SessionFactory
from minemydinner.llm.client import AllModelsFailed, LLMClient, MonthlyCapExceeded
from minemydinner.llm.config import load_llm_config
from minemydinner.settings import get_settings


class ApiError(Exception):
    """An error the UI can explain. `suggest_paste` asks the user to paste the text."""

    def __init__(
        self,
        status_code: int,
        code: str,
        suggest_paste: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.suggest_paste = suggest_paste
        self.extra = extra or {}


def _response(
    status_code: int, code: str, suggest_paste: bool = False, extra: dict[str, Any] | None = None
) -> JSONResponse:
    body = {"code": code, "suggest_paste": suggest_paste, **(extra or {})}
    return JSONResponse(body, status_code=status_code)


async def handle_api_error(_: Request, error: ApiError) -> JSONResponse:
    return _response(error.status_code, error.code, error.suggest_paste, error.extra)


async def handle_monthly_cap(_: Request, __: MonthlyCapExceeded) -> JSONResponse:
    return _response(429, "monthly_cap")


async def handle_models_unavailable(_: Request, __: AllModelsFailed) -> JSONResponse:
    return _response(503, "models_unavailable")


ERROR_HANDLERS = {
    ApiError: handle_api_error,
    MonthlyCapExceeded: handle_monthly_cap,
    AllModelsFailed: handle_models_unavailable,
}


def contains_pattern(q: str) -> str:
    """An ILIKE pattern matching q anywhere, with %, _, and backslash taken literally."""
    escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    config = load_llm_config(model_overrides=settings.model_overrides())
    return LLMClient(config, SessionFactory, settings.llm_monthly_cap_usd)
