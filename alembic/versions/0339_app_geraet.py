"""App-Geraetebericht (schema 1)."""
import sqlalchemy as sa

from alembic import op

revision = "0339"
down_revision = "0338"
branch_labels = None
depends_on = None


def _fk_typ(tabelle: str) -> sa.types.TypeEngine:
    # Altinstallationen koennen INT statt BIGINT haben. MariaDB verlangt
    # identische Groesse und Signedness, daher den tatsaechlichen PK-Typ nutzen.
    if not op.get_context().as_sql:
        for spalte in sa.inspect(op.get_bind()).get_columns(tabelle):
            if spalte["name"] == "id":
                return spalte["type"]
    return sa.Integer()


def upgrade() -> None:
    op.create_table(
        "app_geraet",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", _fk_typ("fire_dept"), sa.ForeignKey("fire_dept.id"), nullable=True),
        sa.Column("user_id", _fk_typ("user"), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_token_id", _fk_typ("device_token"), sa.ForeignKey("device_token.id", ondelete="SET NULL")),
        sa.Column("install_id", sa.String(36), nullable=False),
        sa.Column("app_version", sa.String(30)),
        sa.Column("build_variant", sa.String(20)),
        sa.Column("hersteller", sa.String(80)),
        sa.Column("modell", sa.String(80)),
        sa.Column("geraeteklasse", sa.String(10)),
        sa.Column("android_version", sa.String(40)),
        sa.Column("sicherheitspatch", sa.String(10)),
        sa.Column("aufloesung", sa.String(25)),
        sa.Column("webview_version", sa.String(80)),
        sa.Column("abi", sa.String(80)),
        sa.Column("version_code", sa.Integer()),
        sa.Column("sdk_int", sa.Integer()),
        sa.Column("dichte_dpi", sa.Integer()),
        sa.Column("einstellungen", sa.JSON(), nullable=False),
        sa.Column("berechtigungen", sa.JSON(), nullable=False),
        sa.Column("dienste", sa.JSON(), nullable=False),
        sa.Column("geraet_details", sa.JSON(), nullable=False),
        sa.Column("report_zeiten", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_report_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "install_id", name="uq_app_geraet_install"),
    )
    for feld in ("last_report_at", "sdk_int", "app_version"):
        op.create_index(f"ix_app_geraet_{feld}", "app_geraet", [feld])
    op.create_table(
        "app_geraet_fehler",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("geraet_id", sa.Integer(), sa.ForeignKey("app_geraet.id", ondelete="CASCADE"), nullable=False),
        sa.Column("komponente", sa.String(40), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("text", sa.String(200), nullable=False),
        sa.Column("anzahl", sa.Integer(), nullable=False),
        sa.Column("first_at", sa.DateTime(), nullable=False),
        sa.Column("last_at", sa.DateTime(), nullable=False),
        sa.Column("app_version", sa.String(30)),
        sa.UniqueConstraint("geraet_id", "komponente", "code", name="uq_app_geraet_fehler"),
    )
    op.create_index("ix_app_geraet_fehler_geraet_id", "app_geraet_fehler", ["geraet_id"])


def downgrade() -> None:
    op.drop_table("app_geraet_fehler")
    op.drop_table("app_geraet")
