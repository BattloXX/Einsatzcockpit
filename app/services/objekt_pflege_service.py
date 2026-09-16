"""Pflegeauftrag-Kern: Token, Statusworkflow, Arbeitskopie-Anbindung.

Mailversand und UI folgen in einem spaeteren PR.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import hash_api_key
from app.models.kontakt import Kontakt
from app.models.objekt import (
    OBJEKT_KOPIERBARE_FELDER,
    PFLEGEAUFTRAG_BEREICHE,
    PFLEGEAUFTRAG_STATUS_ABGELAUFEN,
    PFLEGEAUFTRAG_STATUS_EINGELADEN,
    PFLEGEAUFTRAG_STATUS_EINGEREICHT,
    PFLEGEAUFTRAG_STATUS_FREIGEGEBEN,
    PFLEGEAUFTRAG_STATUS_IN_BEARBEITUNG,
    PFLEGEAUFTRAG_STATUS_NACHARBEIT,
    PFLEGEAUFTRAG_STATUS_UEBERGAENGE,
    PFLEGEAUFTRAG_STATUS_VERWORFEN,
    PFLEGEAUFTRAG_STATUS_WIDERRUFEN,
    Objekt,
    ObjektChange,
    ObjektKontakt,
    ObjektPflegeAbschnitt,
    ObjektPflegeauftrag,
    ObjektPflegeEreignis,
)
from app.services.objekt_service import (
    aktualisiere_felder,
    erstelle_arbeitskopie,
    hole_arbeitskopie,
    verwirf_arbeitskopie,
)


def erzeuge_pflegeauftrag_token() -> tuple[str, str]:
    """Gibt (raw_token, token_hash) zurueck; raw_token nie persistieren."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_api_key(raw)


def erstelle_pflegeauftrag(
    db: Session,
    objekt: Objekt,
    kontakt: Kontakt,
    *,
    ersteller_id: int | None,
    bereiche: list[str],
    auftrag_text: str | None = None,
    gueltig_tage: int = 60,
) -> tuple[ObjektPflegeauftrag, str]:
    """Legt einen neuen Pflegeauftrag inklusive Token und Abschnitten an."""
    if not kontakt.email or not kontakt.email.strip():
        raise ValueError("Der Kontakt hat keine E-Mail-Adresse")
    zugeordnet = (
        db.query(ObjektKontakt)
        .filter(
            ObjektKontakt.objekt_id == objekt.id,
            ObjektKontakt.kontakt_id == kontakt.id,
        )
        .first()
    )
    if zugeordnet is None:
        raise ValueError("Der Kontakt ist dem Objekt nicht zugeordnet")
    if hole_arbeitskopie(db, objekt) is not None:
        raise ValueError("Das Objekt hat bereits eine offene Arbeitskopie")
    terminal = {
        PFLEGEAUFTRAG_STATUS_FREIGEGEBEN,
        PFLEGEAUFTRAG_STATUS_VERWORFEN,
        PFLEGEAUFTRAG_STATUS_ABGELAUFEN,
        PFLEGEAUFTRAG_STATUS_WIDERRUFEN,
    }
    if (
        db.query(ObjektPflegeauftrag)
        .filter(
            ObjektPflegeauftrag.objekt_id == objekt.id,
            ObjektPflegeauftrag.status.notin_(terminal),
        )
        .first()
        is not None
    ):
        raise ValueError("Das Objekt hat bereits einen offenen Pflegeauftrag")
    if any(bereich not in PFLEGEAUFTRAG_BEREICHE for bereich in bereiche):
        raise ValueError("Der Pflegeauftrag enthaelt einen ungueltigen Bereich")

    raw_token, token_hash = erzeuge_pflegeauftrag_token()
    auftrag = ObjektPflegeauftrag(
        org_id=objekt.org_id,
        objekt_id=objekt.id,
        kontakt_id=kontakt.id,
        token_hash=token_hash,
        status=PFLEGEAUFTRAG_STATUS_EINGELADEN,
        bereiche_json=json.dumps(sorted(set(bereiche))),
        auftrag_text=auftrag_text,
        erstellt_von_id=ersteller_id,
        gueltig_bis=datetime.now(UTC) + timedelta(days=gueltig_tage),
    )
    db.add(auftrag)
    db.flush()
    db.add_all(
        ObjektPflegeAbschnitt(org_id=objekt.org_id, pflegeauftrag_id=auftrag.id, bereich=bereich)
        for bereich in sorted(set(bereiche))
    )
    db.add(ObjektPflegeEreignis(org_id=objekt.org_id, pflegeauftrag_id=auftrag.id, typ="erstellt"))
    return auftrag, raw_token


def hole_pflegeauftrag_by_token(db: Session, raw_token: str) -> ObjektPflegeauftrag | None:
    """Tenant-uebergreifender Token-Lookup vor Setzen des Gast-Tenant-Kontexts."""
    return (
        db.query(ObjektPflegeauftrag)
        .execution_options(include_all_tenants=True)
        .filter(ObjektPflegeauftrag.token_hash == hash_api_key(raw_token))
        .first()
    )


def pflegeauftrag_token_gueltig(auftrag: ObjektPflegeauftrag) -> bool:
    """True, wenn der Auftrag noch fuer den Gastzugriff offen und nicht abgelaufen ist."""
    if auftrag.status not in {
        PFLEGEAUFTRAG_STATUS_EINGELADEN,
        PFLEGEAUFTRAG_STATUS_IN_BEARBEITUNG,
        PFLEGEAUFTRAG_STATUS_NACHARBEIT,
    }:
        return False
    gueltig_bis = auftrag.gueltig_bis
    if gueltig_bis.tzinfo is None:
        gueltig_bis = gueltig_bis.replace(tzinfo=UTC)
    return gueltig_bis > datetime.now(UTC)


