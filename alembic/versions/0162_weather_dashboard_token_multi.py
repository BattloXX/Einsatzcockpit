"""weather_dashboard_token: mehrere beschriftete Tokens je Org statt einem einzigen

Bisher hatte jede Org genau EINEN Dashboard-Token (org_settings.weather_dashboard_token_hash),
gemeinsam genutzt vom Infoscreen-Wanddisplay UND dem neuen WordPress-JSON-Endpoint. Das fuehrte
zu genau dem Support-Fall, der diese Migration ausgeloest hat: ein bereits vorhandener
Infoscreen-Token liess sich nicht "fuer WordPress erzeugen", weil pro Org nur einer existieren
konnte -- ein "Erneuern" haette den Infoscreen sofort invalidiert. Neue Tabelle
weather_dashboard_token erlaubt beliebig viele, individuell beschriftete und unabhaengig
loeschbare Tokens je Org (Muster: lagekarte_token).

Bestehende Tokens werden 1:1 in die neue Tabelle uebernommen (Label "Bestehend (migriert)"),
damit an bereits verbaute Infoscreen-URLs nichts kaputt geht.

Revision ID: 0162
Revises: 0161
Create Date: 2026-07-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

revision = "0162"
down_revision = "0161"
branch_labels = None
depends_on = None


def _col_exists(conn, table: str, col: str) -> bool:
    return col in [c["name"] for c in sa_inspect(conn).get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()

    if "weather_dashboard_token" not in sa_inspect(bind).get_table_names():
        op.create_table(
            "weather_dashboard_token",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("label", sa.String(150), nullable=False),
            sa.Column("org_id", sa.BigInteger(),
                      sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            mysql_charset="utf8mb4",
            mysql_engine="InnoDB",
        )
        op.create_index("ix_weather_dashboard_token_org_id", "weather_dashboard_token", ["org_id"])

    # Bestehende Einzel-Tokens uebernehmen, bevor die alte Spalte verschwindet.
    if _col_exists(bind, "org_settings", "weather_dashboard_token_hash"):
        rows = bind.execute(text(
            "SELECT org_id, weather_dashboard_token_hash FROM org_settings "
            "WHERE weather_dashboard_token_hash IS NOT NULL"
        )).fetchall()
        for org_id, token_hash in rows:
            exists = bind.execute(text(
                "SELECT 1 FROM weather_dashboard_token WHERE token_hash = :h"
            ), {"h": token_hash}).first()
            if not exists:
                bind.execute(text(
                    "INSERT INTO weather_dashboard_token (token_hash, label, org_id, created_at) "
                    "VALUES (:h, :l, :o, NOW())"
                ), {"h": token_hash, "l": "Bestehend (migriert)", "o": org_id})

        op.drop_column("org_settings", "weather_dashboard_token_hash")


def downgrade() -> None:
    bind = op.get_bind()

    if not _col_exists(bind, "org_settings", "weather_dashboard_token_hash"):
        op.add_column(
            "org_settings",
            sa.Column("weather_dashboard_token_hash", sa.String(64), nullable=True),
        )
        # Best effort: je Org den zuletzt erzeugten Token zurueckschreiben (Downgrade ist
        # ohnehin verlustbehaftet, sobald eine Org mehrere Tokens angelegt hat).
        rows = bind.execute(text(
            "SELECT org_id, token_hash FROM weather_dashboard_token "
            "WHERE id IN (SELECT MAX(id) FROM weather_dashboard_token GROUP BY org_id)"
        )).fetchall()
        for org_id, token_hash in rows:
            bind.execute(text(
                "UPDATE org_settings SET weather_dashboard_token_hash = :h WHERE org_id = :o"
            ), {"h": token_hash, "o": org_id})

    if "weather_dashboard_token" in sa_inspect(bind).get_table_names():
        op.drop_table("weather_dashboard_token")
