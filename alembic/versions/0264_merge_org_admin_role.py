"""Merge the historical org_admin role into admin.

Revision ID: 0264
Revises: 0263
"""

import sqlalchemy as sa

from alembic import op

revision = "0264"
down_revision = "0263"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ohne admin-Zeile würden org_admin-Nutzer beim Löschen ihre Rechte verlieren.
    op.execute(sa.text("""
        INSERT INTO `role` (code, label)
        SELECT 'admin', 'Organisations-Administrator'
        WHERE NOT EXISTS (SELECT 1 FROM `role` WHERE code = 'admin')
    """))
    # INSERT .. SELECT with NOT EXISTS works on both MariaDB and SQLite and
    # avoids violating user_role's composite primary key for dual-assigned users.
    op.execute(sa.text("""
        INSERT INTO user_role (user_id, role_id)
        SELECT old_assignment.user_id, admin_role.id
        FROM user_role AS old_assignment
        JOIN `role` AS old_role ON old_role.id = old_assignment.role_id
        JOIN `role` AS admin_role ON admin_role.code = 'admin'
        WHERE old_role.code = 'org_admin'
          AND NOT EXISTS (
              SELECT 1 FROM user_role AS existing
              WHERE existing.user_id = old_assignment.user_id
                AND existing.role_id = admin_role.id
          )
    """))
    op.execute(sa.text("""
        DELETE FROM user_role
        WHERE role_id IN (SELECT id FROM `role` WHERE code = 'org_admin')
    """))
    op.execute(sa.text("DELETE FROM `role` WHERE code = 'org_admin'"))
    op.execute(sa.text("UPDATE `role` SET label = 'Organisations-Administrator' WHERE code = 'admin'"))


def downgrade() -> None:
    # Role membership cannot be distinguished after a merge.  Restore only the
    # legacy alias row so older application code can be rolled back safely.
    op.execute(sa.text("""
        INSERT INTO `role` (code, label)
        SELECT 'org_admin', 'Organisations-Administrator'
        WHERE NOT EXISTS (SELECT 1 FROM `role` WHERE code = 'org_admin')
    """))
    op.execute(sa.text("UPDATE `role` SET label = 'Administrator (Organisations-Admin)' WHERE code = 'admin'"))
