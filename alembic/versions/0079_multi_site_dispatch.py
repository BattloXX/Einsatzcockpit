"""Mehrfach-Disposition: einheit_site_dispatch Tabelle

Revision ID: 0079
Revises: 0078
Create Date: 2026-06-18
"""
from alembic import op
from sqlalchemy import text

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("""
        CREATE TABLE einheit_site_dispatch (
            id INT NOT NULL AUTO_INCREMENT,
            einheit_id INT NOT NULL,
            site_id INT NOT NULL,
            dispatched_at DATETIME NOT NULL,
            vor_ort_at DATETIME NULL,
            withdrawn_at DATETIME NULL,
            dispatched_by BIGINT NULL,
            author_name VARCHAR(120) NULL,
            PRIMARY KEY (id),
            CONSTRAINT fk_esd_einheit FOREIGN KEY (einheit_id)
                REFERENCES lage_einheit(id) ON DELETE CASCADE,
            CONSTRAINT fk_esd_site FOREIGN KEY (site_id)
                REFERENCES incident_site(id) ON DELETE CASCADE,
            CONSTRAINT fk_esd_user FOREIGN KEY (dispatched_by)
                REFERENCES user(id) ON DELETE SET NULL,
            INDEX ix_esd_einheit_id (einheit_id),
            INDEX ix_esd_site_id (site_id)
        )
    """))

    # Data-Migration: bestehende LageEinheit-Zuordnungen übernehmen
    conn.execute(text("""
        INSERT INTO einheit_site_dispatch
            (einheit_id, site_id, dispatched_at, vor_ort_at)
        SELECT
            id,
            incident_site_id,
            COALESCE(committed_at, added_at, NOW()),
            COALESCE(committed_at, added_at, NOW())
        FROM lage_einheit
        WHERE incident_site_id IS NOT NULL
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS einheit_site_dispatch"))
