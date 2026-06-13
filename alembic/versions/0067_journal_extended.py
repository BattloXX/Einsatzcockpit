"""Einsatzjournal: body_html, SKKM-Felder, LageJournalMedia-Tabelle

Revision ID: 0067
Revises: 0066
Create Date: 2026-06-13
"""
from alembic import op
from sqlalchemy import text

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE lage_journal_entry
            ADD COLUMN body_html     MEDIUMTEXT  NULL,
            ADD COLUMN partner_from  VARCHAR(120) NULL,
            ADD COLUMN partner_to    VARCHAR(120) NULL,
            ADD COLUMN measure       VARCHAR(500) NULL
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS lage_journal_media (
            id               INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
            journal_entry_id INT          NOT NULL,
            stored_filename  VARCHAR(64)  NOT NULL,
            original_filename VARCHAR(255) NOT NULL,
            media_type       VARCHAR(12)  NOT NULL DEFAULT 'image',
            uploaded_at      DATETIME     NOT NULL,
            uploaded_by      BIGINT       NULL,
            author_name      VARCHAR(120) NULL,
            bytes            BIGINT       NOT NULL DEFAULT 0,
            org_id           BIGINT       NULL,
            INDEX ix_lage_journal_media_entry (journal_entry_id),
            INDEX ix_lage_journal_media_org   (org_id),
            CONSTRAINT fk_ljm_entry FOREIGN KEY (journal_entry_id)
                REFERENCES lage_journal_entry(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade():
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS lage_journal_media"))
    conn.execute(text("""
        ALTER TABLE lage_journal_entry
            DROP COLUMN body_html,
            DROP COLUMN partner_from,
            DROP COLUMN partner_to,
            DROP COLUMN measure
    """))
