"""Accept Docker repository path separators in Registry namespace names."""

import sqlalchemy as sa
from alembic import op

revision = "0020_registry_namespace_format"
down_revision = "0019_ci"
branch_labels = None
depends_on = None

DOCKER_COMPONENT = r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*$"
LEGACY_NAMESPACE = r"^[a-z0-9]+(-[a-z0-9]+)*$"


def upgrade():
    op.drop_constraint(
        op.f("ck_registry_namespaces_name_format"), "registry_namespaces", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_registry_namespaces_name_format"),
        "registry_namespaces",
        f"name ~ '{DOCKER_COMPONENT}' AND length(name) <= 63",
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(f"SELECT count(*) FROM registry_namespaces WHERE name !~ '{LEGACY_NAMESPACE}'")
    ):
        raise RuntimeError("Cannot downgrade Registry namespaces that use Docker path separators")
    op.drop_constraint(
        op.f("ck_registry_namespaces_name_format"), "registry_namespaces", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_registry_namespaces_name_format"),
        "registry_namespaces",
        f"name ~ '{LEGACY_NAMESPACE}' AND length(name) <= 63",
    )
