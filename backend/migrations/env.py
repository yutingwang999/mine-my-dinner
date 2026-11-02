from alembic import context
from sqlalchemy import create_engine

from minemydinner.models import Base
from minemydinner.settings import get_settings

# Tests pass their database through `sqlalchemy.url`, so they never load `.env`.
url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url

engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
