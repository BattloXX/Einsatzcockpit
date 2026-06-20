"""uas PR 6 – uas_kartenobjekt (Karten-Integration)

Revision ID: 0085
Revises: 0084
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE uas_kartenobjekt (
            id              BIGINT NOT NULL AUTO_INCREMENT,
            org_id          BIGINT NOT NULL,
            uas_einsatz_id  BIGINT NOT NULL,
            typ             VARCHAR(30) NOT NULL,
            geometrie       TEXT NULL,
            label           VARCHAR(200) NULL,
            hoehe_m         FLOAT NULL,
            radius_m        FLOAT NULL,
            created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            INDEX ix_uas_kartenobjekt_einsatz (uas_einsatz_id),
            INDEX ix_uas_kartenobjekt_org (org_id),
            CONSTRAINT fk_uas_kartenobjekt_einsatz FOREIGN KEY (uas_einsatz_id)
                REFERENCES uas_einsatz(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS uas_kartenobjekt"))
