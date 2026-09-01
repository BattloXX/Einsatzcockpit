"""Scopes fuer API-Keys. Revision 0229."""
import sqlalchemy as sa
from alembic import op

revision = "0229"
down_revision = "0228"
branch_labels = None
depends_on = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _spalten(tabelle: str) -> set[str]:
    if tabelle not in _tabellen():
        return set()
    return {spalte["name"] for spalte in sa.inspect(op.get_bind()).get_columns(tabelle)}


def upgrade() -> None:
    if "scopes" not in _spalten("api_key"):
        with op.batch_alter_table("api_key") as batch:
            batch.add_column(sa.Column(
                "scopes", sa.String(200), nullable=False,
                server_default="einsatz:write,mailing:import",
            ))


def downgrade() -> None:
    if "scopes" in _spalten("api_key"):
        with op.batch_alter_table("api_key") as batch:
            batch.drop_column("scopes")
