"""WordPress-Berichtskonfiguration und Synchronisationsstatus.

Revision ID: 0195
Revises: 0194
"""
import sqlalchemy as sa

from alembic import op

revision = "0195"
down_revision = "0194"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "wordpress_report_config",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("webhook_url", sa.String(length=500), nullable=True),
        sa.Column("webhook_token", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id"),
    )
    op.add_column("incident", sa.Column("wp_report_post_id", sa.Integer(), nullable=True))
    op.add_column("incident", sa.Column("wp_report_edit_url", sa.String(length=500), nullable=True))
    op.add_column("alarm_type", sa.Column("wp_einsatzart", sa.String(length=30), nullable=True))


def downgrade():
    op.drop_column("alarm_type", "wp_einsatzart")
    op.drop_column("incident", "wp_report_edit_url")
    op.drop_column("incident", "wp_report_post_id")
    op.drop_table("wordpress_report_config")
