"""Schema-Abdeckung fuer Migration 0276."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations



def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0276_mail_eingang_quellen.py"
    spec = importlib.util.spec_from_file_location("migration_0276", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_resend_ids_and_downgrades(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0276.db'}")
    metadata = sa.MetaData()
    sa.Table("fire_dept", metadata, sa.Column("id", sa.BigInteger(), primary_key=True))
    sa.Table("org_settings", metadata, sa.Column("id", sa.Integer(), primary_key=True), sa.Column("org_id", sa.BigInteger(), nullable=False), sa.Column("mail_eingang_modus", sa.String(12)))
    sa.Table("org_resend_mail_config", metadata, sa.Column("id", sa.Integer(), primary_key=True), sa.Column("org_id", sa.BigInteger()), sa.Column("inbound_retention_days", sa.Integer()))
    sa.Table("org_smtp_config", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table("org_o365_mail_config", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table("org_mail_eingang", metadata,
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("org_id", sa.BigInteger()),
        sa.Column("resend_email_id", sa.String(64), nullable=False), sa.Column("absender", sa.String(500), nullable=False),
        sa.Column("empfaenger", sa.Text(), nullable=False), sa.Column("betreff", sa.String(998), nullable=False),
        sa.Column("empfangen_at", sa.DateTime(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.UniqueConstraint("org_id", "resend_email_id", name="uq_org_mail_eingang_resend"),
    )
    metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        org_id = 1
        conn.execute(sa.text("INSERT INTO fire_dept (id) VALUES (1)"))
        conn.execute(sa.text("INSERT INTO org_settings (id, org_id) VALUES (1, 1)"))
        conn.execute(sa.text("INSERT INTO org_resend_mail_config (id, org_id, inbound_retention_days) VALUES (1, 1, 42)"))
        conn.execute(sa.text("INSERT INTO org_mail_eingang (org_id, resend_email_id, absender, empfaenger, betreff, empfangen_at, status) VALUES (:org_id, 'same', 'a', '[]', 'b', CURRENT_TIMESTAMP, 'neu')"), {"org_id": org_id})
        migration.upgrade()
        row = conn.execute(sa.text("SELECT quelle, extern_id FROM org_mail_eingang")).one()
        assert row == ("resend", "same")
        assert conn.execute(sa.text("SELECT mail_eingang_aufbewahrung_tage FROM org_settings WHERE org_id = :org_id"), {"org_id": org_id}).scalar() == 42
        conn.execute(sa.text("INSERT INTO org_mail_eingang (org_id, quelle, extern_id, absender, empfaenger, betreff, empfangen_at, status) VALUES (:org_id, 'imap', 'same', 'a', '[]', 'b', CURRENT_TIMESTAMP, 'neu')"), {"org_id": org_id})
        conn.execute(sa.text("DELETE FROM org_mail_eingang WHERE org_id = :org_id AND quelle = 'imap'"), {"org_id": org_id})
        migration.downgrade()
        assert "resend_email_id" in {item["name"] for item in sa.inspect(conn).get_columns("org_mail_eingang")}
