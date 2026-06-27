"""SMS-Gruppen, Einsatzinfo-Empfaenger und SMS-Log

Revision ID: 0105
Revises: 0104
Create Date: 2026-06-27
"""
from alembic import op
from sqlalchemy import text

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Neue Spalten an bestehenden Tabellen ──────────────────────────────────
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `einsatzinfo_sms_enabled`      TINYINT(1) NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS `einsatzinfo_sms_template`     LONGTEXT   NULL,
        ADD COLUMN IF NOT EXISTS `einsatzinfo_sms_send_exercise` TINYINT(1) NOT NULL DEFAULT 0
    """))

    op.execute(text("""
        ALTER TABLE `alarm_type`
        ADD COLUMN IF NOT EXISTS `einsatzinfo_sms_template` LONGTEXT NULL
    """))

    # ── Neue Tabellen ─────────────────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `sms_group` (
            `id`            BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`        INT          NULL,
            `name`          VARCHAR(150) NOT NULL,
            `description`   VARCHAR(500) NULL,
            `display_order` INT          NOT NULL DEFAULT 0,
            `created_at`    DATETIME     NOT NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_sms_group_org_id` (`org_id`),
            CONSTRAINT `fk_sms_group_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `sms_group_member` (
            `sms_group_id` BIGINT NOT NULL,
            `member_id`    BIGINT NOT NULL,
            PRIMARY KEY (`sms_group_id`, `member_id`),
            CONSTRAINT `fk_sgm_group`  FOREIGN KEY (`sms_group_id`)
                REFERENCES `sms_group` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_sgm_member` FOREIGN KEY (`member_id`)
                REFERENCES `member` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `sms_einsatzinfo_recipient` (
            `id`            BIGINT NOT NULL AUTO_INCREMENT,
            `org_id`        INT    NULL,
            `alarm_type_id` BIGINT NULL,
            `group_id`      BIGINT NULL,
            `member_id`     BIGINT NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_seir_org_id`       (`org_id`),
            INDEX `ix_seir_alarm_type`   (`alarm_type_id`),
            CONSTRAINT `fk_seir_org`        FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_seir_alarm_type` FOREIGN KEY (`alarm_type_id`)
                REFERENCES `alarm_type` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_seir_group`      FOREIGN KEY (`group_id`)
                REFERENCES `sms_group` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_seir_member`     FOREIGN KEY (`member_id`)
                REFERENCES `member` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `sms_log` (
            `id`                    BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`                INT          NULL,
            `sent_at`               DATETIME     NOT NULL,
            `source`                VARCHAR(20)  NOT NULL DEFAULT 'manual',
            `alarm_type_code`       VARCHAR(10)  NULL,
            `text`                  LONGTEXT     NOT NULL,
            `recipient_count`       INT          NOT NULL DEFAULT 0,
            `success_count`         INT          NOT NULL DEFAULT 0,
            `triggered_by_user_id`  BIGINT       NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_sms_log_org_id`  (`org_id`),
            INDEX `ix_sms_log_sent_at` (`sent_at`),
            CONSTRAINT `fk_sms_log_org`  FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_sms_log_user` FOREIGN KEY (`triggered_by_user_id`)
                REFERENCES `user` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `sms_log`"))
    op.execute(text("DROP TABLE IF EXISTS `sms_einsatzinfo_recipient`"))
    op.execute(text("DROP TABLE IF EXISTS `sms_group_member`"))
    op.execute(text("DROP TABLE IF EXISTS `sms_group`"))

    op.execute(text("""
        ALTER TABLE `alarm_type`
        DROP COLUMN IF EXISTS `einsatzinfo_sms_template`
    """))

    op.execute(text("""
        ALTER TABLE `org_settings`
        DROP COLUMN IF EXISTS `einsatzinfo_sms_enabled`,
        DROP COLUMN IF EXISTS `einsatzinfo_sms_template`,
        DROP COLUMN IF EXISTS `einsatzinfo_sms_send_exercise`
    """))
