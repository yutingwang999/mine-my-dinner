from alembic import context
from sqlalchemy import create_engine

from minemydinner.models import Base
from minemydinner.settings import get_settings

# Tests pass their database through `sqlalchemy.url`, so they never load `.env`.
url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url

# The Postgres checkpointer creates and migrates these tables itself, so Alembic leaves them alone.
CHECKPOINT_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name in CHECKPOINT_TABLES:
        return False
    if type_ == "index" and obj.table.name in CHECKPOINT_TABLES:
        return False
    return True


engine = create_engine(url)
with engine.connect() as connection:
    context.configure(
        connection=connection, target_metadata=Base.metadata, include_object=include_object
    )
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
