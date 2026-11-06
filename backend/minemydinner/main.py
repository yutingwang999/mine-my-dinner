from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from minemydinner.api import imports
from minemydinner.api.deps import ERROR_HANDLERS
from minemydinner.db import configure_engine
from minemydinner.settings import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    engine = configure_engine(get_settings().database_url)
    yield
    engine.dispose()


app = FastAPI(title="MineMyDinner", lifespan=lifespan, exception_handlers=ERROR_HANDLERS)
app.include_router(imports.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
