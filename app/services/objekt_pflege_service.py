"""Pflegeauftrag-Kern: Token, Statusworkflow, Arbeitskopie-Anbindung.

Mailversand und UI folgen in einem spaeteren PR.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import hash_api_key
from app.models.kontakt import Kontakt
from app.models.objekt import (
    OBJEKT_KOPIERBARE_FELDER,
    OBJEKT_STATUS_ARCHIVIERT,
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
    KontaktAenderungsvorschlag,
    Objekt,
    ObjektChange,
    ObjektDokument,
    ObjektKontakt,
    ObjektPflegeAbschnitt,
    ObjektPflegeauftrag,
    ObjektPflegeEreignis,
)
from app.services import kontakt_service
from app.services.objekt_service import (
    aktualisiere_felder,
    erstelle_arbeitskopie,
    hole_arbeitskopie,
    uebernimm_arbeitskopie,
    verwirf_arbeitskopie,
)

_TERMINALE_STATUS = {
    PFLEGEAUFTRAG_STATUS_FREIGEGEBEN,
    PFLEGEAUFTRAG_STATUS_VERWORFEN,
    PFLEGEAUFTRAG_STATUS_ABGELAUFEN,
    PFLEGEAUFTRAG_STATUS_WIDERRUFEN,
}

STAMMDATEN_BEREICHE_FELDER: dict[str, tuple[str, ...]] = {
    "stammdaten": ("name", "vulgoname", "informationen"),
    "adresse": ("strasse", "hausnummer", "plz", "ort"),
    "zufahrt": ("anfahrtsweg",),
}
BEREICHE_MIT_EDITFORMULAR = frozenset(STAMMDATEN_BEREICHE_FELDER) | {"kontakte", "dokumente"}
BEREICHE_NUR_BESTAETIGUNG = frozenset({"bma", "gefahren"})


def erzeuge_pflegeauftrag_token() -> tuple[str, str]:
    """Gibt (raw_token, token_hash) zurueck; raw_token nie persistieren."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_api_key(raw)


def hole_offenen_pflegeauftrag(db: Session, objekt: Objekt) -> ObjektPflegeauftrag | None:
    """Der aktuell nicht-terminale Pflegeauftrag eines Objekts, falls vorhanden."""
    return (
        db.query(ObjektPflegeauftrag)
        .filter(
            ObjektPflegeauftrag.objekt_id == objekt.id,
            ObjektPflegeauftrag.status.notin_(_TERMINALE_STATUS),
        )
        .first()
    )


def bereiche_liste(auftrag: ObjektPflegeauftrag) -> list[str]:
    """bereiche_json als Liste, robust gegen leere/kaputte Werte."""
    if not auftrag.bereiche_json:
        return []
    try:
        werte = json.loads(auftrag.bereiche_json)
    except (ValueError, TypeError):
        return []
    return [wert for wert in werte if isinstance(wert, str)]


def hole_oder_erstelle_abschnitt(
    db: Session, auftrag: ObjektPflegeauftrag, bereich: str,
) -> ObjektPflegeAbschnitt:
    """Laedt einen Abschnitt und legt ihn defensiv an, falls er fehlt."""
    abschnitt = (
        db.query(ObjektPflegeAbschnitt)
        .execution_options(include_all_tenants=True)
        .filter(
            ObjektPflegeAbschnitt.pflegeauftrag_id == auftrag.id,
            ObjektPflegeAbschnitt.org_id == auftrag.org_id,
            ObjektPflegeAbschnitt.bereich == bereich,
        )
        .first()
    )
    if abschnitt is None:
        abschnitt = ObjektPflegeAbschnitt(
            org_id=auftrag.org_id, pflegeauftrag_id=auftrag.id, bereich=bereich,
        )
        db.add(abschnitt)
    return abschnitt


