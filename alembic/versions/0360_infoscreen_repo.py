"""Repo-Versionen ohne Artefakte und begrenzte Entdeckung."""
import sqlalchemy as sa
from alembic import op

revision = "0360"
down_revision = "0359"
branch_labels = None
depends_on = None

ARTIFACTS = {"sha256": sa.String(64), "signature": sa.String(255),
             "size": sa.BigInteger(), "storage_path": sa.String(500)}
DEVICE = {"arch": sa.String(16), "last_update_check_at": sa.DateTime(),
          "last_update_check_reason": sa.String(16), "release_discovery_day": sa.Date()}


def upgrade() -> None:
    # Nur die globale Release-Tabelle wird unter SQLite neu aufgebaut; keine eingehenden FKs.
    with op.batch_alter_table("infoscreen_release") as batch:
        for name, typ in ARTIFACTS.items():
            batch.alter_column(name, existing_type=typ, nullable=True)
    for name, typ in DEVICE.items():
        op.add_column("infoscreen_device", sa.Column(name, typ, nullable=True))
    op.add_column("infoscreen_device", sa.Column("release_discovery_count", sa.Integer,
                                                nullable=False, server_default="0"))
    table = op.create_table("infoscreen_release_discovery_lock",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("generation", sa.Integer, nullable=False, server_default="0"))
    op.bulk_insert(table, [{"id": 1, "generation": 0}])


def downgrade() -> None:
    # Metadatenzeilen haben im alten Schema keine Darstellung; echte Artefakte bleiben erhalten.
    op.execute("DELETE FROM infoscreen_release WHERE storage_path IS NULL OR sha256 IS NULL "
               "OR signature IS NULL OR size IS NULL")
    with op.batch_alter_table("infoscreen_release") as batch:
        for name, typ in ARTIFACTS.items():
            batch.alter_column(name, existing_type=typ, nullable=False)
    for name in (*DEVICE, "release_discovery_count"):
        op.drop_column("infoscreen_device", name)
    op.drop_table("infoscreen_release_discovery_lock")
