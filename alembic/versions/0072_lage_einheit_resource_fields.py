"""LageEinheit: Ressourcentyp, Abschnittszuordnung, Status-Erweiterung

Revision ID: 0072
Revises: 0070
Create Date: 2026-06-14
"""
from alembic import op
from sqlalchemy import text

revision = "0072"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Neue Spalten an lage_einheit anhängen
    conn.execute(text("""
        ALTER TABLE lage_einheit
            MODIFY COLUMN status VARCHAR(16) NOT NULL DEFAULT 'bereitgestellt',
            ADD COLUMN resource_type    VARCHAR(12)  NOT NULL DEFAULT 'fahrzeug'  AFTER is_from_org,
            ADD COLUMN sector_id        INT          NULL                          AFTER resource_type,
            ADD COLUMN incident_site_id INT          NULL                          AFTER sector_id,
            ADD COLUMN org_name         VARCHAR(120) NULL                          AFTER incident_site_id,
            ADD COLUMN bos              VARCHAR(20)  NULL                          AFTER org_name,
            ADD COLUMN qty              INT          NULL                          AFTER bos,
            ADD COLUMN unit             VARCHAR(20)  NULL                          AFTER qty,
            ADD COLUMN requested_at     DATETIME     NULL                          AFTER unit,
            ADD COLUMN arrived_at       DATETIME     NULL                          AFTER requested_at,
            ADD COLUMN committed_at     DATETIME     NULL                          AFTER arrived_at,
            ADD COLUMN released_at      DATETIME     NULL                          AFTER committed_at,
            ADD COLUMN leader_assignment_id INT      NULL                          AFTER released_at,
            ADD INDEX ix_le_sector (sector_id),
            ADD INDEX ix_le_site   (incident_site_id)
    """))

    # FK sector_id → site_sector
    conn.execute(text("""
        ALTER TABLE lage_einheit
            ADD CONSTRAINT fk_le_sector
                FOREIGN KEY (sector_id) REFERENCES site_sector(id) ON DELETE SET NULL,
            ADD CONSTRAINT fk_le_site
                FOREIGN KEY (incident_site_id) REFERENCES incident_site(id) ON DELETE SET NULL
    """))

    # Status-Mapping: alte Werte → neue Werte
    conn.execute(text("""
        UPDATE lage_einheit SET status = 'bereitgestellt' WHERE status = 'verfuegbar'
    """))
    conn.execute(text("""
        UPDATE lage_einheit SET status = 'im_einsatz' WHERE status = 'eingesetzt'
    """))
    conn.execute(text("""
        UPDATE lage_einheit SET status = 'abgerueckt' WHERE status = 'abgezogen'
    """))

    # GslStaffAssignment: sector_id für Abschnittsleiter-Zuordnung
    conn.execute(text("""
        ALTER TABLE gsl_staff_assignment
            ADD COLUMN sector_id INT NULL AFTER predecessor_id,
            ADD INDEX ix_gslsa_sector (sector_id),
            ADD CONSTRAINT fk_gslsa_sector
                FOREIGN KEY (sector_id) REFERENCES site_sector(id) ON DELETE SET NULL
    """))


def downgrade():
    conn = op.get_bind()

    conn.execute(text("ALTER TABLE gsl_staff_assignment DROP FOREIGN KEY fk_gslsa_sector"))
    conn.execute(text("ALTER TABLE gsl_staff_assignment DROP INDEX ix_gslsa_sector"))
    conn.execute(text("ALTER TABLE gsl_staff_assignment DROP COLUMN sector_id"))

    conn.execute(text("""
        UPDATE lage_einheit SET status = 'verfuegbar' WHERE status = 'bereitgestellt'
    """))
    conn.execute(text("""
        UPDATE lage_einheit SET status = 'eingesetzt' WHERE status = 'im_einsatz'
    """))
    conn.execute(text("""
        UPDATE lage_einheit SET status = 'abgezogen' WHERE status = 'abgerueckt'
    """))

    conn.execute(text("ALTER TABLE lage_einheit DROP FOREIGN KEY fk_le_sector"))
    conn.execute(text("ALTER TABLE lage_einheit DROP FOREIGN KEY fk_le_site"))
    conn.execute(text("""
        ALTER TABLE lage_einheit
            DROP INDEX ix_le_sector,
            DROP INDEX ix_le_site,
            DROP COLUMN resource_type,
            DROP COLUMN sector_id,
            DROP COLUMN incident_site_id,
            DROP COLUMN org_name,
            DROP COLUMN bos,
            DROP COLUMN qty,
            DROP COLUMN unit,
            DROP COLUMN requested_at,
            DROP COLUMN arrived_at,
            DROP COLUMN committed_at,
            DROP COLUMN released_at,
            DROP COLUMN leader_assignment_id,
            MODIFY COLUMN status VARCHAR(12) NOT NULL DEFAULT 'verfuegbar'
    """))
