"""PR 5: Förderstrecken-Persistenz (foerderstrecke, foerder_station, foerder_ergebnis)

Revision ID: 0172
Revises: 0171
Create Date: 2026-07-18
"""
from sqlalchemy import text

from alembic import op

revision = "0172"
down_revision = "0171"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("""
        CREATE TABLE foerderstrecke (
            id                  BIGINT       NOT NULL AUTO_INCREMENT,
            org_id              BIGINT       NULL,
            name                VARCHAR(150) NOT NULL,
            status              VARCHAR(20)  NOT NULL DEFAULT 'entwurf',
            objekt_id           BIGINT       NULL,
            incident_id         BIGINT       NULL,
            lage_id             INT          NULL,
            route_geojson       TEXT         NULL,
            ansaug_json         TEXT         NULL,
            auslass_json        TEXT         NULL,
            hoehenprofil_json   TEXT         NULL,
            parameter_json      TEXT         NULL,
            erstellt_am         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            aktualisiert_am     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            erstellt_von_id     BIGINT       NULL,
            aktualisiert_von_id BIGINT       NULL,
            PRIMARY KEY (id),
            KEY ix_foerderstrecke_org_status (org_id, status),
            CONSTRAINT fk_foerderstrecke_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerderstrecke_objekt FOREIGN KEY (objekt_id)
                REFERENCES objekt (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerderstrecke_incident FOREIGN KEY (incident_id)
                REFERENCES incident (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerderstrecke_lage FOREIGN KEY (lage_id)
                REFERENCES major_incident (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerderstrecke_ersteller FOREIGN KEY (erstellt_von_id)
                REFERENCES user (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerderstrecke_aend FOREIGN KEY (aktualisiert_von_id)
                REFERENCES user (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    conn.execute(text("""
        CREATE TABLE foerder_station (
            id                  BIGINT       NOT NULL AUTO_INCREMENT,
            org_id              BIGINT       NULL,
            strecke_id          BIGINT       NOT NULL,
            sort                INT          NOT NULL DEFAULT 0,
            strang_nr           INT          NOT NULL DEFAULT 1,
            lat                 DOUBLE       NULL,
            lng                 DOUBLE       NULL,
            typ                 VARCHAR(20)  NOT NULL DEFAULT 'verstaerker',
            pumpen_typ_id       BIGINT       NULL,
            rpm                 VARCHAR(20)  NULL,
            druck_parallel      INT          NOT NULL DEFAULT 1,
            schlauch_typ_id     BIGINT       NULL,
            saug_parallel       INT          NOT NULL DEFAULT 1,
            behaelter_volumen_l INT          NULL,
            abgang_straenge     TEXT         NULL,
            wasserstelle_id     BIGINT       NULL,
            PRIMARY KEY (id),
            KEY ix_foerder_station_strecke (strecke_id, strang_nr, sort),
            CONSTRAINT fk_foerder_station_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_station_strecke FOREIGN KEY (strecke_id)
                REFERENCES foerderstrecke (id) ON DELETE CASCADE,
            CONSTRAINT fk_foerder_station_pumpe FOREIGN KEY (pumpen_typ_id)
                REFERENCES foerder_pumpen_typ (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_station_schlauch FOREIGN KEY (schlauch_typ_id)
                REFERENCES foerder_schlauch_typ (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_station_wasserstelle FOREIGN KEY (wasserstelle_id)
                REFERENCES wasserstelle (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    conn.execute(text("""
        CREATE TABLE foerder_ergebnis (
            id                 BIGINT       NOT NULL AUTO_INCREMENT,
            org_id             BIGINT       NULL,
            strecke_id         BIGINT       NOT NULL,
            berechnet_am       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            q_max_l_min        DOUBLE       NULL,
            modus              VARCHAR(1)   NOT NULL DEFAULT 'A',
            stationswerte_json TEXT         NULL,
            material_json      TEXT         NULL,
            warnungen_json     TEXT         NULL,
            PRIMARY KEY (id),
            KEY ix_foerder_ergebnis_strecke (strecke_id, berechnet_am),
            CONSTRAINT fk_foerder_ergebnis_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_ergebnis_strecke FOREIGN KEY (strecke_id)
                REFERENCES foerderstrecke (id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS foerder_ergebnis"))
    conn.execute(text("DROP TABLE IF EXISTS foerder_station"))
    conn.execute(text("DROP TABLE IF EXISTS foerderstrecke"))
