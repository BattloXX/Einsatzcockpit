"""DIBOS EventHub: Einsaetze anlegen (analog LIS/IPR)

- org_dibos_config.create_incidents: Org-Opt-in, unabhaengig von enrich_incidents
  aktivierbar (siehe app/services/dibos/dibos_enrich.py::_get_or_create_incident_for_event()).
- incident.lis_operation_number bekommt eine Unique-Constraint
  (primary_org_id, lis_operation_number) — bisher nur ein normaler, nicht
  erzwungener Wert (Tier-2-Matching in find_matching_incident()). Ohne
  DB-seitige Eindeutigkeit koennte ein Race zwischen LIS-Sync und der neuen
  DIBOS-Erstellung fuer dieselbe Leitstellennummer zwei Einsaetze anlegen —
  identisches Muster wie 0176_incident_lis_operation_id_unique.py fuer
  lis_operation_id. Vor dem Anlegen der Constraint werden etwaige BEREITS
  bestehende Duplikate (moeglicherweise genau die Art doppelt angelegter
  Einsaetze, die dieser Plan beheben soll) defensiv aufgeloest: die
  lis_operation_number wird nur auf dem aeltesten Einsatz je Duplikat-Gruppe
  belassen, bei juengeren Duplikaten auf NULL gesetzt (kein Loeschen/
  Zusammenfuehren von Einsatzdaten — das bleibt eine manuelle Entscheidung).

Revision ID: 0182
Revises: 0181
Create Date: 2026-07-26
"""
from sqlalchemy import text

from alembic import op

revision = "0182"
down_revision = "0181"
branch_labels = None
depends_on = None


def _index_exists(conn, table: str, index_name: str) -> bool:
    r = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
    ), {"t": table, "i": index_name})
    return (r.scalar() or 0) > 0


def _resolve_existing_duplicates(conn) -> None:
    """Setzt lis_operation_number=NULL auf allen ausser dem aeltesten Einsatz je
    (primary_org_id, lis_operation_number)-Duplikat-Gruppe (Muster: 0176)."""
    dupes = conn.execute(text("""
        SELECT primary_org_id, lis_operation_number
        FROM incident
        WHERE lis_operation_number IS NOT NULL
        GROUP BY primary_org_id, lis_operation_number
        HAVING COUNT(*) > 1
    """)).fetchall()
    for org_id, op_number in dupes:
        rows = conn.execute(text("""
            SELECT id FROM incident
            WHERE primary_org_id = :org_id AND lis_operation_number = :op_number
            ORDER BY created_at ASC, id ASC
        """), {"org_id": org_id, "op_number": op_number}).fetchall()
        keeper_id = rows[0][0]
        loser_ids = [int(r[0]) for r in rows[1:]]
        if not loser_ids:
            continue
        id_list = ",".join(str(i) for i in loser_ids)  # aus DB gelesene Integer-IDs, kein User-Input
        conn.execute(text(f"UPDATE incident SET lis_operation_number = NULL WHERE id IN ({id_list})"))
        print(  # noqa: T201 — sichtbar im Migrations-Log, kein Logger im Alembic-Kontext verfuegbar
            f"0182: Duplikat lis_operation_number={op_number!r} (Org {org_id}) — "
            f"Einsatz {keeper_id} behaelt die Nummer, {loser_ids} entkoppelt "
            f"(Einsatzdaten bleiben unveraendert, nur die Leitstellennummer wurde entfernt)."
        )


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE `org_dibos_config` ADD COLUMN `create_incidents` "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    ))

    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return

    _resolve_existing_duplicates(conn)

    if not _index_exists(conn, "incident", "uq_incident_org_lis_operation_number"):
        conn.execute(text(
            "ALTER TABLE `incident` ADD CONSTRAINT `uq_incident_org_lis_operation_number`"
            " UNIQUE (`primary_org_id`, `lis_operation_number`)"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "mysql" and _index_exists(
        conn, "incident", "uq_incident_org_lis_operation_number"
    ):
        conn.execute(text(
            "ALTER TABLE `incident` DROP INDEX `uq_incident_org_lis_operation_number`"
        ))
    op.execute(text("ALTER TABLE `org_dibos_config` DROP COLUMN `create_incidents`"))
