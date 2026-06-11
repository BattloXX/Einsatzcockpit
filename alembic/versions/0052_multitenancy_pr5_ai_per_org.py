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

    # 1. OrgSettings: KI-Felder hinzufügen
    conn.execute(text("""
        ALTER TABLE `org_settings`
          ADD COLUMN `ai_mode`                VARCHAR(10)  NOT NULL DEFAULT 'central',
          ADD COLUMN `ai_api_key_enc`          LONGTEXT     NULL DEFAULT NULL,
          ADD COLUMN `ai_monthly_token_quota`  BIGINT       NULL DEFAULT NULL,
          ADD COLUMN `ai_tokens_used_month`    BIGINT       NOT NULL DEFAULT 0,
          ADD COLUMN `ai_tokens_month_key`     VARCHAR(7)   NULL DEFAULT NULL
    """))

    # 2. AIPromptVersion: org_id hinzufügen (nullable für Bestand)
    conn.execute(text(
        "ALTER TABLE `ai_prompt_versions`"
        "  ADD COLUMN `org_id` INT NULL DEFAULT NULL,"
        "  ADD INDEX `ix_ai_prompt_versions_org_id` (`org_id`)"
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
    conn.execute(text(
        "ALTER TABLE `ai_prompt_versions`"
        "  ADD CONSTRAINT `uq_ai_prompt_org_version`"
        "    UNIQUE (`org_id`, `prompt_key`, `version`)"
    ))

    # 5. org_id NOT NULL + FK
    conn.execute(text(
        "ALTER TABLE `ai_prompt_versions`"
        "  MODIFY COLUMN `org_id` INT NOT NULL,"
        "  ADD CONSTRAINT `fk_ai_prompt_versions_org_id`"
        "    FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE CASCADE"
    ))


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0052")
