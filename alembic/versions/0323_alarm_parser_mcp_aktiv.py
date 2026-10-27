"""Separater Opt-in fuer Alarm-Parser-Ausgaben an MCP-Clients."""
import sqlalchemy as sa

from alembic import op

revision = "0323"
down_revision = "0322"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.add_column(sa.Column("alarm_parser_mcp_aktiv", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("alarm_parser_mcp_aktiv")
