"""Incident.lis_operation_id: nicht-eindeutigen Index durch Unique-Constraint ersetzen

Bisher (0114_lis_integration.py) nur ein normaler Index auf
(primary_org_id, lis_operation_id) — die Eindeutigkeit wurde ausschließlich in
_get_or_link_incident() (lis_sync.py) durchgesetzt und war damit anfällig für
Races zwischen dem Hintergrund-Poll (lis_loop.py) und einem parallel laufenden
manuellen Sync ("Verbindung testen"-Button) oder Backfill (Vorfall 2026-07-21,
doppelt angelegter Einsatz f26006436). NULL-Werte kollidieren in MySQL/MariaDB
nicht miteinander (jede NULL zählt einzeln als eindeutig), daher keine
Sonderbehandlung für Einsätze ohne LIS-Anbindung nötig.

Vor dem Anlegen der Unique-Constraint werden etwaige BEREITS bestehende
Duplikate defensiv aufgelöst: die lis_operation_id wird nur auf dem älteren
(zuerst angelegten) Einsatz je Duplikat-Gruppe belassen, bei allen jüngeren
Duplikaten auf NULL gesetzt (kein Löschen/Zusammenführen von Einsatzdaten —
das bleibt eine manuelle Entscheidung; nur die LIS-Verknüpfung wird entfernt,
damit diese Einsätze nicht weiter fälschlich als "dieselbe Operation" gelten
und die neue Constraint überhaupt angelegt werden kann).

Revision ID: 0176
Revises: 0175
Create Date: 2026-07-21
"""
from sqlalchemy import text

from alembic import op

revision = "0176"
down_revision = "0175"
branch_labels = None
depends_on = None


def _index_exists(conn, table: str, index_name: str) -> bool:
    r = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
    ), {"t": table, "i": index_name})
    return (r.scalar() or 0) > 0


def _resolve_existing_duplicates(conn) -> None:
    """Setzt lis_operation_id=NULL auf allen außer dem ältesten Einsatz je
    (primary_org_id, lis_operation_id)-Duplikat-Gruppe."""
    dupes = conn.execute(text("""
        SELECT primary_org_id, lis_operation_id
        FROM incident
        WHERE lis_operation_id IS NOT NULL
        GROUP BY primary_org_id, lis_operation_id
        HAVING COUNT(*) > 1
    """)).fetchall()
    for org_id, op_id in dupes:
        rows = conn.execute(text("""
            SELECT id FROM incident
            WHERE primary_org_id = :org_id AND lis_operation_id = :op_id
            ORDER BY created_at ASC, id ASC
        """), {"org_id": org_id, "op_id": op_id}).fetchall()
        keeper_id = rows[0][0]
        loser_ids = [int(r[0]) for r in rows[1:]]
        if not loser_ids:
            continue
        id_list = ",".join(str(i) for i in loser_ids)  # aus DB gelesene Integer-IDs, kein User-Input
        conn.execute(text(f"UPDATE incident SET lis_operation_id = NULL WHERE id IN ({id_list})"))
        print(  # noqa: T201 — sichtbar im Migrations-Log, kein Logger im Alembic-Kontext verfuegbar
            f"0176: Duplikat lis_operation_id={op_id!r} (Org {org_id}) — "
            f"Einsatz {keeper_id} behaelt die Verknuepfung, {loser_ids} entkoppelt "
            f"(Einsatzdaten bleiben unveraendert, nur die LIS-Verknuepfung wurde entfernt)."
        )


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return

    _resolve_existing_duplicates(conn)

    if _index_exists(conn, "incident", "ix_incident_org_lis_op"):
        conn.execute(text("DROP INDEX `ix_incident_org_lis_op` ON `incident`"))
    if not _index_exists(conn, "incident", "uq_incident_org_lis_operation_id"):
        conn.execute(text(
            "ALTER TABLE `incident` ADD CONSTRAINT `uq_incident_org_lis_operation_id`"
            " UNIQUE (`primary_org_id`, `lis_operation_id`)"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return

    if _index_exists(conn, "incident", "uq_incident_org_lis_operation_id"):
        conn.execute(text("ALTER TABLE `incident` DROP INDEX `uq_incident_org_lis_operation_id`"))
    if not _index_exists(conn, "incident", "ix_incident_org_lis_op"):
        conn.execute(text(
            "CREATE INDEX `ix_incident_org_lis_op` ON `incident` (`primary_org_id`, `lis_operation_id`)"
        ))
