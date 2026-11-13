from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

SessionFactory = sessionmaker(expire_on_commit=False)


def configure_engine(database_url: str) -> Engine:
    engine = create_engine(database_url)
    SessionFactory.configure(bind=engine)
    return engine


def get_session() -> Iterator[Session]:
    with SessionFactory() as session:
        yield session
