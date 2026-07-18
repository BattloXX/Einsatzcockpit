"""PR 3: Persistenter Höhen-Cache (global) für den Förderstrecken-Planer

Revision ID: 0171
Revises: 0170
Create Date: 2026-07-18
"""
from sqlalchemy import text

from alembic import op

revision = "0171"
down_revision = "0170"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE hoehen_cache (
            id          BIGINT       NOT NULL AUTO_INCREMENT,
            lat_key     INT          NOT NULL,
            lng_key     INT          NOT NULL,
            hoehe_m     DOUBLE       NOT NULL,
            quelle      VARCHAR(20)  NOT NULL DEFAULT 'openmeteo',
            erstellt_am DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_hoehen_cache_koord (lat_key, lng_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS hoehen_cache"))
