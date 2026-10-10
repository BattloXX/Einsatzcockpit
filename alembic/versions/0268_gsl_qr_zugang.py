"""GSL QR-Zugang als zweiter Credential-Typ.

Revision ID: 0268
Revises: 0267
"""

import sqlalchemy as sa

from alembic import op

revision = "0268"
down_revision = "0267"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lage_einheit_zugang",
        sa.Column("typ", sa.String(10), nullable=False, server_default=sa.text("'personal'")),
    )
    # Der neue zusammengesetzte Unique-Index bleibt für die einheit_id-FK auf
    # MariaDB nutzbar. Er muss vor dem bisherigen Einzel-Unique entstehen.
    with op.batch_alter_table("lage_einheit_zugang") as batch:
        batch.create_unique_constraint("uq_lage_einheit_zugang_einheit_typ", ["einheit_id", "typ"])
        batch.drop_constraint("uq_lage_einheit_zugang_einheit_id", type_="unique")
    op.add_column(
        "lage_einheit_zugang_session",
        sa.Column("typ", sa.String(10), nullable=False, server_default=sa.text("'personal'")),
    )
    for column in (
        sa.Column("qr_druck_at", sa.DateTime(), nullable=True),
        sa.Column("qr_druck_job_id", sa.Integer(), nullable=True),
        sa.Column("qr_pin_pflicht", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        op.add_column("lage_einheit_zugang", column)
    for column in (
        sa.Column("gk_qr_aktiv", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gk_qr_gueltigkeit_stunden", sa.Integer(), nullable=False, server_default=sa.text("72")),
        sa.Column("gk_qr_pin", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        op.add_column("org_settings", column)


def downgrade() -> None:
    for column in ("gk_qr_pin", "gk_qr_gueltigkeit_stunden", "gk_qr_aktiv"):
        op.drop_column("org_settings", column)
    for column in ("qr_pin_pflicht", "qr_druck_job_id", "qr_druck_at"):
        op.drop_column("lage_einheit_zugang", column)
    op.drop_column("lage_einheit_zugang_session", "typ")
    # Eine Rückkehr zur UNIQUE(einheit_id) ist nur ohne QR-Credentials möglich.
    # Zuerst deren Sitzungen löschen, danach die QR-Zugänge; damit bleiben auf
    # MariaDB keine FKs gegen zu löschende Zeilen zurück.
    op.execute(
        "DELETE FROM lage_einheit_zugang_session WHERE zugang_id IN "
        "(SELECT id FROM lage_einheit_zugang WHERE typ = 'qr')"
    )
    op.execute("DELETE FROM lage_einheit_zugang WHERE typ = 'qr'")
    with op.batch_alter_table("lage_einheit_zugang") as batch:
        # MariaDB requires the FK-supporting composite index to exist until the
        # replacement unique index has been created.
        batch.create_unique_constraint("uq_lage_einheit_zugang_einheit_id", ["einheit_id"])
        batch.drop_constraint("uq_lage_einheit_zugang_einheit_typ", type_="unique")
        batch.drop_column("typ")
