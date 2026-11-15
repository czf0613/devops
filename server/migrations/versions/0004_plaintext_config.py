"""Unwrap legacy configuration once; runtime no longer needs a config master key."""

import base64
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_plaintext_config"
down_revision = "0003_shared_config"
branch_labels = None
depends_on = None

SECRET_KEYS = ("PASSWORD_CIPHER_KEYS", "AUTH_HMAC_KEY", "SMTP_PASSWORD")
configuration = sa.table(
    "config",
    sa.column("id", sa.BigInteger),
    sa.column("key", sa.Text),
    sa.column("value", JSONB),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended('devops:config', 0))")
    )
    rows = (
        connection.execute(
            sa.select(configuration).where(configuration.c.key.in_(SECRET_KEYS)).with_for_update()
        )
        .mappings()
        .all()
    )
    encrypted = [
        row
        for row in rows
        if isinstance(row["value"], dict) and {"version", "ciphertext"} <= row["value"].keys()
    ]
    if not encrypted:
        return  # Fresh databases and already converted values never read a key file.

    override = op.get_context().config.attributes.get("legacy_config_key_path")
    mounted = Path("/run/secrets/devops_config_key")
    path = (
        Path(override)
        if override
        else (
            mounted
            if mounted.exists()
            else Path(__file__).resolve().parents[3] / ".secrets/config.key"
        )
    )
    try:
        master = base64.b64decode(path.read_text().strip(), validate=True)
        if len(master) != 32:
            raise ValueError()
    except OSError, ValueError:
        raise RuntimeError("Legacy config migration requires its original key file") from None

    converted = []
    for row in encrypted:
        try:
            envelope = row["value"]
            if envelope["version"] != 1:
                raise ValueError()
            payload = base64.b64decode(envelope["ciphertext"], validate=True)
            value = json.loads(
                AESGCM(master).decrypt(
                    payload[:12], payload[12:], f"devops:config:v1:{row['key']}".encode()
                )
            )
            if row["key"] == "PASSWORD_CIPHER_KEYS":
                if not isinstance(value, dict) or not all(
                    isinstance(item, str) for item in value.values()
                ):
                    raise ValueError()
            elif not isinstance(value, str):
                raise ValueError()
        except InvalidTag, ValueError, KeyError, TypeError:
            raise RuntimeError("Cannot decrypt legacy config; no values were converted") from None
        converted.append((row["id"], value))

    # Decode every value before making any writes. Preserve actual AES/HMAC/SMTP values.
    for entry_id, value in converted:
        connection.execute(
            configuration.update()
            .where(configuration.c.id == entry_id)
            .values(value=value, updated_at=sa.func.now())
        )


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.scalar(
            sa.select(configuration.c.id).where(configuration.c.key.in_(SECRET_KEYS)).limit(1)
        )
        is not None
    ):
        raise RuntimeError(
            "Cannot downgrade populated plaintext config to the retired encryption format"
        )