def bestaetige_abschnitt(db: Session, auftrag: ObjektPflegeauftrag, bereich: str, *, kontakt_id: int) -> None:
    """Markiert einen Bereich als bestaetigt und protokolliert dies."""
    abschnitt = hole_oder_erstelle_abschnitt(db, auftrag, bereich)
    abschnitt.status = "bestaetigt"
    abschnitt.bestaetigt_am = datetime.now(UTC)
    db.add(ObjektPflegeEreignis(
        org_id=auftrag.org_id, pflegeauftrag_id=auftrag.id, typ="abschnitt_bestaetigt",
        kontakt_id=kontakt_id, text=f"Bereich {bereich} bestaetigt",
    ))


def markiere_abschnitt_geaendert(db: Session, auftrag: ObjektPflegeauftrag, bereich: str) -> None:
    """Markiert einen Bereich nach einer tatsaechlichen Aenderung."""
    abschnitt = hole_oder_erstelle_abschnitt(db, auftrag, bereich)
    abschnitt.status = "geaendert"
    abschnitt.geaendert_am = datetime.now(UTC)


def alle_pflichtbereiche_bearbeitet(db: Session, auftrag: ObjektPflegeauftrag) -> bool:
    """Prueft, ob alle beauftragten Bereiche abgeschlossen bearbeitet wurden."""
    bereiche = bereiche_liste(auftrag)
    if not bereiche:
        return True
    abschnitte = (
        db.query(ObjektPflegeAbschnitt)
        .execution_options(include_all_tenants=True)
        .filter(
            ObjektPflegeAbschnitt.pflegeauftrag_id == auftrag.id,
            ObjektPflegeAbschnitt.org_id == auftrag.org_id,
        )
        .all()
    )
    status = {abschnitt.bereich: abschnitt.status for abschnitt in abschnitte}
    return all(status.get(bereich, "offen") != "offen" for bereich in bereiche)


def rotiere_pflegeauftrag_token(db: Session, auftrag: ObjektPflegeauftrag) -> str:
    """Erzeugt einen neuen Roh-Token fuer 'Link erneut senden' und ersetzt den Hash.
    Der alte Link wird damit sofort ungueltig. Gibt den neuen Roh-Token zurueck
    (nur fuer den sofortigen Mailversand verwenden, nie persistieren). Caller committet."""
    raw, token_hash = erzeuge_pflegeauftrag_token()
    auftrag.token_hash = token_hash
    return raw


def verlaengere_pflegeauftrag(db: Session, auftrag: ObjektPflegeauftrag, zusatz_tage: int) -> None:
    """Verlaengert die Gueltigkeit um zusatz_tage (ab jetzt, nicht ab altem gueltig_bis,
    damit 'Gueltigkeit verlaengern' auf einem bereits abgelaufenen Auftrag sinnvoll bleibt).
    Caller committet."""
    auftrag.gueltig_bis = datetime.now(UTC) + timedelta(days=zusatz_tage)
    _ereignis(db, auftrag, "verlaengert", text=f"Um {zusatz_tage} Tage verlaengert")


ERMITTELT_AKTUALITAET_KEIN_KONTAKT = "kein_kontakt"
ERMITTELT_AKTUALITAET_FREIGABE_ERFORDERLICH = "freigabe_erforderlich"
ERMITTELT_AKTUALITAET_PRUEFUNG_LAEUFT = "pruefung_laeuft"
ERMITTELT_AKTUALITAET_UEBERFAELLIG = "ueberfaellig"
ERMITTELT_AKTUALITAET_BALD_FAELLIG = "bald_faellig"
ERMITTELT_AKTUALITAET_AKTUELL = "aktuell"

AKTUALITAET_LABELS = {
    ERMITTELT_AKTUALITAET_KEIN_KONTAKT: "Kein Ansprechpartner",
    ERMITTELT_AKTUALITAET_FREIGABE_ERFORDERLICH: "Freigabe erforderlich",
    ERMITTELT_AKTUALITAET_PRUEFUNG_LAEUFT: "Prüfung läuft",
    ERMITTELT_AKTUALITAET_UEBERFAELLIG: "Prüfung überfällig",
    ERMITTELT_AKTUALITAET_BALD_FAELLIG: "Prüfung bald fällig",
    ERMITTELT_AKTUALITAET_AKTUELL: "Aktuell",
}


