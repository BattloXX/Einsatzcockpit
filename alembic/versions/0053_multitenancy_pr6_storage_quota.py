"""Multi-tenancy PR 6: Speicher-Quota – OrgStorageUsage, FireDept.storage_quota_bytes,
SiteMedia.bytes + org_id

Revision ID: 0053
Revises: 0052
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. FireDept: storage_quota_bytes (NULL = unbegrenzt, IF NOT EXISTS – idempotent)
    conn.execute(text("""
        ALTER TABLE fire_dept
        ADD COLUMN IF NOT EXISTS storage_quota_bytes BIGINT NULL
    """))

    # 2. OrgStorageUsage-Tabelle anlegen
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS org_storage_usage (
            org_id       BIGINT NOT NULL,
            used_bytes   BIGINT NOT NULL DEFAULT 0,
            updated_at   DATETIME NOT NULL,
            PRIMARY KEY (org_id),
            CONSTRAINT fk_osu_org FOREIGN KEY (org_id)
                REFERENCES fire_dept(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    # 3. SiteMedia: bytes-Spalte (0 für Altdaten) und org_id (NULL für Altdaten)
    conn.execute(text("""
        ALTER TABLE site_media
        ADD COLUMN IF NOT EXISTS bytes  BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS org_id BIGINT NULL,
        ADD INDEX  IF NOT EXISTS ix_site_media_org_id (org_id)
    """))

    # 4. Bestehende Org-Zeilen in org_storage_usage eintragen (used_bytes=0, Reconcile später)
    conn.execute(text("""
        INSERT IGNORE INTO org_storage_usage (org_id, used_bytes, updated_at)
        SELECT id, 0, NOW() FROM fire_dept
    """))


def downgrade():
    conn = op.get_bind()

    conn.execute(text("ALTER TABLE site_media DROP INDEX ix_site_media_org_id"))
    conn.execute(text("ALTER TABLE site_media DROP COLUMN org_id"))
    conn.execute(text("ALTER TABLE site_media DROP COLUMN bytes"))
    conn.execute(text("DROP TABLE IF EXISTS org_storage_usage"))
    conn.execute(text("ALTER TABLE fire_dept DROP COLUMN storage_quota_bytes"))
