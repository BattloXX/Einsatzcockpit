"""Scopes fuer API-Keys. Revision 0229."""
import sqlalchemy as sa

from alembic import op

revision = "0229"
down_revision = "0228"
branch_labels = None
depends_on = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _spalten(tabelle: str) -> set[str]:
    if tabelle not in _tabellen():
        return set()
    return {spalte["name"] for spalte in sa.inspect(op.get_bind()).get_columns(tabelle)}


def _indizes(tabelle: str) -> set[str]:
    if tabelle not in _tabellen():
        return set()
    insp = sa.inspect(op.get_bind())
    return ({i["name"] for i in insp.get_indexes(tabelle) if i.get("name")} |
            {u["name"] for u in insp.get_unique_constraints(tabelle) if u.get("name")})


def _message_spalten():
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
        sa.Column("api_key_id", sa.BigInteger(), sa.ForeignKey("api_key.id"), nullable=False),
        sa.Column("external_key", sa.String(200), nullable=False),
        sa.Column("kanal", sa.String(10), nullable=False),
        sa.Column("betreff", sa.String(500)), sa.Column("body_text", sa.Text()),
        sa.Column("body_html", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime()), sa.Column("completed_at", sa.DateTime()),
        sa.Column("error_message", sa.Text()),
        sa.Column("sms_log_id", sa.BigInteger(), sa.ForeignKey("sms_log.id", ondelete="SET NULL")),
    ]


def _recipient_spalten():
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.BigInteger(), sa.ForeignKey("api_message.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ziel", sa.String(320), nullable=False), sa.Column("name", sa.String(300)),
        sa.Column("member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("provider", sa.String(40)), sa.Column("error_message", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("next_attempt_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime()),
    ]


def _tabelle_und_spalten(tabelle: str, fabrik) -> None:
    if tabelle not in _tabellen():
        op.create_table(tabelle, *fabrik())
        return
    vorhanden = _spalten(tabelle)
    for spalte in fabrik():
        if spalte.name not in vorhanden:
            with op.batch_alter_table(tabelle) as batch:
                batch.add_column(spalte)


def upgrade() -> None:
    if "scopes" not in _spalten("api_key"):
        with op.batch_alter_table("api_key") as batch:
            batch.add_column(sa.Column(
                "scopes", sa.String(200), nullable=False,
                server_default="einsatz:write,mailing:import",
            ))
    _tabelle_und_spalten("api_message", _message_spalten)
    _tabelle_und_spalten("api_message_recipient", _recipient_spalten)
    for tabelle, name, spalten, unique in (
        ("api_message", "uq_api_message_org_kanal_key", ["org_id", "kanal", "external_key"], True),
        ("api_message", "ix_api_message_org_status_id", ["org_id", "status", "id"], False),
        ("api_message_recipient", "ix_api_message_recipient_message_id", ["message_id"], False),
        ("api_message_recipient", "ix_api_message_recipient_dispatch", ["status", "next_attempt_at"], False),
    ):
        if name not in _indizes(tabelle):
            op.create_index(name, tabelle, spalten, unique=unique)


def downgrade() -> None:
    for tabelle in ("api_message_recipient", "api_message"):
        if tabelle in _tabellen():
            op.drop_table(tabelle)
    if "scopes" in _spalten("api_key"):
        with op.batch_alter_table("api_key") as batch:
            batch.drop_column("scopes")
