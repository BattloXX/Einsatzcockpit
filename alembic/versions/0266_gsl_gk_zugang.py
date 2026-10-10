"""GSL Gruppenkommandanten-Zugang.

Revision ID: 0266
Revises: 0265
"""

import sqlalchemy as sa

from alembic import op

revision = "0266"
down_revision = "0265"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lage_einheit_zugang",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lage_id", sa.Integer(), sa.ForeignKey("major_incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("einheit_id", sa.Integer(), sa.ForeignKey("lage_einheit.id", ondelete="CASCADE"), nullable=False),
        sa.Column("leader_id", sa.Integer(), sa.ForeignKey("lage_einheit_leader.id", ondelete="SET NULL")),
        sa.Column("phone_e164", sa.String(20)),
        sa.Column("phone_version", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True),
        sa.Column("vorheriger_token_hash", sa.String(64), nullable=True, index=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'kein_token'")),
        sa.Column("widerruf_grund", sa.String(24)),
        sa.Column("widerrufen_at", sa.DateTime()),
        sa.Column("widerrufen_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("ausgestellt_at", sa.DateTime()),
        sa.Column("ausgestellt_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("laeuft_ab_at", sa.DateTime()),
        sa.Column("einloesungen", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("erste_einloesung_at", sa.DateTime()),
        sa.Column("letzte_aktivitaet_at", sa.DateTime()),
        sa.Column("pin_pflicht", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pin_hash", sa.String(64)),
        sa.Column("pin_gueltig_bis", sa.DateTime()),
        sa.Column("pin_versuche", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pin_gesperrt_bis", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("einheit_id", name="uq_lage_einheit_zugang_einheit_id"),
    )
    op.create_index("ix_lage_einheit_zugang_org_id", "lage_einheit_zugang", ["org_id"])
    op.create_index("ix_lage_einheit_zugang_lage_id", "lage_einheit_zugang", ["lage_id"])
    op.create_table(
        "lage_einheit_zugang_session",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
        sa.Column(
            "zugang_id", sa.BigInteger(), sa.ForeignKey("lage_einheit_zugang.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("session_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime()),
        sa.Column("laeuft_ab_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("revoke_grund", sa.String(24)),
        sa.Column("client_kurz", sa.String(120)),
        sa.Column("ip_gruppe", sa.String(45)),
        sa.Column("verifiziert_at", sa.DateTime()),
    )
    op.create_index("ix_lage_einheit_zugang_session_org_id", "lage_einheit_zugang_session", ["org_id"])
    op.create_index("ix_lage_einheit_zugang_session_zugang_id", "lage_einheit_zugang_session", ["zugang_id"])
    op.create_table(
        "lage_einheit_zugang_versand",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
        sa.Column(
            "zugang_id", sa.BigInteger(), sa.ForeignKey("lage_einheit_zugang.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("einheit_id", sa.Integer(), nullable=False),
        sa.Column("leader_id", sa.Integer()),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("kanal", sa.String(24), nullable=False),
        sa.Column("ausloeser", sa.String(12), nullable=False),
        sa.Column("user_id", sa.BigInteger()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("fehler", sa.String(300)),
        sa.Column("ziel_maske", sa.String(30)),
        sa.Column("sms_log_id", sa.BigInteger(), sa.ForeignKey("sms_log.id", ondelete="SET NULL")),
        sa.Column("zeichen", sa.Integer()),
        sa.Column("segmente", sa.Integer()),
        sa.Column("auto_schluessel", sa.String(64), unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("abgeschlossen_at", sa.DateTime()),
    )
    op.create_index("ix_lage_einheit_zugang_versand_org_id", "lage_einheit_zugang_versand", ["org_id"])
    op.create_index("ix_lage_einheit_zugang_versand_zugang_id", "lage_einheit_zugang_versand", ["zugang_id"])
    op.add_column("einheit_aktion", sa.Column("zugang_id", sa.BigInteger(), nullable=True))
    with op.batch_alter_table("einheit_aktion") as batch:
        batch.create_foreign_key(
            "fk_einheit_aktion_zugang", "lage_einheit_zugang", ["zugang_id"], ["id"], ondelete="SET NULL"
        )
    for name, column in (
        ("gk_zugang_aktiv", sa.Column("gk_zugang_aktiv", sa.Boolean(), nullable=False, server_default=sa.false())),
        (
            "gk_zugang_auto_sms",
            sa.Column("gk_zugang_auto_sms", sa.Boolean(), nullable=False, server_default=sa.false()),
        ),
        ("gk_zugang_nachricht", sa.Column("gk_zugang_nachricht", sa.Text())),
        (
            "gk_zugang_gueltigkeit_stunden",
            sa.Column("gk_zugang_gueltigkeit_stunden", sa.Integer(), nullable=False, server_default=sa.text("48")),
        ),
        (
            "gk_sitzung_stunden",
            sa.Column("gk_sitzung_stunden", sa.Integer(), nullable=False, server_default=sa.text("12")),
        ),
        (
            "gk_zugang_max_sitzungen",
            sa.Column("gk_zugang_max_sitzungen", sa.Integer(), nullable=False, server_default=sa.text("2")),
        ),
        ("gk_zugang_sms_pin", sa.Column("gk_zugang_sms_pin", sa.Boolean(), nullable=False, server_default=sa.false())),
        (
            "gk_zugang_ressource_pflegen",
            sa.Column("gk_zugang_ressource_pflegen", sa.Boolean(), nullable=False, server_default=sa.false()),
        ),
    ):
        op.add_column("org_settings", column)


def downgrade() -> None:
    for column in (
        "gk_zugang_ressource_pflegen",
        "gk_zugang_sms_pin",
        "gk_zugang_max_sitzungen",
        "gk_sitzung_stunden",
        "gk_zugang_gueltigkeit_stunden",
        "gk_zugang_nachricht",
        "gk_zugang_auto_sms",
        "gk_zugang_aktiv",
    ):
        op.drop_column("org_settings", column)
    with op.batch_alter_table("einheit_aktion") as batch:
        batch.drop_constraint("fk_einheit_aktion_zugang", type_="foreignkey")
        batch.drop_column("zugang_id")
    # MariaDB requires foreign keys to go before their supporting indexes.
    for table, indexes in (
        (
            "lage_einheit_zugang_versand",
            ("ix_lage_einheit_zugang_versand_zugang_id", "ix_lage_einheit_zugang_versand_org_id"),
        ),
        (
            "lage_einheit_zugang_session",
            ("ix_lage_einheit_zugang_session_zugang_id", "ix_lage_einheit_zugang_session_org_id"),
        ),
        ("lage_einheit_zugang", ("ix_lage_einheit_zugang_lage_id", "ix_lage_einheit_zugang_org_id")),
    ):
        op.drop_table(table)  # drops foreign keys before indexes on MariaDB