def _ereignis(
    db: Session, auftrag: ObjektPflegeauftrag, typ: str, *, text: str | None = None, user_id: int | None = None
) -> None:
    db.add(
        ObjektPflegeEreignis(
            org_id=auftrag.org_id,
            pflegeauftrag_id=auftrag.id,
            typ=typ,
            text=text,
            user_id=user_id,
        )
    )


def markiere_pflegeauftrag_zugriff(db: Session, auftrag: ObjektPflegeauftrag) -> None:
    """Protokolliert einen gueltigen Gastzugriff und startet den Auftrag beim ersten Mal."""
    jetzt = datetime.now(UTC)
    erster_zugriff = auftrag.erster_zugriff_am is None
    if erster_zugriff:
        auftrag.erster_zugriff_am = jetzt
    auftrag.letzter_zugriff_am = jetzt
    if erster_zugriff and auftrag.status == PFLEGEAUFTRAG_STATUS_EINGELADEN:
        auftrag.status = PFLEGEAUFTRAG_STATUS_IN_BEARBEITUNG
        _ereignis(db, auftrag, "link_geoeffnet")


def pflegeauftrag_status_wechsel(
    db: Session,
    auftrag: ObjektPflegeauftrag,
    neuer_status: str,
    *,
    user_id: int | None = None,
) -> None:
    """Fuehrt einen gueltigen Statuswechsel aus und protokolliert ihn."""
    if neuer_status not in PFLEGEAUFTRAG_STATUS_UEBERGAENGE.get(auftrag.status, set()):
        raise ValueError(f"Statuswechsel {auftrag.status} -> {neuer_status} nicht erlaubt")
    zeitfelder = {
        PFLEGEAUFTRAG_STATUS_EINGEREICHT: ("abgeschlossen_am", None),
        PFLEGEAUFTRAG_STATUS_FREIGEGEBEN: ("freigegeben_am", "freigegeben_von_id"),
        PFLEGEAUFTRAG_STATUS_NACHARBEIT: ("nacharbeit_am", None),
        PFLEGEAUFTRAG_STATUS_VERWORFEN: ("verworfen_am", "verworfen_von_id"),
        PFLEGEAUFTRAG_STATUS_WIDERRUFEN: ("widerrufen_am", "widerrufen_von_id"),
        PFLEGEAUFTRAG_STATUS_ABGELAUFEN: (None, None),
        PFLEGEAUFTRAG_STATUS_IN_BEARBEITUNG: (None, None),
    }
    zeitfeld, von_feld = zeitfelder[neuer_status]
    jetzt = datetime.now(UTC)
    auftrag.status = neuer_status
    if zeitfeld:
        setattr(auftrag, zeitfeld, jetzt)
    if von_feld:
        setattr(auftrag, von_feld, user_id)
    _ereignis(db, auftrag, neuer_status, user_id=user_id)


def hole_oder_erstelle_arbeitskopie_fuer_auftrag(
    db: Session,
    auftrag: ObjektPflegeauftrag,
    *,
    user_id: int | None = None,
) -> Objekt:
    """Liefert oder erzeugt die erst bei echter externer Aenderung benoetigte Kopie."""
    if auftrag.arbeitskopie_id is not None:
        kopie = db.get(Objekt, auftrag.arbeitskopie_id)
        if kopie is not None:
            return kopie
    kopie = hole_arbeitskopie(db, auftrag.objekt)
    if kopie is not None:
        auftrag.arbeitskopie_id = kopie.id
        _ereignis(db, auftrag, "feld_geaendert", text="An bestehende Arbeitskopie angehaengt")
        return kopie
    kopie = erstelle_arbeitskopie(db, auftrag.objekt, user_id)
    auftrag.arbeitskopie_id = kopie.id
    return kopie


def wende_externe_feldaenderungen_an(
    db: Session,
    auftrag: ObjektPflegeauftrag,
    kopie: Objekt,
    felder: dict[str, Any],
    *,
    kontakt_id: int,
) -> list[str]:
    """Schreibt externe Stammdaten-Diffs mit Herkunftskennzeichnung auf die Kopie."""
    return aktualisiere_felder(
        db,
        kopie,
        felder,
        bereich="stammdaten",
        quelle="extern_pflegeauftrag",
        pflegeauftrag_id=auftrag.id,
        kontakt_id=kontakt_id,
    )


def verwirf_pflegeauftrag_aenderungen(
    db: Session,
    auftrag: ObjektPflegeauftrag,
    *,
    user_id: int | None,
) -> None:
    """Verwirft externe Kopie-Aenderungen, ohne parallele interne Aenderungen zu verlieren."""
    if auftrag.arbeitskopie_id is None:
        return
    kopie = db.get(Objekt, auftrag.arbeitskopie_id)
    if kopie is None:
        return
    aenderungen = (
        db.query(ObjektChange).filter(ObjektChange.objekt_id == kopie.id).order_by(ObjektChange.erstellt_am).all()
    )
    if all(aenderung.quelle == "extern_pflegeauftrag" for aenderung in aenderungen):
        verwirf_arbeitskopie(db, kopie, user_id)
        return
    revert_daten: dict[str, Any] = {}
    for feld in OBJEKT_KOPIERBARE_FELDER:
        externe = [a for a in aenderungen if a.feld == feld and a.quelle == "extern_pflegeauftrag"]
        if externe:
            revert_daten[feld] = json.loads(externe[0].before_json) if externe[0].before_json else None
    if revert_daten:
        aktualisiere_felder(db, kopie, revert_daten, bereich="stammdaten", user_id=user_id)
