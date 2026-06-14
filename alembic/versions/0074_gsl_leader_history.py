"""EL/AbsLtr Leiter-Historie: major_incident.leader_assignment_id + Seed-Rollen

Revision ID: 0074
Revises: 0073
Create Date: 2026-06-14
"""
from alembic import op
from sqlalchemy import text

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

_SEED_ROLES = [
    # (code, name, sort_order, is_required, allows_multiple)
    ("EL",     "Einsatzleiter (Gesamt)",    0,  True,  False),
    ("AbsLtr", "Abschnittsleiter",          5,  False, True),
]


def upgrade():
    conn = op.get_bind()

    # Gesamteinsatzleiter-Pointer auf MajorIncident
    conn.execute(text("""
        ALTER TABLE major_incident
            ADD COLUMN leader_assignment_id INT NULL AFTER updated_at,
            ADD CONSTRAINT fk_mi_leader
                FOREIGN KEY (leader_assignment_id)
                REFERENCES gsl_staff_assignment(id) ON DELETE SET NULL
    """))

    # Seed-Rollen anlegen (nur falls code noch nicht vorhanden)
    for code, name, sort_order, is_required, allows_multiple in _SEED_ROLES:
        conn.execute(text("""
            INSERT INTO gsl_staff_role (code, name, sort_order, is_required, allows_multiple)
            SELECT :code, :name, :sort, :req, :multi
            WHERE NOT EXISTS (
                SELECT 1 FROM gsl_staff_role WHERE code = :code AND org_id IS NULL
            )
        """), {"code": code, "name": name, "sort": sort_order,
               "req": int(is_required), "multi": int(allows_multiple)})


def downgrade():
    conn = op.get_bind()

    conn.execute(text("ALTER TABLE major_incident DROP FOREIGN KEY fk_mi_leader"))
    conn.execute(text("ALTER TABLE major_incident DROP COLUMN leader_assignment_id"))

    for code, *_ in _SEED_ROLES:
        conn.execute(text(
            "DELETE FROM gsl_staff_role WHERE code = :code AND org_id IS NULL"
        ), {"code": code})
