"""Engine/session factory. The only place a database URL is read."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from fhrs.core.config import Settings, get_settings
from fhrs.core.models import Base


def make_engine(settings: Settings | None = None) -> Engine:
    s = settings or get_settings()
    return create_engine(s.database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_schema(engine: Engine) -> None:
    """Create all tables. For tests and the sqlite demo; production Postgres runs
    alembic migrations (same metadata, one source of truth)."""
    Base.metadata.create_all(engine)
