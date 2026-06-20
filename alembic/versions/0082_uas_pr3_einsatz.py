"""uas PR 3 – uas_einsatz und uas_einsatz_rolle

Revision ID: 0082
Revises: 0081
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE uas_einsatz (
            id          BIGINT NOT NULL AUTO_INCREMENT,
            org_id      BIGINT NOT NULL,
            incident_id BIGINT NOT NULL,
            status      VARCHAR(30) NOT NULL DEFAULT 'alarmiert',
            alarmierung_at   DATETIME NULL,
            anmeldung_el_at  DATETIME NULL,
            abmeldung_el_at  DATETIME NULL,
            tetra_rufname    VARCHAR(60)  NULL,
            betreibernummer  VARCHAR(100) NULL,
            kommunikationsmatrix JSON NULL,
            risikobewertung      JSON NULL,
            einsatzgrund         TEXT NULL,
            datenschutz_bestaetigt TINYINT(1) NOT NULL DEFAULT 0,
            gesamteinsatzleiter  VARCHAR(150) NULL,
            notizen              TEXT NULL,
            created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_uas_einsatz_incident (incident_id),
            INDEX ix_uas_einsatz_org (org_id),
            CONSTRAINT fk_uas_einsatz_incident FOREIGN KEY (incident_id) REFERENCES incident(id) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    conn.execute(text("""
        CREATE TABLE uas_einsatz_rolle (
            id              BIGINT NOT NULL AUTO_INCREMENT,
            org_id          BIGINT NOT NULL,
            uas_einsatz_id  BIGINT NOT NULL,
            pilot_id        BIGINT NULL,
            helfer_name     VARCHAR(150) NULL,
            rolle           VARCHAR(40) NOT NULL,
            override_begruendung TEXT NULL,
            created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            INDEX ix_uas_einsatz_rolle_einsatz (uas_einsatz_id),
            INDEX ix_uas_einsatz_rolle_org (org_id),
            CONSTRAINT fk_uas_einsatz_rolle_einsatz FOREIGN KEY (uas_einsatz_id)
                REFERENCES uas_einsatz(id) ON DELETE CASCADE,
            CONSTRAINT fk_uas_einsatz_rolle_pilot FOREIGN KEY (pilot_id)
                REFERENCES uas_pilot(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS uas_einsatz_rolle"))
    conn.execute(text("DROP TABLE IF EXISTS uas_einsatz"))
