"""Teams-Alarmkarte: nur eine Karte je Grossschadenslage

- teams_alarm_config.suppress_card_in_major_incident: Org-Opt-in, Default aus.
- teams_card_post.major_incident_id (nullable FK auf major_incident) + Unique-
  Constraint (org_id, major_incident_id) — Dedup-Ledger fuer
  post_incident_card(): pro laufender Lage darf hoechstens eine Karte-mit-
  gesetztem-major_incident_id protokolliert werden. NULL zaehlt je einzeln als
  eindeutig (Einsaetze ausserhalb einer Lage bleiben unberuehrt).

Revision ID: 0183
Revises: 0182
Create Date: 2026-07-26
"""
from sqlalchemy import text

from alembic import op

revision = "0183"
down_revision = "0182"
branch_labels = None
depends_on = None


def _index_exists(conn, table: str, index_name: str) -> bool:
    r = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
    ), {"t": table, "i": index_name})
    return (r.scalar() or 0) > 0


def _column_exists(conn, table: str, column: str) -> bool:
    r = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {"t": table, "c": column})
    return (r.scalar() or 0) > 0


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE `teams_alarm_config` ADD COLUMN `suppress_card_in_major_incident` "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    op.execute(text(
        "ALTER TABLE `teams_card_post` ADD COLUMN `major_incident_id` BIGINT NULL, "
        "ADD CONSTRAINT `fk_teams_card_post_major_incident` FOREIGN KEY (`major_incident_id`) "
        "REFERENCES `major_incident` (`id`) ON DELETE CASCADE"
    ))

    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return

    if not _index_exists(conn, "teams_card_post", "uq_teams_card_post_org_major_incident"):
        conn.execute(text(
            "ALTER TABLE `teams_card_post` ADD CONSTRAINT `uq_teams_card_post_org_major_incident`"
            " UNIQUE (`org_id`, `major_incident_id`)"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "mysql":
        if _index_exists(conn, "teams_card_post", "uq_teams_card_post_org_major_incident"):
            conn.execute(text(
                "ALTER TABLE `teams_card_post` DROP INDEX `uq_teams_card_post_org_major_incident`"
            ))
        if _column_exists(conn, "teams_card_post", "major_incident_id"):
            op.execute(text(
                "ALTER TABLE `teams_card_post` DROP FOREIGN KEY `fk_teams_card_post_major_incident`"
            ))
    op.execute(text("ALTER TABLE `teams_card_post` DROP COLUMN `major_incident_id`"))
    op.execute(text("ALTER TABLE `teams_alarm_config` DROP COLUMN `suppress_card_in_major_incident`"))
