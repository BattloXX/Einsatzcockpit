"""Ereignisschluessel und Versandbeginn fuer persistente Alarmauftraege."""
import hashlib
import logging

import sqlalchemy as sa

from alembic import op

revision = "0330"
down_revision = "0329"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)
_BATCH_GROESSE = 500


def upgrade() -> None:
    with op.batch_alter_table("incident_alarm_job") as batch:
        batch.add_column(sa.Column("event_key", sa.String(160), nullable=True))
        batch.add_column(sa.Column("send_started_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("may_have_sent", sa.Boolean(), nullable=False, server_default=sa.false()))
    bind = op.get_bind()
    auftrag = sa.table(
        "incident_alarm_job", sa.column("id", sa.BigInteger()), sa.column("org_id", sa.BigInteger()),
        sa.column("incident_id", sa.BigInteger()), sa.column("channel", sa.String(16)),
        sa.column("variant", sa.String(40)), sa.column("event_key", sa.String(160)),
    )
    einsatz = sa.table("incident", sa.column("id", sa.BigInteger()), sa.column("lis_operation_number", sa.String()))
    letzte_id = 0
    while True:
        zeilen = bind.execute(sa.select(
            auftrag.c.id, auftrag.c.org_id, auftrag.c.incident_id, auftrag.c.channel,
            auftrag.c.variant, einsatz.c.lis_operation_number,
        ).select_from(auftrag.outerjoin(einsatz, auftrag.c.incident_id == einsatz.c.id))
            .where(auftrag.c.id > letzte_id).order_by(auftrag.c.id).limit(_BATCH_GROESSE)).all()
        if not zeilen:
            break
        werte = []
        for zeile in zeilen:
            nummer = (zeile.lis_operation_number or "").strip().lower() or f"inc{zeile.incident_id}"
            schluessel = f"{zeile.org_id or 0}:{nummer}:{zeile.channel}:{zeile.variant}"
            if len(schluessel) > 160:
                nummer = hashlib.sha1(nummer.encode()).hexdigest()
                schluessel = f"{zeile.org_id or 0}:{nummer}:{zeile.channel}:{zeile.variant}"
            werte.append((zeile.id, zeile.org_id, schluessel))
        vorhanden = set(bind.execute(sa.select(auftrag.c.org_id, auftrag.c.event_key).where(
            auftrag.c.event_key.in_([schluessel for _, _, schluessel in werte]),
        )).all())
        for job_id, org_id, schluessel in werte:
            if (org_id, schluessel) in vorhanden:
                logger.warning("Doppelter Alarm-Ereignisschluessel beim Backfill (Job %s, Org %s)", job_id, org_id)
                continue  # Spaetere Duplikate behalten NULL, auch bei fehlender Organisation.
            vorhanden.add((org_id, schluessel))
            bind.execute(auftrag.update().where(auftrag.c.id == job_id).values(event_key=schluessel))
        letzte_id = zeilen[-1].id
    op.create_index("uq_incident_alarm_job_org_event_key", "incident_alarm_job", ["org_id", "event_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_incident_alarm_job_org_event_key", table_name="incident_alarm_job")
    with op.batch_alter_table("incident_alarm_job") as batch:
        batch.drop_column("may_have_sent")
        batch.drop_column("send_started_at")
        batch.drop_column("event_key")
