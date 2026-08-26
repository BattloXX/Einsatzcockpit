"""SMS-Freigabe je Telefonnummer.

Revision ID: 0224
Revises: 0223

Der Downgrade ist bewusst verlustbehaftet: Von mehreren SMS-Freigaben bleibt nur
die erste Nummer erhalten. Ein v2-Org-Backup ist auf Stand 0223 nicht importierbar;
alte v1-Backups bleiben auf 0224 durch die Modell-Kompatibilitaet lesbar.

WIEDERAUFSETZBAR: Jeder Schritt prueft erst, ob er noch noetig ist. Grund ist ein
realer Vorfall am 2026-08-26 auf dem Testsystem: MySQL committet DDL sofort
("Will assume non-transactional DDL"), der Constraint-Tausch scheiterte aber mit
Fehler 1553, weil der Fremdschluessel auf incident_id den alten Unique-Index als
Deckung brauchte. Ergebnis: Spalten schon geloescht, alembic_version noch auf 0223 -
ein zweiter Lauf lief in "Unknown column benachrichtigung_sms". Die Pruefungen unten
erlauben es, die Migration nach einem solchen Abbruch einfach erneut zu starten.
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


_UQ = "uq_objekt_kontakt_benachrichtigung"
_UQ_NEU = ["incident_id", "objekt_kontakt_id", "kanal", "empfaenger"]
_UQ_ALT = ["incident_id", "objekt_kontakt_id", "kanal"]


def _spalten(bind, tabelle):
    return {s["name"] for s in sa.inspect(bind).get_columns(tabelle)}


def _unique_spalten(bind, tabelle, name):
    """Spalten des benannten Unique-Constraints, oder None wenn es ihn nicht gibt.

    MySQL bildet Unique-Constraints als Index ab, SQLite meldet sie ueber
    get_unique_constraints - deshalb beide Quellen abfragen.
    """
    insp = sa.inspect(bind)
    for eintrag in insp.get_unique_constraints(tabelle):
        if eintrag["name"] == name:
            return list(eintrag["column_names"])
    for eintrag in insp.get_indexes(tabelle):
        if eintrag["name"] == name and eintrag.get("unique"):
            return list(eintrag["column_names"])
    return None


def _tausche_unique(bind, ziel_spalten):
    """Ersetzt den Unique-Constraint; auf MySQL mit temporaerer FK-Deckung.

    MySQL verweigert das Loeschen des Index mit Fehler 1553, solange er der einzige
    ist, der den Fremdschluessel auf incident_id deckt. Ein temporaerer Index auf
    incident_id haelt die Deckung waehrend des Tauschs aufrecht; danach ist
    incident_id wieder fuehrende Spalte des neuen Constraints und der Hilfsindex
    kann weg.
    """
    if _unique_spalten(bind, "objekt_kontakt_benachrichtigung", _UQ) == ziel_spalten:
        return
    mysql = bind.dialect.name == "mysql"
    if mysql:
        op.create_index("ix_okb_incident_migration", "objekt_kontakt_benachrichtigung", ["incident_id"])
    with op.batch_alter_table("objekt_kontakt_benachrichtigung") as batch:
        batch.drop_constraint(_UQ, type_="unique")
        batch.create_unique_constraint(_UQ, ziel_spalten)
    if mysql:
        op.drop_index("ix_okb_incident_migration", table_name="objekt_kontakt_benachrichtigung")


def upgrade():
    bind = op.get_bind()
    kontakt_spalten = _spalten(bind, "objekt_kontakt")

    # Datenmigration nur, solange die Altspalten noch da sind. Nach einem Abbruch
    # weiter unten sind telefone_json bereits konvertiert und die Spalten weg.
    if {"benachrichtigung_sms", "benachrichtigung_telefon"} <= kontakt_spalten:
        zeilen = bind.execute(sa.text(
            "SELECT id, telefone_json, benachrichtigung_sms, benachrichtigung_telefon FROM objekt_kontakt"
        )).mappings()
        for zeile in zeilen:
            eintraege = _eintraege(zeile["telefone_json"])
            if zeile["benachrichtigung_sms"] and eintraege:
                ziel = _normalisiert(zeile["benachrichtigung_telefon"])
                treffer = next((e for e in eintraege if ziel and _normalisiert(e["nummer"]) == ziel), eintraege[0])
                treffer["sms"] = True
            bind.execute(sa.text("UPDATE objekt_kontakt SET telefone_json=:wert WHERE id=:id"),
                         {"wert": json.dumps(eintraege, ensure_ascii=False) if eintraege else None, "id": zeile["id"]})

    zu_loeschen = [s for s in ("benachrichtigung_telefon", "benachrichtigung_sms") if s in kontakt_spalten]
    if zu_loeschen:
        with op.batch_alter_table("objekt_kontakt") as batch:
            for spalte in zu_loeschen:
                batch.drop_column(spalte)
    if "kontakt_info_enabled" in _spalten(bind, "objekt"):
        with op.batch_alter_table("objekt") as batch:
            batch.drop_column("kontakt_info_enabled")

    _tausche_unique(bind, _UQ_NEU)


def downgrade():
    bind = op.get_bind()
    if "kontakt_info_enabled" not in _spalten(bind, "objekt"):
        with op.batch_alter_table("objekt") as batch:
            batch.add_column(sa.Column("kontakt_info_enabled", sa.Boolean(), nullable=False, server_default="0"))
    kontakt_spalten = _spalten(bind, "objekt_kontakt")
    fehlend = [s for s in ("benachrichtigung_sms", "benachrichtigung_telefon") if s not in kontakt_spalten]
    if fehlend:
        with op.batch_alter_table("objekt_kontakt") as batch:
            if "benachrichtigung_sms" in fehlend:
                batch.add_column(sa.Column("benachrichtigung_sms", sa.Boolean(), nullable=False, server_default="0"))
            if "benachrichtigung_telefon" in fehlend:
                batch.add_column(sa.Column("benachrichtigung_telefon", sa.String(30), nullable=True))
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
    _tausche_unique(bind, _UQ_ALT)
