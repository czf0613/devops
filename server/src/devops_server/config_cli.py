"""Run with python -m devops_server.config_cli; secret input never uses command arguments."""

import argparse
import io
import json
import os
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values
from dotenv.parser import parse_stream
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from devops_server.config import DEVOPS_ROOT, ConnectionSettings, get_connections
from devops_server.config_store import (
    RUNTIME_KEYS,
    SECRET_KEYS,
    ConfigurationError,
    initialize_config,
    load_settings,
    settings_values,
    update_configuration,
)
from devops_server.database import build_engine

CONNECTION_KEYS = frozenset(name.upper() for name in ConnectionSettings.model_fields)


def migrate_env(engine, env_path: Path) -> None:
    snapshot = env_path.read_text() if env_path.exists() else ""
    if any(binding.error for binding in parse_stream(io.StringIO(snapshot))):
        raise ConfigurationError("Invalid .env syntax; file left unchanged")
    parsed = dotenv_values(stream=io.StringIO(snapshot), interpolate=False)
    if parsed.keys() - CONNECTION_KEYS - RUNTIME_KEYS:
        raise ConfigurationError("Unknown .env keys; file left unchanged")
    legacy = {key: value for key, value in parsed.items() if key in RUNTIME_KEYS}
    initialize_config(engine, legacy)
    if not legacy:
        return
    if env_path.read_text() != snapshot:
        raise ConfigurationError(".env changed during migration; file left unchanged")
    lines = ["# Connection settings only. Runtime configuration is stored in PostgreSQL."]
    for key, value in parsed.items():
        if key in CONNECTION_KEYS:
            escaped = (value or "").replace("\\", "\\\\").replace("'", "\\'")
            lines.append(f"{key}='{escaped}'")
    descriptor, temporary = tempfile.mkstemp(dir=env_path.parent, prefix=".env.")
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        if env_path.read_text() != snapshot:
            raise ConfigurationError(".env changed during migration; file left unchanged")
        os.replace(temporary, env_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize development config or import legacy .env; idempotent")
    sub.add_parser("list", help="List runtime config with secrets redacted")
    setter = sub.add_parser(
        "set", help="Read a JSON value from stdin, or an object for multiple keys"
    )
    setter.add_argument("key", nargs="?", help="Omit to update several keys atomically")
    args = parser.parse_args()
    engine = None
    try:
        engine = build_engine(get_connections())
        if args.command == "init":
            migrate_env(engine, DEVOPS_ROOT / ".env")
            print("Shared config initialized and verified; .env contains connection settings only.")
        elif args.command == "list":
            values = settings_values(load_settings(engine))
            print(
                json.dumps(
                    {
                        key: "<redacted>" if key in SECRET_KEYS else value
                        for key, value in values.items()
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            if sys.stdin.isatty():
                raise ConfigurationError(
                    "Pipe JSON on stdin; do not put secret values in arguments"
                )
            value = json.load(sys.stdin)
            updates = {args.key.upper(): value} if args.key else value
            if not isinstance(updates, dict) or not updates:
                raise ConfigurationError("Supply a nonempty JSON object of configuration values")
            update_configuration(engine, updates)
            print("Configuration saved. Restart every server instance to apply it.")
    except ConfigurationError as error:
        parser.exit(1, f"Configuration error: {error}\n")
    except ValidationError:
        parser.exit(1, "Invalid configuration values; no changes saved.\n")
    except SQLAlchemyError, OSError, ValueError:
        parser.exit(1, "Configuration operation failed; check connections and migrations.\n")
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
