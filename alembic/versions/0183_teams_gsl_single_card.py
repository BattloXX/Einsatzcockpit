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
    conn = op.get_bind()
    is_mysql = conn.dialect.name == "mysql"

    # _column_exists()-Guards: robust gegen einen Retry nach einem zuvor teilweise
    # fehlgeschlagenen Lauf (siehe Vorfall unten) - jede ALTER TABLE-Anweisung lief auf
    # MySQL bereits als eigenes, atomares Statement, ein spaeterer Fehler in dieser
    # Funktion darf einen frueher schon erfolgreich angelegten Spalten nicht erneut
    # anzulegen versuchen ("Duplicate column name").
    if not is_mysql or not _column_exists(conn, "teams_alarm_config", "suppress_card_in_major_incident"):
        op.execute(text(
            "ALTER TABLE `teams_alarm_config` ADD COLUMN `suppress_card_in_major_incident` "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        ))

    if not is_mysql or not _column_exists(conn, "teams_card_post", "major_incident_id"):
        # INT (nicht BIGINT!): major_incident.id ist ein einfaches Integer/INT - eine FK
        # braucht denselben Spaltentyp, sonst schlaegt ADD CONSTRAINT auf MySQL mit
        # errno 150 "Foreign key constraint is incorrectly formed" fehl (Vorfall
        # 2026-07-26, urspruenglich faelschlich als BIGINT angelegt).
        op.execute(text(
            "ALTER TABLE `teams_card_post` ADD COLUMN `major_incident_id` INT NULL, "
            "ADD CONSTRAINT `fk_teams_card_post_major_incident` FOREIGN KEY (`major_incident_id`) "
            "REFERENCES `major_incident` (`id`) ON DELETE CASCADE"
        ))

    if not is_mysql:
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
