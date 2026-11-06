import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from minemydinner.api import dictionary, imports, recipes
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
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    engine = configure_engine(get_settings().database_url)
    # A thread, so a slow provider never delays startup.
    threading.Thread(target=_retry_embeddings_in_background, daemon=True).start()
    yield
    engine.dispose()


app = FastAPI(title="MineMyDinner", lifespan=lifespan, exception_handlers=ERROR_HANDLERS)
app.include_router(imports.router)
app.include_router(recipes.router)
app.include_router(dictionary.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
