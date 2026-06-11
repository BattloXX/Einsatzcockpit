"""Multi-tenancy PR 5: KI je Org – AIPromptVersion.org_id + OrgSettings AI-Felder

Revision ID: 0052
Revises: 0051
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. OrgSettings: KI-Felder hinzufügen (IF NOT EXISTS – idempotent)
    conn.execute(text("""
        ALTER TABLE `org_settings`
          ADD COLUMN IF NOT EXISTS `ai_mode`                VARCHAR(10)  NOT NULL DEFAULT 'central',
          ADD COLUMN IF NOT EXISTS `ai_api_key_enc`          LONGTEXT     NULL DEFAULT NULL,
          ADD COLUMN IF NOT EXISTS `ai_monthly_token_quota`  BIGINT       NULL DEFAULT NULL,
          ADD COLUMN IF NOT EXISTS `ai_tokens_used_month`    BIGINT       NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS `ai_tokens_month_key`     VARCHAR(7)   NULL DEFAULT NULL
    """))

    # 2. AIPromptVersion: org_id hinzufügen (nullable BIGINT, idempotent)
    conn.execute(text(
        "ALTER TABLE `ai_prompt_versions`"
        "  ADD COLUMN IF NOT EXISTS `org_id` BIGINT NULL DEFAULT NULL,"
        "  ADD INDEX IF NOT EXISTS `ix_ai_prompt_versions_org_id` (`org_id`)"
    ))

    # 3. Bestand auf org_id = MIN(fire_dept.id) setzen
    conn.execute(text(
        "UPDATE `ai_prompt_versions` SET `org_id` = (SELECT MIN(`id`) FROM `fire_dept`) WHERE `org_id` IS NULL"
    ))

    # 4. Alten Unique-Constraint entfernen und neuen mit org_id anlegen
    try:
        conn.execute(text("ALTER TABLE `ai_prompt_versions` DROP INDEX `uq_ai_prompt_version`"))
    except Exception:
        pass
    try:
        conn.execute(text(
            "ALTER TABLE `ai_prompt_versions`"
            "  ADD CONSTRAINT `uq_ai_prompt_org_version`"
            "    UNIQUE (`org_id`, `prompt_key`, `version`)"
        ))
    except Exception:
        pass  # Constraint existiert bereits von einem vorherigen Lauf

    # 5. org_id NOT NULL + FK (MODIFY ist idempotent; FK nur wenn noch nicht vorhanden)
    conn.execute(text(
        "ALTER TABLE `ai_prompt_versions`"
        "  MODIFY COLUMN `org_id` BIGINT NOT NULL"
    ))
    r = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_prompt_versions'"
        " AND CONSTRAINT_NAME = 'fk_ai_prompt_versions_org_id'"
    ))
    if r.scalar() == 0:
        conn.execute(text(
            "ALTER TABLE `ai_prompt_versions`"
            "  ADD CONSTRAINT `fk_ai_prompt_versions_org_id`"
            "    FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE CASCADE"
        ))


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0052")
