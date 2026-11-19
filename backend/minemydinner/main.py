import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from minemydinner.agent.checkpoint import open_checkpointer
from minemydinner.api import (
    chat,
    diners,
    dictionary,
    imports,
    inventory,
    plan_edits,
    preferences,
    recipes,
)
from minemydinner.api.deps import ERROR_HANDLERS, get_llm_client
from minemydinner.db import SessionFactory, configure_engine
from minemydinner.recipes.embedding import retry_pending_embeddings
from minemydinner.settings import get_settings

logger = logging.getLogger(__name__)


def _retry_embeddings_in_background() -> None:
    """Embed recipes left without one. Errors are logged so startup still succeeds."""
    try:
        with SessionFactory() as session:
            retry_pending_embeddings(session, get_llm_client())
    except Exception:
        logger.exception("Retrying pending embeddings failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database_url = get_settings().database_url
    engine = configure_engine(database_url)
    # A thread, so a slow provider never delays startup.
    threading.Thread(target=_retry_embeddings_in_background, daemon=True).start()
    # The chat endpoint builds its graph on this saver.
    with open_checkpointer(database_url) as saver:
        app.state.checkpointer = saver
        yield
    engine.dispose()


app = FastAPI(title="MineMyDinner", lifespan=lifespan, exception_handlers=ERROR_HANDLERS)
app.include_router(imports.router)
app.include_router(recipes.router)
app.include_router(recipes.images_router)
app.include_router(dictionary.router)
app.include_router(inventory.router)
app.include_router(diners.router)
app.include_router(preferences.router)
app.include_router(chat.router)
app.include_router(plan_edits.router)


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def host_name(host_header: str) -> str:
    """The host without its port. Handles the IPv6 form `[::1]:8000`."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[: host.find("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


@app.middleware("http")
async def check_host_and_origin(request: Request, call_next):
    """Reject unexpected Host values (DNS rebinding) and cross-site writes.

    Requests without an Origin header (scripts, tests) pass the origin check.
    """
    settings = get_settings()
    if host_name(request.headers.get("host", "")) not in settings.allowed_hosts:
        return JSONResponse({"code": "bad_host"}, status_code=400)
    origin = request.headers.get("origin")
    if (
        origin is not None
        and request.method in WRITE_METHODS
        and origin not in settings.allowed_origins
    ):
        return JSONResponse({"code": "forbidden_origin"}, status_code=403)
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
