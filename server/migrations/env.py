from logging.config import fileConfig

from alembic import context

from devops_server.config import get_connections
from devops_server.database import build_engine
from devops_server.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def configure_connection(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=get_connections().database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif (connection := config.attributes.get("connection")) is not None:
    configure_connection(connection)
else:
    engine = build_engine(get_connections())
    try:
        with engine.connect() as connection:
            configure_connection(connection)
    finally:
        engine.dispose()
