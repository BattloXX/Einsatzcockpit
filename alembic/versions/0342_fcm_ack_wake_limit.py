"""FCM-Aktivitaet und persistente Einsatz-Wake-Zuordnung."""
import sqlalchemy as sa

from alembic import op

revision = "0342"
down_revision = "0341"
branch_labels = None
depends_on = None


def _fk_typ(tabelle: str) -> sa.types.TypeEngine:
    # MariaDB verlangt dieselbe Groesse und Signedness wie beim referenzierten PK.
    if not op.get_context().as_sql:
        for spalte in sa.inspect(op.get_bind()).get_columns(tabelle):
            if spalte["name"] == "id":
                return spalte["type"]
    return sa.BigInteger()


def upgrade() -> None:
    op.add_column("fcm_token", sa.Column("last_ack_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("push_log") as batch:
        batch.add_column(sa.Column("incident_id", _fk_typ("incident"), nullable=True))
        batch.create_foreign_key("fk_push_log_incident", "incident", ["incident_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_push_log_incident_id", ["incident_id"])


def downgrade() -> None:
    with op.batch_alter_table("push_log") as batch:
        batch.drop_index("ix_push_log_incident_id")
        batch.drop_constraint("fk_push_log_incident", type_="foreignkey")
        batch.drop_column("incident_id")
    op.drop_column("fcm_token", "last_ack_at")
