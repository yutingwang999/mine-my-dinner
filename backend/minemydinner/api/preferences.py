"""App preferences."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from minemydinner.agent.request import get_request_style, set_request_style
from minemydinner.db import get_session

router = APIRouter(prefix="/api/preferences")

SessionDep = Annotated[Session, Depends(get_session)]


class RequestStyle(BaseModel):
    value: Literal["ask_first", "plan_then_adjust"] | None


@router.get("/request-style")
def read_request_style(session: SessionDep) -> RequestStyle:
    return RequestStyle(value=get_request_style(session))


@router.put("/request-style")
def write_request_style(body: RequestStyle, session: SessionDep) -> RequestStyle:
    set_request_style(session, body.value)
    return RequestStyle(value=get_request_style(session))
