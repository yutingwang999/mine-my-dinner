from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


@contextmanager
def open_checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    """A PostgresSaver on its own connection pool, closed when the block ends.

    The pool uses the connection settings PostgresSaver requires. `setup()` creates the
    checkpoint tables if they are missing and is safe to repeat.
    """
    pool = ConnectionPool(
        conninfo=database_url.replace("+psycopg", "", 1),
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    pool.open(wait=True)
    try:
        saver = PostgresSaver(pool)
        saver.setup()
        yield saver
    finally:
        pool.close()
