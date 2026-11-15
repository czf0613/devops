from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from devops_server.config import ConnectionSettings


def build_engine(settings: ConnectionSettings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        # Managed PostgreSQL may default to a local session timezone; keep every
        # connection on UTC so ORM datetimes match the application's UTC clocks.
        connect_args={"connect_timeout": 3, "options": "-c timezone=UTC"},
    )
