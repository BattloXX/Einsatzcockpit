"""Versionierte Parserregeln und Testfaelle je Organisation."""

import sqlalchemy as sa

from alembic import op

revision = "0319"
down_revision = "0318"
branch_labels = None
depends_on = None


def _gemeinsame_spalten():
    return (
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("quelle", sa.String(20), nullable=False, server_default="ui"),
        sa.Column("erstellt_von_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
    )


def upgrade() -> None:
    op.create_table(
        "alarm_parser_regelsatz", *_gemeinsame_spalten(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="entwurf"),
        sa.Column("regeln", sa.JSON(), nullable=False),
        sa.Column("notiz", sa.Text()),
        sa.Column("basis_version", sa.Integer()),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("aktiviert_am", sa.DateTime()),
        sa.Column("aktiviert_von_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.UniqueConstraint("org_id", "version", name="uq_alarm_parser_regelsatz_org_version"),
    )
    op.create_table(
        "alarm_parser_testfall", *_gemeinsame_spalten(),
        sa.Column("titel", sa.String(200), nullable=False),
        sa.Column("rohtext", sa.Text(), nullable=False),
        sa.Column("erwartet", sa.JSON(), nullable=False),
        sa.Column("ingest_id", sa.BigInteger(), sa.ForeignKey("alarm_ingest.id", ondelete="SET NULL")),
        sa.Column("aktiv", sa.Boolean(), nullable=False, server_default="1"),
    )
    for table in ("alarm_parser_regelsatz", "alarm_parser_testfall"):
        op.create_index(f"ix_{table}_org_id", table, ["org_id"])


def downgrade() -> None:
    op.drop_table("alarm_parser_testfall")
    op.drop_table("alarm_parser_regelsatz")
