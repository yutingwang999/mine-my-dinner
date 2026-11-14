from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from minemydinner.models.base import Base


class PlanningRun(Base):
    """One planning attempt, kept locally for evaluation. Never shown in the UI."""

    __tablename__ = "planning_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    tools_version: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    defaults_used: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    events: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    retries: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    first_passed: Mapped[bool | None] = mapped_column(Boolean)
    final_passed: Mapped[bool | None] = mapped_column(Boolean)
    final_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    has_temporary_guests: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class AppPreference(Base):
    __tablename__ = "app_preferences"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    # A JSON null is a stored value; the column itself is never SQL NULL.
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
