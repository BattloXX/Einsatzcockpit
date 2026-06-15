"""Rename Stabsrolle 'Sichter' zu 'Erkunder'.

Revision ID: 0076
Revises: 0075
Create Date: 2026-06-15
"""
from alembic import op
from sqlalchemy import text

revision = '0076'
down_revision = '0075'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(text(
        "UPDATE gsl_staff_role SET code='ERKUNDER', name='Erkunder' WHERE code='SICHTER'"
    ))


def downgrade() -> None:
    op.get_bind().execute(text(
        "UPDATE gsl_staff_role SET code='SICHTER', name='Sichter' WHERE code='ERKUNDER'"
    ))
