"""Multi-tenancy PR 1: AuditLog org_id, org_admin role

Revision ID: 0044
Revises: 0043
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    # 1. org_id auf audit_log (nullable, bestehende Einträge bleiben NULL = systemweit)
    op.execute(text(
        "ALTER TABLE `audit_log` "
        "ADD COLUMN `org_id` INT NULL DEFAULT NULL, "
        "ADD INDEX `ix_audit_log_org_id` (`org_id`)"
    ))
    op.execute(text(
        "ALTER TABLE `audit_log` "
        "ADD CONSTRAINT `fk_audit_log_org_id` "
        "FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE SET NULL"
    ))

    # 2. org_admin-Rolle einfügen (falls noch nicht vorhanden)
    op.execute(text(
        "INSERT IGNORE INTO `role` (`code`, `label`) "
        "VALUES ('org_admin', 'Organisations-Administrator')"
    ))

    # 3. Alle Nutzer, die die 'admin'-Rolle haben, erhalten zusätzlich 'org_admin'
    #    (Alias-Übergang – beide Rollen bleiben vorläufig bestehen)
    op.execute(text("""
        INSERT IGNORE INTO `user_role` (`user_id`, `role_id`)
        SELECT ur.user_id, (SELECT id FROM `role` WHERE code = 'org_admin')
        FROM `user_role` ur
        JOIN `role` r ON r.id = ur.role_id AND r.code = 'admin'
        WHERE (SELECT id FROM `role` WHERE code = 'org_admin') IS NOT NULL
    """))


def downgrade():
    op.execute(text(
        "ALTER TABLE `audit_log` "
        "DROP FOREIGN KEY `fk_audit_log_org_id`, "
        "DROP INDEX `ix_audit_log_org_id`, "
        "DROP COLUMN `org_id`"
    ))
