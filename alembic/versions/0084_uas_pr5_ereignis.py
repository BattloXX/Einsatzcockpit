"""uas PR 5 – uas_ereignis (Notfall/Unfall/Störung, ACG-Meldung)

Revision ID: 0084
Revises: 0083
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE uas_ereignis (
            id              BIGINT NOT NULL AUTO_INCREMENT,
            org_id          BIGINT NOT NULL,
            uas_flug_id     BIGINT NULL,
            typ             VARCHAR(20) NOT NULL DEFAULT 'stoerung',
            kategorie       VARCHAR(100) NULL,
            zeit_lokal      VARCHAR(10) NULL,
            datum_lokal     DATE NULL,
            zeit_utc        VARCHAR(10) NULL,
            datum_utc       DATE NULL,
            ort_icao        VARCHAR(10) NULL,
            koordinaten     TEXT NULL,
            klassifizierung VARCHAR(100) NULL,
            beschreibung    TEXT NULL,
            massnahmen      JSON NULL,
            gemeldet_an     JSON NULL,
            acg_export_at   DATETIME NULL,
            inhalt_hash     VARCHAR(64) NULL,
            created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            INDEX ix_uas_ereignis_flug (uas_flug_id),
            INDEX ix_uas_ereignis_org (org_id),
            CONSTRAINT fk_uas_ereignis_flug FOREIGN KEY (uas_flug_id)
                REFERENCES uas_flug(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS uas_ereignis"))
