"""Einsatzstellenübergreifende Meldungen (cross_site_marker)

Revision ID: 0068
Revises: 0067
Create Date: 2026-06-13
"""
from alembic import op
from sqlalchemy import text

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS cross_site_marker (
            id                INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
            major_incident_id INT          NOT NULL,
            org_id            BIGINT       NULL,
            title             VARCHAR(160) NOT NULL,
            marker_type       VARCHAR(32)  NOT NULL DEFAULT 'sonstiges',
            status            VARCHAR(16)  NOT NULL DEFAULT 'gemeldet',
            description       TEXT         NULL,
            ort               VARCHAR(120) NULL,
            strasse           VARCHAR(160) NULL,
            hausnr            VARCHAR(20)  NULL,
            lat               DOUBLE       NULL,
            lng               DOUBLE       NULL,
            source            VARCHAR(12)  NOT NULL DEFAULT 'manual',
            sort_index        INT          NOT NULL DEFAULT 0,
            created_at        DATETIME     NOT NULL,
            updated_at        DATETIME     NOT NULL,
            created_by        BIGINT       NULL,
            author_name       VARCHAR(120) NULL,
            INDEX ix_csm_incident (major_incident_id),
            INDEX ix_csm_org      (org_id),
            CONSTRAINT fk_csm_incident FOREIGN KEY (major_incident_id)
                REFERENCES major_incident(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade():
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS cross_site_marker"))
