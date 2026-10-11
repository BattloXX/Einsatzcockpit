"""Zuordnung eingehender Mails zu Einsaetzen.

Revision ID: 0273
Revises: 0272
"""
import sqlalchemy as sa

from alembic import op

revision = "0273"
down_revision = "0272"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        # Test/local SQLite cannot ALTER-add a foreign key. Production MariaDB
        # uses the direct DDL below.
        with op.batch_alter_table("org_mail_eingang") as batch:
            batch.add_column(sa.Column("incident_id", sa.BigInteger(), nullable=True))
            batch.add_column(sa.Column("message_id_ref", sa.BigInteger(), nullable=True))
            batch.add_column(sa.Column("zugeordnet_at", sa.DateTime(), nullable=True))
            batch.add_column(sa.Column("zuordnung", sa.String(16), nullable=True))
            batch.add_column(sa.Column("zuordnung_grund", sa.String(255), nullable=True))
            batch.create_foreign_key(
                "fk_org_mail_eingang_incident", "incident", ["incident_id"], ["id"], ondelete="SET NULL"
            )
            batch.create_foreign_key(
                "fk_org_mail_eingang_message", "message", ["message_id_ref"], ["id"], ondelete="SET NULL"
            )
            batch.create_index("ix_org_mail_eingang_incident_id", ["incident_id"])
        return
    op.add_column("org_mail_eingang", sa.Column("incident_id", sa.BigInteger(), nullable=True))
    op.add_column("org_mail_eingang", sa.Column("message_id_ref", sa.BigInteger(), nullable=True))
    op.add_column("org_mail_eingang", sa.Column("zugeordnet_at", sa.DateTime(), nullable=True))
    op.add_column("org_mail_eingang", sa.Column("zuordnung", sa.String(16), nullable=True))
    op.add_column("org_mail_eingang", sa.Column("zuordnung_grund", sa.String(255), nullable=True))
    # Eigener Index vor dem FK: MariaDB darf den impliziten FK-Index sonst beim
    # Downgrade nicht entfernen (errno 1553).
    op.create_index("ix_org_mail_eingang_incident_id", "org_mail_eingang", ["incident_id"])
    op.create_foreign_key(
        "fk_org_mail_eingang_incident", "org_mail_eingang", "incident", ["incident_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_org_mail_eingang_message", "org_mail_eingang", "message", ["message_id_ref"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("org_mail_eingang") as batch:
            batch.drop_constraint("fk_org_mail_eingang_message", type_="foreignkey")
            batch.drop_constraint("fk_org_mail_eingang_incident", type_="foreignkey")
            batch.drop_index("ix_org_mail_eingang_incident_id")
            batch.drop_column("zuordnung_grund")
            batch.drop_column("zuordnung")
            batch.drop_column("zugeordnet_at")
            batch.drop_column("message_id_ref")
            batch.drop_column("incident_id")
        return
    op.drop_constraint("fk_org_mail_eingang_message", "org_mail_eingang", type_="foreignkey")
    op.drop_constraint("fk_org_mail_eingang_incident", "org_mail_eingang", type_="foreignkey")
    op.drop_index("ix_org_mail_eingang_incident_id", table_name="org_mail_eingang")
    op.drop_column("org_mail_eingang", "zuordnung_grund")
    op.drop_column("org_mail_eingang", "zuordnung")
    op.drop_column("org_mail_eingang", "zugeordnet_at")
    op.drop_column("org_mail_eingang", "message_id_ref")
    op.drop_column("org_mail_eingang", "incident_id")
