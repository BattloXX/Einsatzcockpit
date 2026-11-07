"""Release-Verteilung und Update-Policy des Infoscreen-Agents."""
import sqlalchemy as sa
from alembic import op

revision = "0358"
down_revision = "0357"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite erzwingt VARCHAR-Laengen nicht. Kein Table-Rebuild: DROP TABLE
    # wuerde bei aktiven FKs bestehende Displays per CASCADE entfernen.
    if op.get_bind().dialect.name != "sqlite":
        for table, name in (("infoscreen_device", "agent_version"), ("infoscreen_device", "update_pinned_version"),
                            ("infoscreen_release", "version"), ("infoscreen_release", "min_version")):
            op.alter_column(table, name, existing_type=sa.String(50), type_=sa.String(100),
                            existing_nullable=name != "version")
    for name, typ in (("update_status", sa.String(16)), ("update_error_code", sa.String(32)),
                      ("update_reported_at", sa.DateTime)):
        op.add_column("infoscreen_device", sa.Column(name, typ, nullable=True))
    op.add_column("infoscreen_release", sa.Column("key_id", sa.String(100), nullable=False, server_default=""))
    op.add_column("infoscreen_release", sa.Column("critical", sa.Boolean, nullable=False, server_default="0"))
    for name, size in (("installer_path", 500), ("installer_sha256", 64), ("installer_signature", 255)):
        op.add_column("infoscreen_release", sa.Column(name, sa.String(size), nullable=True))
    for name, default in (("infoscreen_update_start", "02:00"), ("infoscreen_update_end", "04:30")):
        op.add_column("org_settings", sa.Column(name, sa.String(5), nullable=False, server_default=default))
    op.add_column("org_settings", sa.Column("infoscreen_critical_allowed", sa.Boolean, nullable=False, server_default="0"))
    # Keine neuen FKs: bestehende INT-/BIGINT-Verknuepfungen bleiben unveraendert.
    op.create_table("infoscreen_release_upload_token",
                    sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
                    sa.Column("created_at", sa.DateTime, nullable=False),
                    sa.Column("revoked_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_table("infoscreen_release_upload_token")
    for name in ("infoscreen_update_start", "infoscreen_update_end", "infoscreen_critical_allowed"):
        op.drop_column("org_settings", name)
    for name in ("key_id", "critical", "installer_path", "installer_sha256", "installer_signature"):
        op.drop_column("infoscreen_release", name)
    for name in ("update_status", "update_error_code", "update_reported_at"):
        op.drop_column("infoscreen_device", name)
    if op.get_bind().dialect.name != "sqlite":
        for table, name in (("infoscreen_device", "agent_version"), ("infoscreen_device", "update_pinned_version"),
                            ("infoscreen_release", "version"), ("infoscreen_release", "min_version")):
            op.alter_column(table, name, existing_type=sa.String(100), type_=sa.String(50),
                            existing_nullable=name != "version")
