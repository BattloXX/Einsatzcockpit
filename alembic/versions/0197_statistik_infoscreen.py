"""Statistik-Infoscreen

Revision ID: 0197
Revises: 0196
"""
import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision = "0197"
down_revision = "0196"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.create_table(
            "statistik_dashboard_token",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("label", sa.String(150), nullable=False),
            sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
        )
        with op.batch_alter_table("org_settings") as batch_op:
            batch_op.add_column(sa.Column("statistik_infoscreen_enabled", sa.Boolean(), nullable=False,
                                          server_default=sa.false()))
        return
    op.execute(text(
        "CREATE TABLE `statistik_dashboard_token` ("
        "`id` BIGINT NOT NULL AUTO_INCREMENT, `token_hash` VARCHAR(64) NOT NULL, "
        "`label` VARCHAR(150) NOT NULL, `org_id` BIGINT NOT NULL, "
        "`created_at` DATETIME NOT NULL, `last_used_at` DATETIME NULL, "
        "PRIMARY KEY (`id`), UNIQUE KEY `uq_statistik_dashboard_token_hash` (`token_hash`), "
        "CONSTRAINT `fk_statistik_dashboard_token_org` FOREIGN KEY (`org_id`) "
        "REFERENCES `fire_dept` (`id`) ON DELETE CASCADE)"
    ))
    op.execute(text(
        "ALTER TABLE `org_settings` ADD COLUMN `statistik_infoscreen_enabled` "
        "TINYINT(1) NOT NULL DEFAULT 0"
    ))


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("org_settings") as batch_op:
            batch_op.drop_column("statistik_infoscreen_enabled")
        op.drop_table("statistik_dashboard_token")
        return
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN `statistik_infoscreen_enabled`"))
    op.execute(text("DROP TABLE `statistik_dashboard_token`"))
