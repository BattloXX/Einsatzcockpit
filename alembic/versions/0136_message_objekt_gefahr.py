"""Board: Meldung ↔ Objektgefahr (Spalte Objektgefahren, Idempotenz + Live-Links)

- message.objekt_gefahr_id (FK objekt_gefahr, ondelete SET NULL)

Revision ID: 0136
Revises: 0135
Create Date: 2026-07-06
"""
from sqlalchemy import text

from alembic import op

revision = "0136"
down_revision = "0135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE `message` "
        "ADD COLUMN `objekt_gefahr_id` BIGINT NULL, "
        "ADD CONSTRAINT `fk_message_objekt_gefahr` FOREIGN KEY (`objekt_gefahr_id`) "
        "REFERENCES `objekt_gefahr` (`id`) ON DELETE SET NULL"
    ))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `message` DROP FOREIGN KEY `fk_message_objekt_gefahr`"))
    op.execute(text("ALTER TABLE `message` DROP COLUMN `objekt_gefahr_id`"))
