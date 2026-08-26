"""SMS-Freigabe je Telefonnummer.

Revision ID: 0224
Revises: 0223

Der Downgrade ist bewusst verlustbehaftet: Von mehreren SMS-Freigaben bleibt nur
die erste Nummer erhalten. Ein v2-Org-Backup ist auf Stand 0223 nicht importierbar;
alte v1-Backups bleiben auf 0224 durch die Modell-Kompatibilitaet lesbar.
"""
from __future__ import annotations

import json
import re

from alembic import op
import sqlalchemy as sa

revision = "0224"
down_revision = "0223"
branch_labels = None
depends_on = None

_LABELS = ("Telefon beruflich", "Telefon privat", "Mobil beruflich", "Mobil privat", "Pager")


def _legacy(wert):
    text = str(wert).strip()
    for label in _LABELS:
        praefix = f"{label}: "
        if text.startswith(praefix):
            return {"nummer": text[len(praefix):].strip(), "label": label, "sms": False}
    return {"nummer": text, "label": None, "sms": False}


def _normalisiert(wert):
    text = re.sub(r"[\s\-()/]", "", str(wert or "").strip())
    return "+" + text[2:] if text.startswith("00") else text


def _eintraege(raw):
    try:
        werte = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []
    if not isinstance(werte, list):
        return []
    ergebnis = []
    for wert in werte:
        if isinstance(wert, str):
            ergebnis.append(_legacy(wert))
        elif isinstance(wert, dict) and str(wert.get("nummer") or "").strip():
            ergebnis.append({"nummer": str(wert["nummer"]).strip(),
                              "label": str(wert.get("label") or "").strip() or None,
                              "sms": wert.get("sms") is True})
    return ergebnis


def upgrade():
    bind = op.get_bind()
    zeilen = bind.execute(sa.text("SELECT id, telefone_json, benachrichtigung_sms, benachrichtigung_telefon FROM objekt_kontakt")).mappings()
    for zeile in zeilen:
        eintraege = _eintraege(zeile["telefone_json"])
        if zeile["benachrichtigung_sms"] and eintraege:
            ziel = _normalisiert(zeile["benachrichtigung_telefon"])
            treffer = next((e for e in eintraege if ziel and _normalisiert(e["nummer"]) == ziel), eintraege[0])
            treffer["sms"] = True
        bind.execute(sa.text("UPDATE objekt_kontakt SET telefone_json=:wert WHERE id=:id"),
                     {"wert": json.dumps(eintraege, ensure_ascii=False) if eintraege else None, "id": zeile["id"]})
    with op.batch_alter_table("objekt_kontakt") as batch:
        batch.drop_column("benachrichtigung_telefon")
        batch.drop_column("benachrichtigung_sms")
    with op.batch_alter_table("objekt") as batch:
        batch.drop_column("kontakt_info_enabled")
    with op.batch_alter_table("objekt_kontakt_benachrichtigung") as batch:
        batch.drop_constraint("uq_objekt_kontakt_benachrichtigung", type_="unique")
        batch.create_unique_constraint("uq_objekt_kontakt_benachrichtigung",
                                       ["incident_id", "objekt_kontakt_id", "kanal", "empfaenger"])


def downgrade():
    with op.batch_alter_table("objekt") as batch:
        batch.add_column(sa.Column("kontakt_info_enabled", sa.Boolean(), nullable=False, server_default="0"))
    with op.batch_alter_table("objekt_kontakt") as batch:
        batch.add_column(sa.Column("benachrichtigung_sms", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("benachrichtigung_telefon", sa.String(30), nullable=True))
    bind = op.get_bind()
    zeilen = bind.execute(sa.text("SELECT id, objekt_id, telefone_json, benachrichtigung_mail FROM objekt_kontakt")).mappings()
    aktive_objekte = set()
    for zeile in zeilen:
        eintraege = _eintraege(zeile["telefone_json"])
        freigegeben = next((e for e in eintraege if e["sms"]), None)
        if freigegeben or zeile["benachrichtigung_mail"]:
            aktive_objekte.add(zeile["objekt_id"])
        anzeigen = [f'{e["label"]}: {e["nummer"]}' if e["label"] else e["nummer"] for e in eintraege]
        bind.execute(sa.text("UPDATE objekt_kontakt SET telefone_json=:json, benachrichtigung_sms=:sms, benachrichtigung_telefon=:tel WHERE id=:id"),
                     {"json": json.dumps(anzeigen, ensure_ascii=False) if anzeigen else None,
                      "sms": bool(freigegeben), "tel": freigegeben["nummer"] if freigegeben else None, "id": zeile["id"]})
    for objekt_id in aktive_objekte:
        bind.execute(sa.text("UPDATE objekt SET kontakt_info_enabled=1 WHERE id=:id"), {"id": objekt_id})
    with op.batch_alter_table("objekt_kontakt_benachrichtigung") as batch:
        batch.drop_constraint("uq_objekt_kontakt_benachrichtigung", type_="unique")
        batch.create_unique_constraint("uq_objekt_kontakt_benachrichtigung",
                                       ["incident_id", "objekt_kontakt_id", "kanal"])
