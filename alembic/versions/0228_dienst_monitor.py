"""Persistente Dienstueberwachung und Benachrichtigung. Revision 0228."""
import sqlalchemy as sa
from alembic import op

revision = "0228"
down_revision = "0227"
branch_labels = None
depends_on = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _spalten(tabelle: str) -> set[str]:
    return ({s["name"] for s in sa.inspect(op.get_bind()).get_columns(tabelle)}
            if tabelle in _tabellen() else set())


def _indizes(tabelle: str) -> set[str]:
    if tabelle not in _tabellen():
        return set()
    insp = sa.inspect(op.get_bind())
    return ({i["name"] for i in insp.get_indexes(tabelle) if i.get("name")} |
            {u["name"] for u in insp.get_unique_constraints(tabelle) if u.get("name")})


def _status_spalten():
    return [sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
            sa.Column("key", sa.String(50), nullable=False, server_default=""),
            sa.Column("state", sa.String(20), nullable=False, server_default="unknown"),
            sa.Column("since", sa.DateTime()), sa.Column("down_since", sa.DateTime()),
            sa.Column("last_ok_at", sa.DateTime()), sa.Column("last_error", sa.String(500)),
            sa.Column("fail_cycles", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ok_cycles", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_probe_at", sa.DateTime()), sa.Column("last_probe_ok", sa.Boolean()),
            sa.Column("last_probe_error", sa.String(500)), sa.Column("outage_notified_at", sa.DateTime()),
            sa.Column("last_repeat_at", sa.DateTime())]


def _log_spalten():
    return [sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
            sa.Column("key", sa.String(50), nullable=False, server_default=""),
            sa.Column("state", sa.String(20), nullable=False, server_default="unknown"),
            sa.Column("kanal", sa.String(20), nullable=False, server_default="unknown"),
            sa.Column("empfaenger", sa.String(255)),
            sa.Column("betreff", sa.String(255), nullable=False, server_default=""),
            sa.Column("status", sa.String(20), nullable=False, server_default="fehler"),
            sa.Column("fehlertext", sa.String(500)), sa.Column("payload_excerpt", sa.String(1000)),
            sa.Column("gesendet_am", sa.DateTime(), nullable=False, server_default=sa.func.now())]


def _token_spalten():
    return [sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("token_hash", sa.String(64), nullable=False, server_default=""),
            sa.Column("label", sa.String(150), nullable=False, server_default=""),
            sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("last_used_at", sa.DateTime()), sa.Column("expires_at", sa.DateTime()),
            sa.Column("revoked_at", sa.DateTime())]


def _tabelle_und_spalten(tabelle: str, fabrik) -> None:
    """Jede Spalte einzeln pruefen, damit partielle MariaDB-Laeufe fortsetzbar sind."""
    if tabelle not in _tabellen():
        op.create_table(tabelle, *fabrik())
        return
    vorhanden = _spalten(tabelle)
    for spalte in fabrik():
        if spalte.name not in vorhanden:
            with op.batch_alter_table(tabelle) as batch:
                batch.add_column(spalte)


def upgrade() -> None:
    _tabelle_und_spalten("dienst_status", _status_spalten)
    _tabelle_und_spalten("dienst_monitor_log", _log_spalten)
    _tabelle_und_spalten("dienst_monitor_token", _token_spalten)
    for tabelle, name, spalten, unique in (
        ("dienst_status", "ix_dienst_status_org_id", ["org_id"], False),
        ("dienst_status", "uq_dienst_status_org_key", ["org_id", "key"], True),
        ("dienst_monitor_log", "ix_dienst_monitor_log_org_id", ["org_id"], False),
        ("dienst_monitor_log", "ix_dienst_monitor_log_org_gesendet", ["org_id", "gesendet_am"], False),
        ("dienst_monitor_token", "uq_dienst_monitor_token_hash", ["token_hash"], True),
    ):
        if name not in _indizes(tabelle):
            op.create_index(name, tabelle, spalten, unique=unique)
    org_spalten = {
        "dienst_monitor_enabled": sa.Column("dienst_monitor_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        "dienst_monitor_mail": sa.Column("dienst_monitor_mail", sa.String(255)),
        "dienst_monitor_teams_webhook_url": sa.Column("dienst_monitor_teams_webhook_url", sa.String(1000)),
        "dienst_monitor_sms": sa.Column("dienst_monitor_sms", sa.String(255)),
        "dienst_monitor_karenz_min": sa.Column("dienst_monitor_karenz_min", sa.Integer(), nullable=False, server_default="5"),
        "dienst_monitor_wiederholung_min": sa.Column("dienst_monitor_wiederholung_min", sa.Integer(), nullable=False, server_default="60")}
    for name, spalte in org_spalten.items():
        if name not in _spalten("org_settings"):
            with op.batch_alter_table("org_settings") as batch:
                batch.add_column(spalte)
    if "last_heartbeat_at" not in _spalten("sms_gateway_token"):
        with op.batch_alter_table("sms_gateway_token") as batch:
            batch.add_column(sa.Column("last_heartbeat_at", sa.DateTime()))
    if "gateway_offline_alert_min" in _spalten("org_settings"):
        op.execute(sa.text("UPDATE org_settings SET dienst_monitor_karenz_min = gateway_offline_alert_min "
                           "WHERE gateway_offline_alert_min <> 15"))
        with op.batch_alter_table("org_settings") as batch:
            batch.drop_column("gateway_offline_alert_min")
    if "offline_alerted_at" in _spalten("gateway"):
        with op.batch_alter_table("gateway") as batch:
            batch.drop_column("offline_alerted_at")


def downgrade() -> None:
    if "gateway_offline_alert_min" not in _spalten("org_settings"):
        with op.batch_alter_table("org_settings") as batch:
            batch.add_column(sa.Column("gateway_offline_alert_min", sa.Integer(), nullable=False, server_default="15"))
    if "offline_alerted_at" not in _spalten("gateway"):
        with op.batch_alter_table("gateway") as batch:
            batch.add_column(sa.Column("offline_alerted_at", sa.DateTime()))
    if "last_heartbeat_at" in _spalten("sms_gateway_token"):
        with op.batch_alter_table("sms_gateway_token") as batch:
            batch.drop_column("last_heartbeat_at")
    for name in ("dienst_monitor_wiederholung_min", "dienst_monitor_karenz_min", "dienst_monitor_sms",
                 "dienst_monitor_teams_webhook_url", "dienst_monitor_mail", "dienst_monitor_enabled"):
        if name in _spalten("org_settings"):
            with op.batch_alter_table("org_settings") as batch:
                batch.drop_column(name)
    for tabelle in ("dienst_monitor_token", "dienst_monitor_log", "dienst_status"):
        if tabelle in _tabellen():
            op.drop_table(tabelle)
