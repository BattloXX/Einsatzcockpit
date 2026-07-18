"""PR 7: Förderstrecken-Kalibrierung (foerder_messung, foerder_kalibrier_vorschlag)

Revision ID: 0173
Revises: 0172
Create Date: 2026-07-18
"""
from sqlalchemy import text

from alembic import op

revision = "0173"
down_revision = "0172"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("""
        CREATE TABLE foerder_messung (
            id              BIGINT       NOT NULL AUTO_INCREMENT,
            org_id          BIGINT       NULL,
            strecke_id      BIGINT       NULL,
            station_id      BIGINT       NULL,
            schlauch_typ_id BIGINT       NULL,
            datum           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            q_gemessen_l_min DOUBLE      NULL,
            laenge_m        DOUBLE       NULL,
            n_parallel      INT          NOT NULL DEFAULT 1,
            delta_hoehe_m   DOUBLE       NOT NULL DEFAULT 0,
            p_aus_bar       DOUBLE       NULL,
            p_ein_folge_bar DOUBLE       NULL,
            notiz           TEXT         NULL,
            erstellt_von_id BIGINT       NULL,
            PRIMARY KEY (id),
            KEY ix_foerder_messung_strecke (strecke_id, datum),
            CONSTRAINT fk_foerder_messung_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_messung_strecke FOREIGN KEY (strecke_id)
                REFERENCES foerderstrecke (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_messung_station FOREIGN KEY (station_id)
                REFERENCES foerder_station (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_messung_schlauch FOREIGN KEY (schlauch_typ_id)
                REFERENCES foerder_schlauch_typ (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_messung_user FOREIGN KEY (erstellt_von_id)
                REFERENCES user (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    conn.execute(text("""
        CREATE TABLE foerder_kalibrier_vorschlag (
            id                BIGINT       NOT NULL AUTO_INCREMENT,
            org_id            BIGINT       NULL,
            schlauch_typ_id   BIGINT       NOT NULL,
            k_alt             DOUBLE       NULL,
            k_neu             DOUBLE       NOT NULL,
            n_messungen       INT          NOT NULL DEFAULT 0,
            begruendung       TEXT         NULL,
            status            VARCHAR(20)  NOT NULL DEFAULT 'offen',
            erstellt_am       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            entschieden_von_id BIGINT      NULL,
            entschieden_am    DATETIME     NULL,
            PRIMARY KEY (id),
            KEY ix_foerder_kalibrier_org_status (org_id, status),
            CONSTRAINT fk_foerder_kalibrier_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_foerder_kalibrier_schlauch FOREIGN KEY (schlauch_typ_id)
                REFERENCES foerder_schlauch_typ (id) ON DELETE CASCADE,
            CONSTRAINT fk_foerder_kalibrier_user FOREIGN KEY (entschieden_von_id)
                REFERENCES user (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS foerder_kalibrier_vorschlag"))
    conn.execute(text("DROP TABLE IF EXISTS foerder_messung"))
