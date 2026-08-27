"""Alten Verleih-Autodruck in Druckregeln ueberfuehren.

Revision ID: 0226
Revises: 0225

Der Downgrade ist bewusst verlustbehaftet: Er stellt nur die Spalte mit Default 0
wieder her. Migrierte Druckregeln bleiben bestehen, die alten Toggle-Werte werden
nicht rekonstruiert.

WIEDERAUFSETZBAR: Fehlt die Altspalte bereits, ist der Upgrade abgeschlossen. Das
ist insbesondere fuer MariaDB wichtig, weil DDL dort sofort committet werden kann.
"""
from __future__ import annotations

from datetime import UTC, datetime
import json

from alembic import op
import sqlalchemy as sa

revision = "0226"
down_revision = "0225"
branch_labels = None
depends_on = None


def _spalten(bind, tabelle: str) -> set[str]:
    return {spalte["name"] for spalte in sa.inspect(bind).get_columns(tabelle)}


def _trigger_spalte(bind) -> str:
    """`trigger` ist in MariaDB ein reserviertes Wort (siehe Backticks in Migration 0144)
    und muss in rohem SQL gequotet werden. SQLite akzeptiert es zwar auch ungequotet,
    daher wuerde ein reiner SQLite-Test den Fehler nicht aufdecken. Das Quoting kommt
    vom Dialekt, damit dieselbe Migration auf beiden Backends laeuft."""
    return bind.dialect.identifier_preparer.quote("trigger")


def _json_liste(raw) -> list:
    if isinstance(raw, list):
        return raw
    try:
        wert = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return wert if isinstance(wert, list) else []


def _json_objekt(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        wert = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return wert if isinstance(wert, dict) else {}


def _hat_wirksame_regel(bind, org_id: int) -> bool:
    zeilen = bind.execute(
        sa.text(
            "SELECT aktiv, documents, printer_ids FROM print_rule "
            f"WHERE org_id=:org_id AND {_trigger_spalte(bind)}=:trigger"
        ),
        {"org_id": org_id, "trigger": "verleih_created"},
    ).mappings()
    return any(
        bool(zeile["aktiv"])
        and "verleih_schein" in _json_liste(zeile["documents"])
        and bool(_json_liste(zeile["printer_ids"]))
        for zeile in zeilen
    )


def _drucker(bind, org_id: int):
    gateway_id = bind.execute(
        sa.text(
            "SELECT id FROM gateway WHERE org_id=:org_id "
            "AND device_token_hash IS NOT NULL ORDER BY id"
        ),
        {"org_id": org_id},
    ).scalar()
    if gateway_id is None:
        return None
    drucker = list(bind.execute(
        sa.text(
            "SELECT id, defaults FROM printer WHERE gateway_id=:gateway_id "
            "AND aktiv=:aktiv ORDER BY name"
        ),
        {"gateway_id": gateway_id, "aktiv": True},
    ).mappings())
    if not drucker:
        return None
    return next(
        (zeile for zeile in drucker if _json_objekt(zeile["defaults"]).get("role") == "standard"),
        drucker[0],
    )


def _regelname(bind, org_id: int) -> str:
    basis = "Verleihschein automatisch"
    namen = set(bind.execute(
        sa.text("SELECT name FROM print_rule WHERE org_id=:org_id"),
        {"org_id": org_id},
    ).scalars())
    if basis not in namen:
        return basis
    nummer = 2
    while f"{basis} ({nummer})" in namen:
        nummer += 1
    return f"{basis} ({nummer})"


def upgrade() -> None:
    bind = op.get_bind()
    if "verleih_autodruck" not in _spalten(bind, "org_settings"):
        return

    org_ids = bind.execute(
        sa.text("SELECT org_id FROM org_settings WHERE verleih_autodruck=:aktiv"),
        {"aktiv": True},
    ).scalars()
    for org_id in list(org_ids):
        if _hat_wirksame_regel(bind, org_id):
            continue
        drucker = _drucker(bind, org_id)
        defaults = _json_objekt(drucker["defaults"]) if drucker else {}
        sort_order = bind.execute(
            sa.text("SELECT COALESCE(MAX(sort_order), 0) FROM print_rule WHERE org_id=:org_id"),
            {"org_id": org_id},
        ).scalar_one()
        jetzt = datetime.now(UTC).replace(tzinfo=None)
        bind.execute(
            sa.text(
                "INSERT INTO print_rule "
                f"(org_id, name, aktiv, {_trigger_spalte(bind)}, filters, documents, "
                "objekt_elements, printer_ids, fallback_printer_id, options, sort_order, "
                "erstellt_am, aktualisiert_am) "
                "VALUES (:org_id, :name, :aktiv, :trigger, :filters, :documents, "
                ":objekt_elements, :printer_ids, :fallback_printer_id, :options, "
                ":sort_order, :erstellt_am, :aktualisiert_am)"
            ),
            {
                "org_id": org_id,
                "name": _regelname(bind, org_id),
                "aktiv": drucker is not None,
                "trigger": "verleih_created",
                "filters": json.dumps({}),
                "documents": json.dumps(["verleih_schein"]),
                "objekt_elements": json.dumps([]),
                "printer_ids": json.dumps([drucker["id"]] if drucker else []),
                "fallback_printer_id": None,
                "options": json.dumps({"copies": 1, "duplex": defaults.get("duplex") or "off"}),
                "sort_order": sort_order + 1,
                "erstellt_am": jetzt,
                "aktualisiert_am": jetzt,
            },
        )

    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("verleih_autodruck")


def downgrade() -> None:
    bind = op.get_bind()
    if "verleih_autodruck" not in _spalten(bind, "org_settings"):
        with op.batch_alter_table("org_settings") as batch:
            batch.add_column(sa.Column(
                "verleih_autodruck", sa.Boolean(), nullable=False, server_default="0"
            ))
