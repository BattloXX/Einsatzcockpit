"""template matrix: Auftragsvorlagen/Meldungsvorlagen/Default-Meldungen als n:m zu Alarmtypen

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-29
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def _column_exists(conn, table, column):
    r = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {"t": table, "c": column})
    return r.scalar() > 0


def _drop_fk_on_column(conn, table, column):
    """Drop any FK constraints referencing the given column."""
    r = conn.execute(text(
        "SELECT CONSTRAINT_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        " AND REFERENCED_TABLE_NAME IS NOT NULL"
    ), {"t": table, "c": column})
    for row in r:
        conn.execute(text(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{row[0]}`"))


def upgrade():
    conn = op.get_bind()

    # ── task_suggestion_alarm ──────────────────────────────────────────────────
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS task_suggestion_alarm (
            id BIGINT NOT NULL AUTO_INCREMENT,
            task_suggestion_id BIGINT NOT NULL,
            alarm_type_code VARCHAR(10) NOT NULL,
            display_order INT NOT NULL DEFAULT 0,
            PRIMARY KEY (id),
            FOREIGN KEY (task_suggestion_id) REFERENCES task_suggestion (id) ON DELETE CASCADE,
            FOREIGN KEY (alarm_type_code) REFERENCES alarm_type (code) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))
    if _column_exists(conn, "task_suggestion", "alarm_type_code"):
        conn.execute(text("DELETE FROM task_suggestion_alarm"))
        conn.execute(text("""
            INSERT INTO task_suggestion_alarm (task_suggestion_id, alarm_type_code, display_order)
            SELECT id, alarm_type_code, display_order FROM task_suggestion
        """))
        _drop_fk_on_column(conn, "task_suggestion", "alarm_type_code")
        conn.execute(text("ALTER TABLE task_suggestion DROP COLUMN alarm_type_code"))
    if _column_exists(conn, "task_suggestion", "display_order"):
        conn.execute(text("ALTER TABLE task_suggestion DROP COLUMN display_order"))

    # ── message_suggestion_alarm ───────────────────────────────────────────────
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS message_suggestion_alarm (
            id BIGINT NOT NULL AUTO_INCREMENT,
            message_suggestion_id BIGINT NOT NULL,
            alarm_type_code VARCHAR(10) NOT NULL,
            display_order INT NOT NULL DEFAULT 0,
            PRIMARY KEY (id),
            FOREIGN KEY (message_suggestion_id) REFERENCES message_suggestion (id) ON DELETE CASCADE,
            FOREIGN KEY (alarm_type_code) REFERENCES alarm_type (code) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))
    if _column_exists(conn, "message_suggestion", "alarm_type_code"):
        conn.execute(text("DELETE FROM message_suggestion_alarm"))
        conn.execute(text("""
            INSERT INTO message_suggestion_alarm (message_suggestion_id, alarm_type_code, display_order)
            SELECT id, alarm_type_code, display_order FROM message_suggestion
        """))
        _drop_fk_on_column(conn, "message_suggestion", "alarm_type_code")
        conn.execute(text("ALTER TABLE message_suggestion DROP COLUMN alarm_type_code"))
    if _column_exists(conn, "message_suggestion", "display_order"):
        conn.execute(text("ALTER TABLE message_suggestion DROP COLUMN display_order"))

    # ── default_message_alarm ──────────────────────────────────────────────────
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS default_message_alarm (
            id INT NOT NULL AUTO_INCREMENT,
            default_message_id INT NOT NULL,
            alarm_type_code VARCHAR(10) NOT NULL,
            display_order INT NOT NULL DEFAULT 0,
            due_after_sec INT NOT NULL DEFAULT 300,
            PRIMARY KEY (id),
            FOREIGN KEY (default_message_id) REFERENCES default_message (id) ON DELETE CASCADE,
            FOREIGN KEY (alarm_type_code) REFERENCES alarm_type (code) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))
    if _column_exists(conn, "default_message", "alarm_type_code"):
        conn.execute(text("DELETE FROM default_message_alarm"))
        conn.execute(text("""
            INSERT INTO default_message_alarm (default_message_id, alarm_type_code, display_order, due_after_sec)
            SELECT id, alarm_type_code, 0, due_after_sec FROM default_message
        """))
        _drop_fk_on_column(conn, "default_message", "alarm_type_code")
        conn.execute(text("ALTER TABLE default_message DROP COLUMN alarm_type_code"))
    if _column_exists(conn, "default_message", "due_after_sec"):
        conn.execute(text("ALTER TABLE default_message DROP COLUMN due_after_sec"))


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0024")