def ermittle_objekt_aktualitaet(
    objekt: Objekt, *, offener_auftrag: ObjektPflegeauftrag | None, hat_email_kontakt: bool,
) -> str:
    """Datenqualitaetsstatus fuer die Objektliste/Detailseite. Prioritaet (hoechste zuerst):
    freigabe_erforderlich > pruefung_laeuft > ueberfaellig > bald_faellig > aktuell.
    kein_kontakt wird nur zurueckgegeben, wenn KEIN offener Auftrag laeuft (sonst waere ein
    laufender Auftrag trotzdem sichtbar/aussagekraeftiger als 'kein Kontakt')."""
    if offener_auftrag is not None:
        if offener_auftrag.status == PFLEGEAUFTRAG_STATUS_EINGEREICHT:
            return ERMITTELT_AKTUALITAET_FREIGABE_ERFORDERLICH
        return ERMITTELT_AKTUALITAET_PRUEFUNG_LAEUFT
    if not hat_email_kontakt:
        return ERMITTELT_AKTUALITAET_KEIN_KONTAKT
    if objekt.revision_datum is None:
        return ERMITTELT_AKTUALITAET_AKTUELL
    heute = date.today()
    if objekt.revision_datum <= heute:
        return ERMITTELT_AKTUALITAET_UEBERFAELLIG
    if (objekt.revision_datum - heute).days <= 30:
        return ERMITTELT_AKTUALITAET_BALD_FAELLIG
    return ERMITTELT_AKTUALITAET_AKTUELL


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
    if objekt.status == OBJEKT_STATUS_ARCHIVIERT:
        raise ValueError("Archivierte Objekte können nicht zur externen Pflege eingeladen werden")
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


def hole_offene_kontakt_vorschlaege(
    db: Session, auftrag: ObjektPflegeauftrag,
) -> list[KontaktAenderungsvorschlag]:
    """Offene Kontakt-Aenderungsvorschlaege dieses Pflegeauftrags."""
    return (
        db.query(KontaktAenderungsvorschlag)
        .filter(
            KontaktAenderungsvorschlag.org_id == auftrag.org_id,
            KontaktAenderungsvorschlag.pflegeauftrag_id == auftrag.id,
            KontaktAenderungsvorschlag.status == "offen",
        )
        .order_by(KontaktAenderungsvorschlag.erstellt_am, KontaktAenderungsvorschlag.id)
        .all()
    )


def hole_wartende_dokumentversionen(db: Session, auftrag: ObjektPflegeauftrag) -> list[ObjektDokument]:
    """Vom Auftrag hochgeladene Dokumentversionen, die noch auf Freigabe warten
    (ist_aktuelle_version=False, freigabe_status='wartet_freigabe', pflegeauftrag_id=auftrag.id)."""
    return (
        db.query(ObjektDokument)
        .filter(
            ObjektDokument.org_id == auftrag.org_id,
            ObjektDokument.pflegeauftrag_id == auftrag.id,
            ObjektDokument.ist_aktuelle_version.is_(False),
            ObjektDokument.freigabe_status == "wartet_freigabe",
        )
        .order_by(ObjektDokument.hochgeladen_am, ObjektDokument.id)
        .all()
    )


