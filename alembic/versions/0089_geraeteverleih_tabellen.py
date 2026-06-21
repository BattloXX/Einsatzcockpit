"""Geräteverleih-Modul – 4 neue Tabellen (Artikel, Stückliste, Positionen, Ausleihe)

Revision ID: 0089
Revises: 0088
Create Date: 2026-06-21
"""

from alembic import op
from sqlalchemy import text

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS verleih_artikel (
            id                  BIGINT NOT NULL AUTO_INCREMENT,
            org_id              BIGINT NULL,
            artikel_nr          VARCHAR(100) NULL,
            bezeichnung         VARCHAR(200) NOT NULL,
            ist_mengenartikel   TINYINT(1) NOT NULL DEFAULT 0,
            lagerbestand        INT NULL,
            notizen             TEXT NULL,
            aktiv               TINYINT(1) NOT NULL DEFAULT 1,
            created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            INDEX ix_verleih_artikel_org (org_id),
            CONSTRAINT fk_verleih_artikel_org FOREIGN KEY (org_id)
                REFERENCES fire_dept(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS verleih_stueckliste (
            id                  BIGINT NOT NULL AUTO_INCREMENT,
            org_id              BIGINT NULL,
            bezeichnung         VARCHAR(200) NOT NULL,
            notizen             TEXT NULL,
            aktiv               TINYINT(1) NOT NULL DEFAULT 1,
            created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            INDEX ix_verleih_stueckliste_org (org_id),
            CONSTRAINT fk_verleih_stueckliste_org FOREIGN KEY (org_id)
                REFERENCES fire_dept(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS verleih_stueckliste_position (
            id                  BIGINT NOT NULL AUTO_INCREMENT,
            stueckliste_id      BIGINT NOT NULL,
            artikel_id          BIGINT NULL,
            bezeichnung         VARCHAR(200) NULL,
            artikel_nr          VARCHAR(100) NULL,
            menge               INT NOT NULL DEFAULT 1,
            PRIMARY KEY (id),
            INDEX ix_verleih_sl_pos_sl (stueckliste_id),
            CONSTRAINT fk_verleih_sl_pos_sl FOREIGN KEY (stueckliste_id)
                REFERENCES verleih_stueckliste(id) ON DELETE CASCADE,
            CONSTRAINT fk_verleih_sl_pos_artikel FOREIGN KEY (artikel_id)
                REFERENCES verleih_artikel(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS verleih_ausleihe (
            id                       BIGINT NOT NULL AUTO_INCREMENT,
            org_id                   BIGINT NULL,
            lage_id                  INT NOT NULL,
            site_id                  INT NULL,
            name                     VARCHAR(200) NOT NULL,
            adresse                  VARCHAR(300) NULL,
            telefon                  VARCHAR(50) NULL,
            status                   ENUM('ausgeliehen','zurueckgegeben') NOT NULL DEFAULT 'ausgeliehen',
            pin                      VARCHAR(6) NULL,
            sms_ausleih_gesendet     TINYINT(1) NOT NULL DEFAULT 0,
            erinnerung_geplant_at    DATETIME NULL,
            erinnerung_gesendet_at   DATETIME NULL,
            ausgeliehen_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            zurueckgegeben_at        DATETIME NULL,
            created_by_user_id       BIGINT NULL,
            created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            INDEX ix_verleih_ausleihe_lage (lage_id),
            INDEX ix_verleih_ausleihe_org (org_id),
            INDEX ix_verleih_ausleihe_erinnerung (erinnerung_geplant_at),
            CONSTRAINT fk_verleih_ausleihe_lage FOREIGN KEY (lage_id)
                REFERENCES major_incident(id) ON DELETE CASCADE,
            CONSTRAINT fk_verleih_ausleihe_site FOREIGN KEY (site_id)
                REFERENCES incident_site(id) ON DELETE SET NULL,
            CONSTRAINT fk_verleih_ausleihe_org FOREIGN KEY (org_id)
                REFERENCES fire_dept(id) ON DELETE SET NULL,
            CONSTRAINT fk_verleih_ausleihe_user FOREIGN KEY (created_by_user_id)
                REFERENCES user(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS verleih_position (
            id                  BIGINT NOT NULL AUTO_INCREMENT,
            ausleihe_id         BIGINT NOT NULL,
            org_id              BIGINT NOT NULL,
            artikel_id          BIGINT NULL,
            bezeichnung         VARCHAR(200) NOT NULL,
            artikel_nr          VARCHAR(100) NULL,
            menge               INT NOT NULL DEFAULT 1,
            status              ENUM('ausgeliehen','zurueckgegeben') NOT NULL DEFAULT 'ausgeliehen',
            zurueckgegeben_at   DATETIME NULL,
            PRIMARY KEY (id),
            INDEX ix_verleih_position_ausleihe (ausleihe_id),
            CONSTRAINT fk_verleih_position_ausleihe FOREIGN KEY (ausleihe_id)
                REFERENCES verleih_ausleihe(id) ON DELETE CASCADE,
            CONSTRAINT fk_verleih_position_artikel FOREIGN KEY (artikel_id)
                REFERENCES verleih_artikel(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS verleih_position"))
    conn.execute(text("DROP TABLE IF EXISTS verleih_ausleihe"))
    conn.execute(text("DROP TABLE IF EXISTS verleih_stueckliste_position"))
    conn.execute(text("DROP TABLE IF EXISTS verleih_stueckliste"))
    conn.execute(text("DROP TABLE IF EXISTS verleih_artikel"))
