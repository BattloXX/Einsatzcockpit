"""PR 10: avatar_path für User-Profil

Revision ID: 0058
Revises: 0057
Create Date: 2026-06-12 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE user
        ADD COLUMN avatar_path VARCHAR(300) NULL DEFAULT NULL
    """))


def downgrade():
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE user DROP COLUMN avatar_path"))
