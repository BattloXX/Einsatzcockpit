"""GSL Stab: gsl_staff_role + gsl_staff_assignment (SKKM-Besetzungsjournal)

Revision ID: 0062
Revises: 0061
Create Date: 2026-06-12
"""
from alembic import op
from sqlalchemy import text

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("""
        CREATE TABLE gsl_staff_role (
            id            INT          NOT NULL AUTO_INCREMENT,
            code          VARCHAR(20)  NOT NULL,
            name          VARCHAR(80)  NOT NULL,
            sort_order    INT          NOT NULL DEFAULT 0,
            is_required   TINYINT(1)   NOT NULL DEFAULT 0,
            allows_multiple TINYINT(1) NOT NULL DEFAULT 0,
            org_id        BIGINT       NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_staff_role_code_org (code, org_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    # Seed SKKM roles (org_id=NULL = systemweit)
    conn.execute(text("""
        INSERT INTO gsl_staff_role (code, name, sort_order, is_required, allows_multiple, org_id) VALUES
          ('EL',     'Einsatzleiter',                           0, 1, 0, NULL),
          ('LDS',    'Leiter der Stabsarbeit',                  1, 1, 0, NULL),
          ('S1',     'S1 – Personal',                           2, 1, 0, NULL),
          ('S2',     'S2 – Lage',                               3, 1, 0, NULL),
          ('S3',     'S3 – Einsatz',                            4, 1, 0, NULL),
          ('S4',     'S4 – Versorgung',                         5, 1, 0, NULL),
          ('S5',     'S5 – Öffentlichkeitsarbeit',              6, 1, 0, NULL),
          ('S6',     'S6 – Kommunikation',                      7, 1, 0, NULL),
          ('MSST',   'Meldesammelstelle',                       8, 0, 0, NULL),
          ('ERKUNDER','Erkunder',                               9, 0, 0, NULL),
          ('FB',     'Fachberater / Sachverständiger',         10, 0, 1, NULL),
          ('VO',     'Verbindungsoffizier',                    11, 0, 1, NULL)
    """))

    conn.execute(text("""
        CREATE TABLE gsl_staff_assignment (
            id              INT          NOT NULL AUTO_INCREMENT,
            incident_id     INT          NOT NULL,
            role_id         INT          NOT NULL,
            org_id          BIGINT       NOT NULL,
            member_id       BIGINT       NULL,
            person_name     VARCHAR(120) NULL,
            is_lead         TINYINT(1)   NOT NULL DEFAULT 1,
            start_at        DATETIME     NOT NULL,
            end_at          DATETIME     NULL,
            predecessor_id  INT          NULL,
            note            TEXT         NULL,
            created_by      BIGINT       NULL,
            created_at      DATETIME     NOT NULL,
            PRIMARY KEY (id),
            KEY idx_gsa_incident (incident_id),
            KEY idx_gsa_role (role_id),
            CONSTRAINT fk_gsa_incident FOREIGN KEY (incident_id) REFERENCES major_incident(id) ON DELETE CASCADE,
            CONSTRAINT fk_gsa_role     FOREIGN KEY (role_id)     REFERENCES gsl_staff_role(id),
            CONSTRAINT fk_gsa_member   FOREIGN KEY (member_id)   REFERENCES member(id) ON DELETE SET NULL,
            CONSTRAINT fk_gsa_pred     FOREIGN KEY (predecessor_id) REFERENCES gsl_staff_assignment(id) ON DELETE SET NULL,
            CONSTRAINT fk_gsa_creator  FOREIGN KEY (created_by)  REFERENCES user(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    # OrgSettings: konfigurierbare Pflicht-Rollen je Org
    conn.execute(text("""
        ALTER TABLE org_settings
            ADD COLUMN gsl_required_staff_roles TEXT NULL,
            ADD COLUMN vehicle_stale_minutes INT NOT NULL DEFAULT 5
    """))


def downgrade():
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE org_settings DROP COLUMN vehicle_stale_minutes"))
    conn.execute(text("ALTER TABLE org_settings DROP COLUMN gsl_required_staff_roles"))
    conn.execute(text("DROP TABLE IF EXISTS gsl_staff_assignment"))
    conn.execute(text("DROP TABLE IF EXISTS gsl_staff_role"))