def _ereignis_dokument_id(ereignis: ObjektPflegeEreignis) -> int | None:
    try:
        metadaten = json.loads(ereignis.metadaten_json or "{}")
        dokument_id = metadaten.get("dokument_id")
        return int(dokument_id) if dokument_id is not None else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def hole_dokument_ungueltig_meldungen(
    db: Session, auftrag: ObjektPflegeauftrag,
) -> list[ObjektPflegeEreignis]:
    """Ereignisse dieses Auftrags mit typ='dokument_ungueltig_gemeldet', die noch nicht
    entschieden wurden (kein spaeteres 'dokument_ungueltig_entschieden'-Ereignis mit
    derselben dokument_id in metadaten_json existiert)."""
    ereignisse = (
        db.query(ObjektPflegeEreignis)
        .filter(
            ObjektPflegeEreignis.org_id == auftrag.org_id,
            ObjektPflegeEreignis.pflegeauftrag_id == auftrag.id,
            ObjektPflegeEreignis.typ.in_(("dokument_ungueltig_gemeldet", "dokument_ungueltig_entschieden")),
        )
        .order_by(ObjektPflegeEreignis.erstellt_am, ObjektPflegeEreignis.id)
        .all()
    )
    entschieden = {
        dokument_id for ereignis in ereignisse
        if ereignis.typ == "dokument_ungueltig_entschieden"
        if (dokument_id := _ereignis_dokument_id(ereignis)) is not None
    }
    return [
        ereignis for ereignis in ereignisse
        if ereignis.typ == "dokument_ungueltig_gemeldet"
        and _ereignis_dokument_id(ereignis) not in entschieden
    ]


def berechne_stammdaten_diff(db: Session, auftrag: ObjektPflegeauftrag) -> list[dict]:
    """Feldgenauer Diff der externen Stammdaten-Aenderungen dieses Auftrags."""
    if auftrag.arbeitskopie_id is None:
        return []
    kopie = db.get(Objekt, auftrag.arbeitskopie_id)
    if kopie is None:
        return []
    aenderungen = (
        db.query(ObjektChange)
        .filter(
            ObjektChange.org_id == auftrag.org_id,
            ObjektChange.objekt_id == kopie.id,
            ObjektChange.quelle == "extern_pflegeauftrag",
        )
        .order_by(ObjektChange.erstellt_am, ObjektChange.id)
        .all()
    )
    result = []
    for feld in OBJEKT_KOPIERBARE_FELDER:
        aenderung = next((eintrag for eintrag in aenderungen if eintrag.feld == feld), None)
        if aenderung is not None:
            result.append({
                "feld": feld,
                "vorher": json.loads(aenderung.before_json) if aenderung.before_json else None,
                "jetzt": getattr(kopie, feld),
            })
    return result


