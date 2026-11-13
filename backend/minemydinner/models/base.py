from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def in_list(column: str, values: tuple[str, ...]) -> str:
    """Build a CHECK expression limiting a text column to fixed values."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def array_literal(values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"ARRAY[{quoted}]::text[]"
