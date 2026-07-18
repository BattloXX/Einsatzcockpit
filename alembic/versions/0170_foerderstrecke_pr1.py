"""PR 1: Förderstrecken-Planer Gerätekatalog (Feature-Flag + Pumpen-/Schlauchtypen)

Revision ID: 0170
Revises: 0169
Create Date: 2026-07-18
"""
from sqlalchemy import text

from alembic import op

revision = "0170"
down_revision = "0169"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Feature-Flag je Org (zweistufig; System-Flag lebt in system_settings) ──
    conn.execute(text("""
        ALTER TABLE org_settings
            ADD COLUMN foerderstrecke_module_enabled TINYINT(1) NOT NULL DEFAULT 0
    """))

    # ── foerder_pumpen_typ: Pumpenkatalog je Org ──────────────────────────────
    conn.execute(text("""
        CREATE TABLE foerder_pumpen_typ (
            id                  BIGINT          NOT NULL AUTO_INCREMENT,
            org_id              BIGINT          NULL,
            name                VARCHAR(150)    NOT NULL,
            kennlinien_json     TEXT            NULL,
            druck_anschluss_dn  INT             NULL,
            druck_parallel_max  INT             NOT NULL DEFAULT 1,
            saug_anschluss_dn   INT             NULL,
            saug_parallel_max   INT             NOT NULL DEFAULT 1,
            max_ansaughoehe_m   DOUBLE          NOT NULL DEFAULT 7.5,
            min_eingangsdruck_bar DOUBLE        NOT NULL DEFAULT 1.5,
            max_ausgangsdruck_bar DOUBLE        NULL,
            npshr_json          TEXT            NULL,
            tank_l              INT             NULL,
            verbrauch_json      TEXT            NULL,
            vehicle_id          BIGINT          NULL,
            hinweise            TEXT            NULL,
            foto_pfad           VARCHAR(500)    NULL,
            aktiv               TINYINT(1)      NOT NULL DEFAULT 1,
            quelle              VARCHAR(20)     NOT NULL DEFAULT 'manuell',
            vorlage_key         VARCHAR(64)     NULL,
            erstellt_am         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
            aktualisiert_am     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            erstellt_von_id     BIGINT          NULL,
            aktualisiert_von_id BIGINT          NULL,
            PRIMARY KEY (id),
            KEY ix_foerder_pumpen_typ_org_aktiv (org_id, aktiv),
            CONSTRAINT fk_foerder_pumpen_typ_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_pumpen_typ_vehicle FOREIGN KEY (vehicle_id)
                REFERENCES vehicle_master (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_pumpen_typ_ersteller FOREIGN KEY (erstellt_von_id)
                REFERENCES user (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_pumpen_typ_aend FOREIGN KEY (aktualisiert_von_id)
                REFERENCES user (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    # ── foerder_schlauch_typ: Schlauchkatalog je Org ──────────────────────────
    conn.execute(text("""
        CREATE TABLE foerder_schlauch_typ (
            id                  BIGINT          NOT NULL AUTO_INCREMENT,
            org_id              BIGINT          NULL,
            kuerzel             VARCHAR(30)     NOT NULL,
            durchmesser_mm      INT             NOT NULL,
            k_verlust           DOUBLE          NOT NULL,
            element_laenge_m    INT             NOT NULL DEFAULT 20,
            max_betriebsdruck_bar DOUBLE        NULL,
            wasserinhalt_l_m    DOUBLE          NULL,
            vorrat_m            INT             NULL,
            aktiv               TINYINT(1)      NOT NULL DEFAULT 1,
            quelle              VARCHAR(20)     NOT NULL DEFAULT 'manuell',
            vorlage_key         VARCHAR(64)     NULL,
            erstellt_am         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
            aktualisiert_am     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            erstellt_von_id     BIGINT          NULL,
            aktualisiert_von_id BIGINT          NULL,
            PRIMARY KEY (id),
            KEY ix_foerder_schlauch_typ_org_aktiv (org_id, aktiv),
            CONSTRAINT fk_foerder_schlauch_typ_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_schlauch_typ_ersteller FOREIGN KEY (erstellt_von_id)
                REFERENCES user (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_schlauch_typ_aend FOREIGN KEY (aktualisiert_von_id)
                REFERENCES user (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS foerder_schlauch_typ"))
    conn.execute(text("DROP TABLE IF EXISTS foerder_pumpen_typ"))
    conn.execute(text("ALTER TABLE org_settings DROP COLUMN foerderstrecke_module_enabled"))
