"""PR 1: UAS-Modul Stammdaten (uas_device, uas_pilot, uas_flugbewegung, uas_wartung)

Revision ID: 0081
Revises: 0080
Create Date: 2026-06-20
"""
from sqlalchemy import text

from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── uas_device: Geräteregister ────────────────────────────────────────────
    conn.execute(text("""
        CREATE TABLE uas_device (
            id              BIGINT          NOT NULL AUTO_INCREMENT,
            org_id          BIGINT          NULL,
            bezeichnung     VARCHAR(150)    NOT NULL,
            hersteller      VARCHAR(100)    NOT NULL DEFAULT '',
            typ             VARCHAR(100)    NOT NULL DEFAULT '',
            registriernummer VARCHAR(100)   NULL,
            ce_klasse       VARCHAR(10)     NOT NULL DEFAULT 'C2',
            unterkategorie  VARCHAR(5)      NOT NULL DEFAULT 'A2',
            mtom_g          INT             NULL,
            leergewicht_g   INT             NULL,
            hat_waermebildkamera TINYINT(1) NOT NULL DEFAULT 0,
            allwettertauglich    TINYINT(1) NOT NULL DEFAULT 0,
            versicherung_polizze  VARCHAR(100) NULL,
            versicherung_gueltig_bis DATE   NULL,
            sybos_id        VARCHAR(50)     NULL,
            beschaffungsdatum DATE          NULL,
            tauschintervall_jahre INT       NOT NULL DEFAULT 7,
            status          VARCHAR(20)     NOT NULL DEFAULT 'aktiv',
            qr_token        VARCHAR(64)     NOT NULL,
            notizen         TEXT            NULL,
            created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_uas_device_qr_token (qr_token),
            KEY ix_uas_device_org_status (org_id, status),
            CONSTRAINT fk_uas_device_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    # ── uas_pilot: Piloten & Zertifikate ──────────────────────────────────────
    conn.execute(text("""
        CREATE TABLE uas_pilot (
            id              BIGINT          NOT NULL AUTO_INCREMENT,
            org_id          BIGINT          NULL,
            person_id       BIGINT          NULL,
            nachname        VARCHAR(100)    NOT NULL,
            vorname         VARCHAR(100)    NOT NULL,
            geburtsdatum    DATE            NULL,
            ist_truppfuehrer TINYINT(1)    NOT NULL DEFAULT 0,
            a1a3_id         VARCHAR(100)    NULL,
            a1a3_gueltig_bis DATE           NULL,
            a2_id           VARCHAR(100)    NULL,
            a2_gueltig_bis  DATE            NULL,
            bos_stufe       VARCHAR(5)      NOT NULL DEFAULT '0',
            bos_ausbildung_datum DATE       NULL,
            bos_rezert_bis  DATE            NULL,
            lfv_zugelassen  TINYINT(1)      NOT NULL DEFAULT 0,
            qualifikationen TEXT            NULL,
            aktiv           TINYINT(1)      NOT NULL DEFAULT 1,
            notizen         TEXT            NULL,
            PRIMARY KEY (id),
            KEY ix_uas_pilot_org_aktiv (org_id, aktiv),
            CONSTRAINT fk_uas_pilot_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_uas_pilot_person FOREIGN KEY (person_id)
                REFERENCES member (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    # ── uas_flugbewegung: Currency-Tracking ───────────────────────────────────
    conn.execute(text("""
        CREATE TABLE uas_flugbewegung (
            id              BIGINT          NOT NULL AUTO_INCREMENT,
            org_id          BIGINT          NULL,
            pilot_id        BIGINT          NOT NULL,
            device_id       BIGINT          NULL,
            datum           DATE            NOT NULL,
            dauer_min       INT             NULL,
            art             VARCHAR(20)     NOT NULL DEFAULT 'einsatz',
            uas_flug_id     BIGINT          NULL,
            PRIMARY KEY (id),
            KEY ix_uas_flugbewegung_pilot_datum (pilot_id, datum),
            CONSTRAINT fk_uas_flugbewegung_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_uas_flugbewegung_pilot FOREIGN KEY (pilot_id)
                REFERENCES uas_pilot (id) ON DELETE CASCADE,
            CONSTRAINT fk_uas_flugbewegung_device FOREIGN KEY (device_id)
                REFERENCES uas_device (id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    # ── uas_wartung: Wartungsbuch ─────────────────────────────────────────────
    conn.execute(text("""
        CREATE TABLE uas_wartung (
            id              BIGINT          NOT NULL AUTO_INCREMENT,
            org_id          BIGINT          NULL,
            device_id       BIGINT          NOT NULL,
            datum           DATE            NOT NULL,
            art             VARCHAR(40)     NOT NULL DEFAULT 'monatliche_sichtkontrolle',
            pruefpunkte     TEXT            NULL,
            pruefer         VARCHAR(150)    NULL,
            ergebnis        VARCHAR(5)      NOT NULL DEFAULT 'io',
            bemerkung       TEXT            NULL,
            naechste_faellig DATE           NULL,
            PRIMARY KEY (id),
            KEY ix_uas_wartung_device_datum (device_id, datum),
            CONSTRAINT fk_uas_wartung_org FOREIGN KEY (org_id)
                REFERENCES fire_dept (id) ON DELETE SET NULL,
            CONSTRAINT fk_uas_wartung_device FOREIGN KEY (device_id)
                REFERENCES uas_device (id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS uas_wartung"))
    conn.execute(text("DROP TABLE IF EXISTS uas_flugbewegung"))
    conn.execute(text("DROP TABLE IF EXISTS uas_pilot"))
    conn.execute(text("DROP TABLE IF EXISTS uas_device"))
