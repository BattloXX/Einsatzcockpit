"""Varianten fuer Nachalarmierung und deren persistente Versandprotokolle."""
import sqlalchemy as sa

from alembic import op

revision = "0322"
down_revision = "0321"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MariaDB darf den bisherigen UNIQUE-Index nicht entfernen, solange er
    # noch den FK auf incident stuetzt. Ein eigener Index muss vorher bestehen.
    bind = op.get_bind()
    if op.get_context().as_sql:
        op.create_index("ix_incident_alarm_job_variant_fk", "incident_alarm_job", ["incident_id"])
    elif not any(index["column_names"] == ["incident_id"]
                 for index in sa.inspect(bind).get_indexes("incident_alarm_job")):
        op.create_index("ix_incident_alarm_job_variant_fk", "incident_alarm_job", ["incident_id"])
    with op.batch_alter_table("incident_alarm_job") as batch:
        batch.add_column(sa.Column("variant", sa.String(40), nullable=False, server_default=""))
        batch.drop_constraint("uq_incident_alarm_job_incident_channel", type_="unique")
        batch.create_unique_constraint("uq_incident_alarm_job_incident_channel_variant",
                                       ["incident_id", "channel", "variant"])
    # Beide Dispatcher suchen bisher nur nach incident_id. Varianten muessen
    # auch dort persistent sein, sonst gilt der Erstalarm als bereits gesendet.
    for table in ("sms_log", "mail_einsatzinfo_log"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("variant", sa.String(40), nullable=False, server_default=""))


def downgrade() -> None:
    # Nachalarme duerfen beim Downgrade nicht stillschweigend geloescht werden.
    # Bei mehreren Varianten muss der Betreiber diese vorher archivieren.
    with op.batch_alter_table("incident_alarm_job") as batch:
        batch.drop_constraint("uq_incident_alarm_job_incident_channel_variant", type_="unique")
        batch.create_unique_constraint("uq_incident_alarm_job_incident_channel", ["incident_id", "channel"])
        batch.drop_column("variant")
    for table in ("sms_log", "mail_einsatzinfo_log"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("variant")
