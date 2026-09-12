"""Bestehende Objektkontakte als zentrale Kontakte uebernehmen.

Revision ID: 0240
Revises: 0239
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.core.telefon import telefon_normalisiert
from app.models.objekt import legacy_telefon_eintrag

revision = "0240"
down_revision = "0239"
branch_labels = None
depends_on = None

_LOGGER = logging.getLogger(__name__)

_KONTAKT = sa.table(
    "kontakt",
    sa.column("id", sa.BigInteger),
    sa.column("org_id", sa.BigInteger),
    sa.column("typ", sa.String),
    sa.column("anzeigename", sa.String),
    sa.column("email", sa.String),
    sa.column("erreichbarkeit", sa.Text),
    sa.column("aktiv", sa.Boolean),
    sa.column("archiviert", sa.Boolean),
    sa.column("version", sa.Integer),
    sa.column("erstellt_am", sa.DateTime),
    sa.column("aktualisiert_am", sa.DateTime),
)
_KONTAKT_TELEFON = sa.table(
    "kontakt_telefon",
    sa.column("org_id", sa.BigInteger),
    sa.column("kontakt_id", sa.BigInteger),
    sa.column("nummer", sa.String),
    sa.column("nummer_normalisiert", sa.String),
    sa.column("label", sa.String),
    sa.column("sort", sa.Integer),
    sa.column("bevorzugt", sa.Boolean),
    sa.column("sms_eignung", sa.Boolean),
)
_FREIGABE = sa.table(
    "objekt_kontakt_freigabe",
    sa.column("org_id", sa.BigInteger),
    sa.column("objekt_kontakt_id", sa.BigInteger),
    sa.column("kanal", sa.String),
    sa.column("ziel_wert", sa.String),
    sa.column("aktiv", sa.Boolean),
)


def _telefone_aus_json(wert: Any, objekt_kontakt_id: int) -> tuple[list[dict[str, Any]], bool]:
    """Liest das historische und das aktuelle Telefon-JSON ohne ORM-Abhaengigkeit."""
    if not wert:
        return [], False
    try:
        werte = json.loads(wert)
    except (TypeError, ValueError):
        _LOGGER.warning("ObjektKontakt %s: telefone_json ist unlesbar und wird uebersprungen", objekt_kontakt_id)
        return [], True
    if not isinstance(werte, list):
        _LOGGER.warning("ObjektKontakt %s: telefone_json ist keine Liste und wird uebersprungen", objekt_kontakt_id)
        return [], True

    ergebnis: list[dict[str, Any]] = []
    for eintrag in werte:
        if isinstance(eintrag, str):
            telefon = legacy_telefon_eintrag(eintrag)
        elif isinstance(eintrag, Mapping):
            nummer = str(eintrag.get("nummer") or "").strip()
            if not nummer:
                continue
            label = str(eintrag.get("label") or "").strip() or None
            telefon = {"nummer": nummer, "label": label, "sms": eintrag.get("sms") is True}
        else:
            continue
        if telefon["nummer"]:
            ergebnis.append(telefon)
    return ergebnis, False


def _plausible_email(wert: Any) -> str | None:
    email = str(wert or "").strip().lower()
    if "@" not in email:
        return None
    lokalteil, domain = email.rsplit("@", 1)
    return email if lokalteil and domain else None


def backfill_objekt_kontakte(conn: sa.Connection) -> dict[str, int]:
    """Migriert noch nicht verknuepfte Objektkontakte ueber eine rohe Connection.

    Die Funktion ist absichtlich von Alembic entkoppelt, damit sie gegen eine
    isolierte SQLite-Datenbank getestet werden kann.
    """
    zaehler = {"migriert": 0, "telefone": 0, "freigaben": 0, "leere_namen": 0, "unlesbare_telefone": 0}
    zeilen = conn.execute(sa.text("""
        SELECT id, org_id, name, telefone_json, email, erreichbarkeit, benachrichtigung_mail
        FROM objekt_kontakt
        WHERE kontakt_id IS NULL
        ORDER BY id
    """)).mappings()

    for zeile in zeilen:
        objekt_kontakt_id = zeile["id"]
        name = str(zeile["name"] or "").strip()
        if not name:
            zaehler["leere_namen"] += 1
            _LOGGER.warning("ObjektKontakt %s: leerer Name, nicht migriert", objekt_kontakt_id)
            continue

        telefone, unlesbar = _telefone_aus_json(zeile["telefone_json"], objekt_kontakt_id)
        if unlesbar:
            zaehler["unlesbare_telefone"] += 1

        ergebnis = conn.execute(
            _KONTAKT.insert().values(
                org_id=zeile["org_id"], typ="person", anzeigename=name, email=zeile["email"],
                erreichbarkeit=zeile["erreichbarkeit"], aktiv=True, archiviert=False, version=0,
                erstellt_am=sa.func.now(), aktualisiert_am=sa.func.now(),
            )
        )
        # Die leichten, eingefrorenen ``sa.table``-Definitionen kennen keinen
        # Primaerschluessel; daher ist ``inserted_primary_key`` leer. Beide
        # unterstuetzten Dialekte liefern fuer den autoincrement-Insert jedoch
        # DBAPI-lastrowid.
        kontakt_id = ergebnis.lastrowid
        if kontakt_id is None:
            raise RuntimeError("Neue Kontakt-ID konnte nicht ermittelt werden")

        vorhandene_freigaben: set[tuple[str, str]] = set()
        for position, telefon in enumerate(telefone):
            nummer = telefon["nummer"]
            nummer_normalisiert = telefon_normalisiert(nummer)
            conn.execute(
                _KONTAKT_TELEFON.insert().values(
                    org_id=zeile["org_id"], kontakt_id=kontakt_id, nummer=nummer,
                    nummer_normalisiert=nummer_normalisiert, label=telefon["label"], sort=position,
                    bevorzugt=False, sms_eignung=None,
                )
            )
            zaehler["telefone"] += 1
            freigabe = ("sms", nummer_normalisiert)
            if telefon["sms"] and freigabe not in vorhandene_freigaben:
                conn.execute(
                    _FREIGABE.insert().values(
                        org_id=zeile["org_id"], objekt_kontakt_id=objekt_kontakt_id,
                        kanal="sms", ziel_wert=nummer_normalisiert, aktiv=True,
                    )
                )
                vorhandene_freigaben.add(freigabe)
                zaehler["freigaben"] += 1

        email = _plausible_email(zeile["email"])
        freigabe = ("mail", email) if email else None
        if zeile["benachrichtigung_mail"] and freigabe and freigabe not in vorhandene_freigaben:
            conn.execute(
                _FREIGABE.insert().values(
                    org_id=zeile["org_id"], objekt_kontakt_id=objekt_kontakt_id,
                    kanal="mail", ziel_wert=email, aktiv=True,
                )
            )
            zaehler["freigaben"] += 1

        conn.execute(
            sa.text("UPDATE objekt_kontakt SET kontakt_id = :kontakt_id WHERE id = :id"),
            {"kontakt_id": kontakt_id, "id": objekt_kontakt_id},
        )
        zaehler["migriert"] += 1

    _LOGGER.info(
        "ObjektKontakt-Backfill: %(migriert)s migriert, %(telefone)s Telefonnummern, "
        "%(freigaben)s Freigaben, %(leere_namen)s leere Namen, %(unlesbare_telefone)s unlesbare Telefonlisten",
        zaehler,
    )
    return zaehler


def upgrade() -> None:
    backfill_objekt_kontakte(op.get_bind())


def downgrade() -> None:
    # Ein Daten-Backfill ist absichtlich nicht reversibel: Ohne eine eigene
    # Herkunftsmarkierung lassen sich die erzeugten zentralen Kontakte und ihre
    # Telefone/Freigaben nicht sicher von spaeter gepflegten Daten unterscheiden.
    pass
