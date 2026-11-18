import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from devops_server.config import ConnectionSettings


def build_engine(settings: ConnectionSettings) -> Engine:
    # Keep UTC while preserving libpq PGOPTIONS used by local integration
    # harnesses to select an isolated schema.
    options = " ".join(filter(None, (os.environ.get("PGOPTIONS"), "-c timezone=UTC")))
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3, "options": options},
    )
