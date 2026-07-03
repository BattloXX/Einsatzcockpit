"""SMS-Empfang: SmsInbox, SmsForwardRule (+Ziele), OrgSettings-Schalter

Revision ID: 0113
Revises: 0112
Create Date: 2026-07-03
"""
from alembic import op
from sqlalchemy import text

revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Org-Schalter + Default-Webhook ────────────────────────────────────────
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `sms_receive_enabled`            TINYINT(1)   NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS `sms_receive_teams_webhook_url`  VARCHAR(1000) NULL
    """))

    # ── Weiterleitungsregeln ──────────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `sms_forward_rule` (
            `id`                     BIGINT        NOT NULL AUTO_INCREMENT,
            `org_id`                 BIGINT        NULL,
            `name`                   VARCHAR(150)  NOT NULL,
            `enabled`                TINYINT(1)    NOT NULL DEFAULT 1,
            `match_type`             VARCHAR(10)   NOT NULL DEFAULT 'exact',
            `match_number`           VARCHAR(30)   NOT NULL,
            `display_order`          INT           NOT NULL DEFAULT 0,
            `forward_teams`          TINYINT(1)    NOT NULL DEFAULT 0,
            `teams_webhook_url`      VARCHAR(1000) NULL,
            `forward_adhoc_numbers`  LONGTEXT      NULL,
            `prepend_sender`         TINYINT(1)    NOT NULL DEFAULT 1,
            `created_at`             DATETIME      NOT NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_sms_forward_rule_org_id` (`org_id`),
            CONSTRAINT `fk_sfr_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `sms_forward_rule_group` (
            `rule_id`  BIGINT NOT NULL,
            `group_id` BIGINT NOT NULL,
            PRIMARY KEY (`rule_id`, `group_id`),
            CONSTRAINT `fk_sfrg_rule`  FOREIGN KEY (`rule_id`)
                REFERENCES `sms_forward_rule` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_sfrg_group` FOREIGN KEY (`group_id`)
                REFERENCES `sms_group` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `sms_forward_rule_member` (
            `rule_id`   BIGINT NOT NULL,
            `member_id` BIGINT NOT NULL,
            PRIMARY KEY (`rule_id`, `member_id`),
            CONSTRAINT `fk_sfrm_rule`   FOREIGN KEY (`rule_id`)
                REFERENCES `sms_forward_rule` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_sfrm_member` FOREIGN KEY (`member_id`)
                REFERENCES `member` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # ── Empfangs-Log ──────────────────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `sms_inbox` (
            `id`                BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`            BIGINT       NULL,
            `received_at`       DATETIME     NOT NULL,
            `from_number`       VARCHAR(30)  NOT NULL,
            `text`              LONGTEXT     NOT NULL,
            `gateway_token_id`  BIGINT       NULL,
            `processed`         TINYINT(1)   NOT NULL DEFAULT 0,
            `matched_rule_id`   BIGINT       NULL,
            `forward_summary`   VARCHAR(255) NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_sms_inbox_org_id` (`org_id`),
            INDEX `ix_sms_inbox_received_at` (`received_at`),
            CONSTRAINT `fk_sms_inbox_org`   FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_sms_inbox_token` FOREIGN KEY (`gateway_token_id`)
                REFERENCES `sms_gateway_token` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_sms_inbox_rule`  FOREIGN KEY (`matched_rule_id`)
                REFERENCES `sms_forward_rule` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `sms_inbox`"))
    op.execute(text("DROP TABLE IF EXISTS `sms_forward_rule_member`"))
    op.execute(text("DROP TABLE IF EXISTS `sms_forward_rule_group`"))
    op.execute(text("DROP TABLE IF EXISTS `sms_forward_rule`"))

    op.execute(text("""
        ALTER TABLE `org_settings`
        DROP COLUMN IF EXISTS `sms_receive_enabled`,
        DROP COLUMN IF EXISTS `sms_receive_teams_webhook_url`
    """))
