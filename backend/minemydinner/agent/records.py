"""Local planning records. Never shown in the UI; kept when a conversation is deleted."""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import cast

from minemydinner.models import PlanningRun


class PlanningRecorder:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def start(
        self,
        thread_id: str,
        request: dict[str, Any],
        defaults_used: list[str],
        prompt_version: str,
        tools_version: str,
        has_temporary_guests: bool,
    ) -> int:
        row = PlanningRun(
            thread_id=thread_id,
            request=request,
            defaults_used=defaults_used,
            prompt_version=prompt_version,
            tools_version=tools_version,
            has_temporary_guests=has_temporary_guests,
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            return row.id

    def add_event(self, run_id: int, event: dict[str, Any]) -> None:
        """Append one event in a transaction of its own, so a later rollback keeps it."""
        statement = (
            update(PlanningRun)
            .where(PlanningRun.id == run_id)
            .values(events=PlanningRun.events.op("||")(cast([event], JSONB)))
        )
        with self.session_factory() as session:
            session.execute(statement)
            session.commit()

    def finish(
        self,
        run_id: int,
        retries: int,
        first_passed: bool,
        final_passed: bool,
        final_plan: dict[str, Any] | None,
    ) -> None:
        statement = (
            update(PlanningRun)
            .where(PlanningRun.id == run_id)
            .values(
                retries=retries,
                first_passed=first_passed,
                final_passed=final_passed,
                final_plan=final_plan,
            )
        )
        with self.session_factory() as session:
            session.execute(statement)
            session.commit()

    def fail(self, run_id: int, retries: int) -> None:
        """Close a run that stopped early. Rows already finished are left alone."""
        statement = (
            update(PlanningRun)
            .where(PlanningRun.id == run_id, PlanningRun.final_passed.is_(None))
            .values(retries=retries, final_passed=False, final_plan=None)
        )
        with self.session_factory() as session:
            session.execute(statement)
            session.commit()


def load_run(session, run_id: int) -> PlanningRun:
    return session.scalars(select(PlanningRun).where(PlanningRun.id == run_id)).one()
