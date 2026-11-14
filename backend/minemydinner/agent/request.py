"""The request-style preference: ask first, or plan then adjust."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from minemydinner.models import AppPreference

REQUEST_STYLE_KEY = "request_style"
REQUEST_STYLES = ("ask_first", "plan_then_adjust")


def get_request_style(session: Session) -> str | None:
    row = session.get(AppPreference, REQUEST_STYLE_KEY)
    return row.value if row else None


def set_request_style(session: Session, value: str | None) -> None:
    if value is not None and value not in REQUEST_STYLES:
        raise ValueError(f"Unknown request style {value}")
    statement = insert(AppPreference).values(key=REQUEST_STYLE_KEY, value=value)
    # A JSON null must be stored as JSON, not as SQL NULL.
    statement = statement.on_conflict_do_update(
        index_elements=["key"], set_={"value": statement.excluded.value}
    )
    session.execute(statement)
    session.commit()