def freigabe_transaktion(
    db: Session,
    auftrag: ObjektPflegeauftrag,
    *,
    user_id: int,
    kontakt_vorschlag_freigeben: set[int],
    kontakt_vorschlag_verwerfen: set[int],
    dokument_freigeben: set[int],
    dokument_verwerfen: set[int],
    dokument_archivieren: set[int],
    revision_intervall_tage: int = 365,
) -> Objekt:
    """Fuehrt die komplette Freigabe in EINER Transaktion durch."""
    auftrag = (
        db.query(ObjektPflegeauftrag)
        .filter(ObjektPflegeauftrag.id == auftrag.id)
        .with_for_update()
        .one()
    )
    if auftrag.status != PFLEGEAUFTRAG_STATUS_EINGEREICHT:
        raise ValueError("Nur eingereichte Pflegeauftraege koennen freigegeben werden")
    if auftrag.objekt.status == OBJEKT_STATUS_ARCHIVIERT:
        raise ValueError("Das Objekt wurde zwischenzeitlich archiviert — Freigabe nicht möglich")
    offene_kontakte = hole_offene_kontakt_vorschlaege(db, auftrag)
    wartende_dokumente = hole_wartende_dokumentversionen(db, auftrag)
    offene_meldungen = hole_dokument_ungueltig_meldungen(db, auftrag)
    offene_kontakt_ids = {vorschlag.id for vorschlag in offene_kontakte}
    wartende_dokument_ids = {dokument.id for dokument in wartende_dokumente}
    offene_meldung_ids = {_ereignis_dokument_id(meldung) for meldung in offene_meldungen}
    if auftrag.org_id is None:
        raise ValueError("Pflegeauftrag ohne Organisation")

    if (
        kontakt_vorschlag_freigeben & kontakt_vorschlag_verwerfen
        or kontakt_vorschlag_freigeben | kontakt_vorschlag_verwerfen != offene_kontakt_ids
    ):
        raise ValueError("Es liegen unentschiedene Kontaktvorschlaege vor")
    if (
        dokument_freigeben & dokument_verwerfen
        or dokument_freigeben | dokument_verwerfen != wartende_dokument_ids
    ):
        raise ValueError("Es liegen unentschiedene Dokumentversionen vor")
    if not dokument_archivieren <= offene_meldung_ids:
        raise ValueError("Ein zu archivierendes Dokument wurde nicht als ungueltig gemeldet")
    if revision_intervall_tage < 1:
        raise ValueError("Das Revisionsintervall muss mindestens einen Tag betragen")

    kontakt_diffs: dict[int, dict[str, Any]] = {}
    for vorschlag in offene_kontakte:
        if vorschlag.id not in kontakt_vorschlag_freigeben:
            continue
        try:
            diff = json.loads(vorschlag.diff_json)
            if not isinstance(diff, dict) or any(
                feld not in {"funktion", "email", "erreichbarkeit", "notizen"}
                or not isinstance(eintrag, dict) or "neu" not in eintrag
                for feld, eintrag in diff.items()
            ):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Kontaktvorschlag enthaelt keinen gueltigen Diff") from exc
        if kontakt_service.get_kontakt(db, vorschlag.kontakt_id, include_archiviert=True) is None:
            raise ValueError("Kontakt des Vorschlags nicht gefunden")
        kontakt_diffs[vorschlag.id] = diff

    if auftrag.arbeitskopie_id is not None:
        kopie = db.get(Objekt, auftrag.arbeitskopie_id)
        if kopie is None:
            raise ValueError("Arbeitskopie nicht gefunden")
        objekt = uebernimm_arbeitskopie(db, kopie, user_id)
    else:
        objekt = auftrag.objekt

    jetzt = datetime.now(UTC)
    for vorschlag in offene_kontakte:
        if vorschlag.id in kontakt_vorschlag_freigeben:
            diff = kontakt_diffs[vorschlag.id]
            daten = {feld: eintrag["neu"] for feld, eintrag in diff.items()}
            kontakt = kontakt_service.get_kontakt(db, vorschlag.kontakt_id, include_archiviert=True)
            assert kontakt is not None
            telefone = [
                {
                    "nummer": telefon.nummer,
                    "label": telefon.label,
                    "bevorzugt": telefon.bevorzugt,
                    "sms_eignung": telefon.sms_eignung,
                }
                for telefon in kontakt.telefone
            ]
            kategorien = [zuordnung.kategorie.name for zuordnung in kontakt.kategorien]
            kontakt_service.update_kontakt(
                db, vorschlag.kontakt_id, daten, telefone, kategorien,
                version=vorschlag.basis_version, org_id=auftrag.org_id, user_id=user_id,
            )
            vorschlag.status = "uebernommen"
        else:
            vorschlag.status = "verworfen"
        vorschlag.geprueft_am = jetzt
        vorschlag.geprueft_von_id = user_id

    dokumente = {dokument.id: dokument for dokument in wartende_dokumente}
    for dokument_id in dokument_freigeben:
        dokument = dokumente[dokument_id]
        if dokument.dokument_gruppe_id is not None:
            genesis_id = dokument.dokument_gruppe_id
            bisherige = (
                db.query(ObjektDokument)
                .filter(
                    ObjektDokument.org_id == auftrag.org_id,
                    (ObjektDokument.id == genesis_id) | (ObjektDokument.dokument_gruppe_id == genesis_id),
                    ObjektDokument.ist_aktuelle_version.is_(True),
                )
                .first()
            )
            if bisherige is not None:
                bisherige.ist_aktuelle_version = False
                bisherige.freigabe_status = "archiviert"
        dokument.ist_aktuelle_version = True
        dokument.freigabe_status = "freigegeben"
        dokument.freigegeben_am = jetzt
        dokument.freigegeben_von_id = user_id
    for dokument_id in dokument_verwerfen:
        dokumente[dokument_id].freigabe_status = "verworfen"

    for dokument_id in dokument_archivieren:
        archiv_dokument = (
            db.query(ObjektDokument)
            .filter(ObjektDokument.id == dokument_id, ObjektDokument.org_id == auftrag.org_id)
            .first()
        )
        if archiv_dokument is None:
            raise ValueError("Zu archivierendes Dokument nicht gefunden")
        if not archiv_dokument.ist_aktuelle_version:
            raise ValueError("Zu archivierendes Dokument ist nicht die aktuelle Version")
        archiv_dokument.ist_aktuelle_version = False
        archiv_dokument.freigabe_status = "archiviert"
        db.add(ObjektPflegeEreignis(
            org_id=auftrag.org_id,
            pflegeauftrag_id=auftrag.id,
            typ="dokument_archiviert",
            text=f"{archiv_dokument.dateiname_original} archiviert",
            user_id=user_id,
            metadaten_json=json.dumps({"dokument_id": archiv_dokument.id}),
        ))
        db.add(ObjektPflegeEreignis(
            org_id=auftrag.org_id,
            pflegeauftrag_id=auftrag.id,
            typ="dokument_ungueltig_entschieden",
            text=f"{archiv_dokument.dateiname_original} als ungueltig entschieden",
            user_id=user_id,
            metadaten_json=json.dumps({"dokument_id": archiv_dokument.id}),
        ))

    objekt.letzte_bestaetigung_am = jetzt
    objekt.letzte_bestaetigung_kontakt_id = auftrag.kontakt_id
    objekt.letzte_bestaetigung_pflegeauftrag_id = auftrag.id
    objekt.revision_datum = date.today() + timedelta(days=revision_intervall_tage)
    pflegeauftrag_status_wechsel(db, auftrag, PFLEGEAUFTRAG_STATUS_FREIGEGEBEN, user_id=user_id)
    return objekt


