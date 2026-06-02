"""Alarmstichwort-Zuweisung für Lage-Hinweise

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-02 23:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS lage_hint_alarm (
            id BIGINT NOT NULL AUTO_INCREMENT,
            lage_hint_id BIGINT NOT NULL,
            alarm_type_code VARCHAR(10) NOT NULL,
            display_order INT NOT NULL DEFAULT 0,
            PRIMARY KEY (id),
            FOREIGN KEY (lage_hint_id) REFERENCES lage_hint (id) ON DELETE CASCADE,
            FOREIGN KEY (alarm_type_code) REFERENCES alarm_type (code) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    op.drop_table("lage_hint_alarm")