def verwerfen_transaktion(db: Session, auftrag: ObjektPflegeauftrag, *, user_id: int) -> None:
    """Verwirft den gesamten Pflegeauftrag und alle noch offenen Nebenentscheidungen."""
    auftrag = (
        db.query(ObjektPflegeauftrag)
        .filter(ObjektPflegeauftrag.id == auftrag.id)
        .with_for_update()
        .one()
    )
    if auftrag.status != PFLEGEAUFTRAG_STATUS_EINGEREICHT:
        raise ValueError("Nur eingereichte Pflegeauftraege koennen verworfen werden")
    verwirf_pflegeauftrag_aenderungen(db, auftrag, user_id=user_id)
    jetzt = datetime.now(UTC)
    for vorschlag in hole_offene_kontakt_vorschlaege(db, auftrag):
        vorschlag.status = "verworfen"
        vorschlag.geprueft_am = jetzt
        vorschlag.geprueft_von_id = user_id
    for dokument in hole_wartende_dokumentversionen(db, auftrag):
        dokument.freigabe_status = "verworfen"
    pflegeauftrag_status_wechsel(db, auftrag, PFLEGEAUFTRAG_STATUS_VERWORFEN, user_id=user_id)


def nacharbeit_anfordern(db: Session, auftrag: ObjektPflegeauftrag, *, user_id: int, text: str) -> str:
    """Fordert Nacharbeit an, rotiert den Token und gibt dessen Rohwert zurueck."""
    if auftrag.status != PFLEGEAUFTRAG_STATUS_EINGEREICHT:
        raise ValueError("Nur eingereichte Pflegeauftraege koennen Nacharbeit erhalten")
    auftrag.nacharbeit_text = text
    raw = rotiere_pflegeauftrag_token(db, auftrag)
    pflegeauftrag_status_wechsel(db, auftrag, PFLEGEAUFTRAG_STATUS_NACHARBEIT, user_id=user_id)
    return raw
