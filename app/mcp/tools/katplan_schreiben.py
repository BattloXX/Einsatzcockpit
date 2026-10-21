"""MCP-Entwuerfe im Arbeitsstand; die Freigabe bleibt beim Menschen."""

import asyncio
import contextlib
import json
from datetime import date
from functools import wraps
from pathlib import Path

from app.config import settings
from app.core.audit import write_audit
from app.core.permissions import KATPLAN_LESE_ROLLEN, KATPLAN_VERWALTER_ROLES
from app.mcp.context import MCPContext, MCPPermissionError
from app.mcp.registry import register_tool
from app.mcp.tools.objekt_dokumente import _decode_inhalt
from app.models.katplan import (
    KatplanAbschnitt,
    KatplanDokumentSeite,
    KatplanElement,
    KatplanElementPhase,
    KatplanImportVorschlag,
    KatplanPhase,
    KatplanPlan,
    _utcnow,
)
from app.services import katplan_mcp_service as lesedaten
from app.services import katplan_service as service
from app.services.katplan_akteur_service import validiere_akteure
from app.services.katplan_anhang_service import anhang_hochladen
from app.services.katplan_flags import katplan_effective_enabled
from app.services.katplan_ki_service import _abgleichen, _parse_antwort, text_zu_html
from app.services.katplan_quelle_service import validiere_element_quelle
from app.services.katplan_zugriff import darf_bearbeiten, darf_lesen, darf_ueberpruefung_bestaetigen
from app.services.mcp_datei_url_service import lade_datei_von_url
from app.services.mcp_upload_service import effektives_limit, erstelle_upload, lade_upload_fuer_uebergabe

_FELDER = {
    "titel", "beschreibung", "prioritaet", "daten", "tags", "adresse", "geometrie", "geometrie_von", "kapitel_id",
    "akteure", "phasen", "quelle_dokument_id", "quelle_seiten", "gueltig_bis", "pruefen_am",
}
_HINWEIS = "Nur Arbeitsstand: Ein Bearbeiter muss den Entwurf prüfen und ein Mensch muss ihn freigeben."


def _tool(name: str, description: str, *, lesen: bool = False):
    def decorator(handler):
        @wraps(handler)
        async def aufruf(context: MCPContext, **kwargs) -> dict[str, object]:
            if not (darf_lesen(context.user) if lesen else darf_bearbeiten(context.user)):
                raise MCPPermissionError("Keine Berechtigung")
            if context.org_id != context.user.org_id or not katplan_effective_enabled(context.org_id, context.db):
                raise MCPPermissionError("Werkzeug nicht verfügbar")
            dateien: list[Path] = []
            context.db.info["katplan_mcp_dateien"] = dateien
            try:
                result = await handler(context, **kwargs)
                write_audit(context.db, "mcp." + name, org_id=context.org_id, user_id=context.user.id,
                            entity_type="katplan", entity_id=kwargs.get("plan_id"))
                context.db.commit()
                if not kwargs.get("pruefen"):
                    dateien.clear()
                return result
            except service.KatplanKonflikt as exc:
                context.db.rollback()
                raise ValueError("Versionskonflikt: " + str(exc)) from exc
            except Exception:
                context.db.rollback()
                raise
            finally:
                context.db.info.pop("katplan_mcp_dateien", None)
                for pfad in dateien:
                    pfad.unlink(missing_ok=True)
                for verzeichnis in {p.parent for p in dateien}:
                    # Nur leere Anhangsverzeichnisse entfernen; geteilte Ordner bleiben.
                    with contextlib.suppress(OSError):
                        verzeichnis.rmdir()
        return register_tool(name=name, description=description,
                             required_roles=tuple(KATPLAN_LESE_ROLLEN if lesen else KATPLAN_VERWALTER_ROLES),
                             module_check=katplan_effective_enabled)(aufruf)
    return decorator


def _plan(context: MCPContext, plan_id: int) -> KatplanPlan:
    plan = context.db.query(KatplanPlan).filter_by(org_id=context.org_id, id=plan_id).one_or_none()
    if plan is None:
        raise ValueError("Nicht gefunden")
    if plan.status == "archiviert":
        raise ValueError("Archivierte Pläne können nicht bearbeitet werden.")
    return plan


def _element(context: MCPContext, plan: KatplanPlan, element_id: str) -> KatplanElement:
    element = context.db.query(KatplanElement).filter(
        KatplanElement.org_id == context.org_id, KatplanElement.plan_id == plan.id,
        (KatplanElement.uuid == element_id) | (KatplanElement.kennung == element_id),
    ).one_or_none()
    if element is None:
        raise ValueError("Element nicht gefunden.")
    return element


def _kapitel(context: MCPContext, plan: KatplanPlan, kapitel_id: int | None) -> int | None:
    if kapitel_id is not None and not context.db.query(KatplanAbschnitt).filter_by(
        org_id=context.org_id, plan_id=plan.id, id=kapitel_id,
    ).first():
        raise ValueError("Unbekanntes Kapitel.")
    return kapitel_id


def _phasen(context: MCPContext, plan: KatplanPlan, namen: list[str]) -> set[int]:
    if not isinstance(namen, list) or any(not isinstance(n, str) or not n.strip() or len(n) > 120 for n in namen):
        raise ValueError("Phasen müssen Namen mit 1 bis 120 Zeichen sein.")
    ids = set()
    for name in dict.fromkeys(n.strip() for n in namen):
        # In Python falten: SQL-LOWER() faltet unter SQLite keine Umlaute.
        phase = next((p for p in context.db.query(KatplanPhase).filter_by(org_id=plan.org_id, plan_id=plan.id)
                      if p.name.strip().casefold() == name.casefold()), None)
        if phase is None:
            phase = service.lege_phase_an(context.db, plan, context.user, name=name, quelle="mcp")
        ids.add(phase.id)
    return ids


_ADRESS_FELDER = ("strasse", "hausnummer", "plz", "ort")
_ADRESS_HINWEIS = ('adresse ist ein Objekt mit den Textfeldern strasse, hausnummer, plz und ort, '
                   'null oder "" leert ein Feld; adresse=null leert alle vier Felder. '
                   'z. B. {"strasse": "Eulentobel", "hausnummer": "7", "plz": "6922", "ort": "Wolfurt"}.')


def _adresse(adresse: dict | None) -> dict[str, str]:
    """Adresse pruefen: nur bekannte Schluessel mit Textwerten, leere Werte bleiben leer."""
    if adresse is None:
        return {}
    if not isinstance(adresse, dict):
        raise ValueError(_ADRESS_HINWEIS)
    unbekannt = set(adresse) - set(_ADRESS_FELDER)
    if unbekannt:
        raise ValueError("Unbekannte Adressfelder: " + ", ".join(sorted(unbekannt)) + ". " + _ADRESS_HINWEIS)
    if any(v is not None and not isinstance(v, str) for v in adresse.values()):
        raise ValueError(_ADRESS_HINWEIS)
    return {k: (adresse.get(k) or "").strip() for k in _ADRESS_FELDER if k in adresse}


async def _verorten(context: MCPContext, typ: str, adresse: dict[str, str]) -> tuple[dict | None, dict, dict]:
    """Adresse geokodieren; ohne Ort wird der Ort der Organisation verwendet.

    Liefert (Punkt-Geometrie oder None, ergaenzte Adresse, Ergebnis-Info). Der Punkt ist immer
    ungeprueft; fuer reine Linien-/Flaechentypen wird nicht verortet.
    """
    from app.services.geocoding import search_addresses
    from app.services.katplan_typen import ELEMENT_TYPEN

    if ELEMENT_TYPEN[typ].geometrie not in ("punkt", "beliebig"):
        return None, adresse, {"verortet": False, "hinweis": "Dieser Typ braucht eine Linie oder Fläche."}
    org_ort = (context.user.org.city or "").strip() if context.user.org else ""
    zeile1 = " ".join(v for v in (adresse.get("strasse"), adresse.get("hausnummer")) if v)
    zeile2 = " ".join(v for v in (adresse.get("plz"), adresse.get("ort") or org_ort) if v)
    q = ", ".join(v for v in (zeile1, zeile2) if v)
    if not zeile1 or len(q) < 3:
        return None, adresse, {"verortet": False, "hinweis": "Für die Verortung mindestens die Straße angeben."}
    treffer = await search_addresses(q, limit=3)
    if not treffer:
        return None, adresse, {"verortet": False, "suche": q, "hinweis": "Keine Adresse gefunden."}
    from app.services.katplan_verortung_service import entfernung_m, kandidat

    hausnummer = adresse.get("hausnummer", "").strip().casefold()
    passend = [t for t in treffer if hausnummer and str(t.get("hausnummer") or "").strip().casefold() == hausnummer]
    gleichwertig = passend if passend else treffer
    if len(gleichwertig) > 1 and any(
        entfernung_m(a["lat"], a["lng"], b["lat"], b["lng"]) > 200
        for i, a in enumerate(gleichwertig) for b in gleichwertig[i + 1:]
    ):
        return None, adresse, {
            "verortet": False, "kandidaten": [kandidat("adresse", t, context.user.org) for t in gleichwertig],
            "hinweis": "Mehrdeutig; mit katplan_elemente_verorten(auswahl=...) waehlen.",
        }
    t = gleichwertig[0]
    ergaenzt = {**adresse, **{k: t[k] for k in _ADRESS_FELDER if t.get(k) and not adresse.get(k)}}
    return ({"type": "Point", "coordinates": [t["lng"], t["lat"]]}, ergaenzt,
            {"verortet": True, "suche": q, "treffer": t["display_name"], "geo_status": "ungeprueft",
             "hinweis": "Punkt aus der Adresse gesetzt; Lage vor Ort prüfen und im Editor als geprüft markieren."})


def _werte(typ: str, *, titel: str, beschreibung: str, prioritaet: int, daten: dict,
           tags: list[str], adresse: dict, geometrie: dict | None,
           verworfen: list[str] | None = None, akteure: list[str] | None = None) -> dict:
    if not isinstance(titel, str) or not isinstance(beschreibung, str):
        raise ValueError("Titel und Beschreibung müssen Text sein.")
    if type(prioritaet) is not int:
        raise ValueError("Priorität muss 1, 2 oder 3 sein.")
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise ValueError("Tags müssen eine Textliste sein.")
    adresse = _adresse(adresse)
    if geometrie is not None and not isinstance(geometrie, dict):
        raise ValueError("Geometrie muss ein GeoJSON-Objekt sein.")
    return service.validiere_element_felder(
        typ, titel=titel, prioritaet=prioritaet, daten=daten, verworfen=verworfen, akteure=akteure,
        tags=[t.strip() for t in tags if t.strip()], adresse=adresse,
        beschreibung_html=text_zu_html(beschreibung),
        geometrie=json.dumps(geometrie) if geometrie is not None else None,
    )


def _feldwerte(context: MCPContext, element: KatplanElement) -> dict:
    """Gespeicherte Werte nach Eingabeschluesseln fuer den Wirkungsvergleich."""
    return {
        "typ": element.typ, "titel": element.titel, "beschreibung": element.beschreibung_html,
        "prioritaet": element.prioritaet, "daten": json.loads(element.daten_json or "{}"),
        "tags": json.loads(element.tags_json or "[]"),
        "akteure": json.loads(element.akteure_json or "[]"),
        "adresse": {k: getattr(element, k) or "" for k in _ADRESS_FELDER},
        "geometrie": (element.geometry_json, element.lat, element.lng, element.geo_status),
        "geometrie_von": (element.geometry_json, element.lat, element.lng, element.geo_status),
        "kapitel_id": element.abschnitt_id, "kapitel_titel": element.abschnitt_id,
        "phasen": {r.phase_id for r in context.db.query(KatplanElementPhase).filter_by(
            org_id=context.org_id, element_id=element.id)},
        **{k: getattr(element, k) for k in (
            "quelle_dokument_id", "quelle_seiten", "gueltig_bis", "pruefen_am")},
    }


def _antwort(context: MCPContext, plan: KatplanPlan, element: KatplanElement, *,
             geaendert: list[str], verworfen: list[str], pruefen: bool,
             unveraendert: list[str] | None = None) -> dict[str, object]:
    context.db.flush()
    _, elemente, _, _ = lesedaten.fassung_laden(context.db, context.user, plan, "arbeitsstand")
    antwort: dict[str, object] = {
        "element_id": element.uuid, "id": element.id, "kennung": element.kennung,
        "version": element.version, "hinweis": _HINWEIS, "geaendert": geaendert,
        "verworfen": verworfen, "gespeichert": not pruefen,
        "element": next(e for e in elemente if e["element_id"] == element.uuid),
    }
    if unveraendert is not None:
        antwort["unveraendert"] = unveraendert
    if pruefen:
        context.db.rollback()
    return antwort


def _pruefe_element_version(element: KatplanElement, version: int) -> None:
    if element.version != version:
        raise ValueError(
            f"Versionskonflikt: Element {element.kennung} hat version {element.version}, übergeben {version}. "
            'Mit katplan_element_lesen(fassung="arbeitsstand") neu lesen.'
        )


def _schreibe_element_werte(context: MCPContext, plan: KatplanPlan, element: KatplanElement,
                            werte: dict) -> None:
    for feld, wert in werte.items():
        alt = getattr(element, feld)
        if alt != wert:
            setattr(element, feld, wert)
            service.protokolliere(context.db, plan, "element", "geaendert", context.user,
                                  feld={"daten_json": "daten", "geometry_json": "geometrie"}.get(feld, feld),
                                  alt=str(alt) if alt is not None else None,
                                  neu=str(wert) if wert is not None else None, element_id=element.id, quelle="mcp")


def _element_geaendert(context: MCPContext, plan: KatplanPlan, element: KatplanElement) -> None:
    element.version += 1
    element.aktualisiert_am, element.aktualisiert_von_id = _utcnow(), context.user.id
    service.aktualisiere_element_suchtext(element)
    service.markiere_ueberarbeitung(context.db, plan, context.user, quelle="mcp")


_SCHREIB_HINWEIS = (
    " Telefonnummern nicht in Texte schreiben, sondern zentrale Kontakte mit katplan_verknuepfung_setzen verknüpfen. "
    " akteure: Liste von Schlüsseln, Katalog über katplan_lesen; null leert die Zuordnung. "
    " Zahlen in daten-Textfeldern und Listen werden zu Text (500 wird zu \"500\"); bool ist kein Text. "
    "quelle_dokument_id verweist auf die Quelle, quelle_seiten enthält Seiten wie 3-4,7. "
    "quelle_dokument_id=null entfernt auch Seiten, sofern quelle_seiten nicht übergeben wird. "
    "geometrie=null entfernt den Ort; gueltig_bis, pruefen_am und kapitel_id werden mit null oder leer geleert. "
    "Antwort: geaendert, unveraendert (beim Ändern), verworfen (Pfad und Grund), gespeichert und element "
    "im Leseformat des Arbeitsstands. Für Folgeänderungen version aus "
    "katplan_element_lesen(fassung=arbeitsstand) verwenden, nicht element_version_id. "
    "streng=true lehnt Datenverlust ab; pruefen=true validiert als "
    "Trockenlauf mit Rollback und gespeichert=false."
)


@_tool("katplan_element_entwurf_anlegen", (
    "Legt einen KI-Entwurf nur im Arbeitsstand an; menschliche Freigabe nötig. " + _ADRESS_HINWEIS
    + " verorten=true setzt aus der Adresse einen ungeprüften Punkt (ohne Ort wird der Ort der Organisation "
    "verwendet); mehrdeutige Treffer liefern Kandidaten zur Auswahl mit katplan_elemente_verorten. "
    "geometrie (GeoJSON) hat Vorrang. typ: info, massnahme, kontrollstelle, gefahrenbereich, "
    "ablauf, ressource, medien, zustaendigkeit, objektverweis." + _SCHREIB_HINWEIS
))
async def katplan_element_entwurf_anlegen(
    context: MCPContext, plan_id: int, typ: str, titel: str, beschreibung: str = "",
    kapitel_id: int | None = None, kapitel_titel: str | None = None, phasen: list[str] | None = None,
    prioritaet: int = 2, daten: dict | None = None, tags: list[str] | None = None,
    adresse: dict | None = None, geometrie: dict | None = None,
    quelle_dokument_id: int | None = None, quelle_seiten: str | None = None, verorten: bool = False,
    streng: bool = False, pruefen: bool = False, akteure: list[str] | None = None,
) -> dict[str, object]:
    plan = _plan(context, plan_id)
    from app.services.katplan_typen import ELEMENT_TYPEN
    if typ not in ELEMENT_TYPEN:
        raise ValueError("Unbekannter Elementtyp. Erlaubt: " + ", ".join(ELEMENT_TYPEN))
    eingaben = dict(locals())
    verworfen: list[str] = []
    adresse_werte = _adresse(adresse)
    verortung = None
    if verorten and geometrie is None:
        geometrie, adresse_werte, verortung = await _verorten(context, typ, adresse_werte)
    werte = _werte(typ, titel=titel, beschreibung=beschreibung, prioritaet=prioritaet,
                   daten=daten if daten is not None else {}, tags=tags if tags is not None else [],
                   adresse=adresse_werte, geometrie=geometrie, verworfen=verworfen,
                   akteure=validiere_akteure(context.db, plan.org_id, akteure if akteure is not None else []))
    if streng and verworfen:
        raise ValueError("Nicht gespeichert: " + "; ".join(verworfen))
    validiere_element_quelle(context.db, plan, quelle_dokument_id, quelle_seiten or "")
    if kapitel_id is not None and kapitel_titel is not None:
        raise ValueError("Entweder kapitel_id oder kapitel_titel angeben.")
    _kapitel(context, plan, kapitel_id)
    if kapitel_titel is not None:
        titel_kapitel = kapitel_titel.strip()
        kapitel = next((a for a in context.db.query(KatplanAbschnitt).filter_by(org_id=plan.org_id, plan_id=plan.id)
                        if a.titel.strip().casefold() == titel_kapitel.casefold()), None)
        if kapitel is None:
            kapitel = service.lege_abschnitt_an(context.db, plan, context.user, titel=titel_kapitel, quelle="mcp")
        kapitel_id = kapitel.id
    phase_ids = _phasen(context, plan, phasen if phasen is not None else [])
    element = service.lege_element_an(
        context.db, plan, context.user, typ=typ, titel=werte["titel"], abschnitt_id=kapitel_id,
        daten=json.loads(werte["daten_json"]), beschreibung_html=werte["beschreibung_html"],
        herkunft="ai_suggested", quelle_dokument_id=quelle_dokument_id, quelle_seiten=quelle_seiten, quelle="mcp",
    )
    for feld, wert in werte.items():
        setattr(element, feld, wert)
    for pid in phase_ids:
        context.db.add(KatplanElementPhase(org_id=plan.org_id, element_id=element.id, phase_id=pid))
    service.aktualisiere_element_suchtext(element)
    context.db.flush()
    gespeichert = _feldwerte(context, element)
    geaendert = [k for k, v in eingaben.items() if k in gespeichert and v not in (None, "", [], {})
                and gespeichert[k] not in (None, "", [], {}, set())
                and (k != "adresse" or any(gespeichert[k].values()))]
    antwort = _antwort(context, plan, element, geaendert=geaendert, verworfen=verworfen, pruefen=pruefen)
    if verortung is not None:
        antwort["verortung"] = verortung
    return antwort


@_tool("katplan_element_entwurf_aendern", (
    "Ändert erlaubte Entwurfsfelder mit Versionsprüfung nur im Arbeitsstand. felder darf enthalten: titel, "
    "beschreibung, prioritaet, daten, tags, akteure, adresse, geometrie, geometrie_von, kapitel_id, phasen, "
    "quelle_dokument_id, "
    "quelle_seiten, gueltig_bis, pruefen_am. "
    'geometrie_von: {"objekt_id": n} oder {"wasserstelle_id": n} übernimmt einen ungeprüften Stammdatenpunkt; '
    "nicht zusammen mit geometrie. " + _ADRESS_HINWEIS + " Nur übergebene Adressfelder werden "
    "geändert. verorten=true setzt aus der (neuen) Adresse einen ungeprüften Punkt; mehrdeutige Treffer "
    "liefern Kandidaten zur Auswahl mit katplan_elemente_verorten. "
    "version aus katplan_element_lesen(fassung=arbeitsstand), Feld version, NICHT element_version_id, verwenden."
    + _SCHREIB_HINWEIS
))
async def katplan_element_entwurf_aendern(context: MCPContext, plan_id: int, element_id: str,
                                         version: int, felder: dict, verorten: bool = False,
                                         streng: bool = False, pruefen: bool = False) -> dict[str, object]:
    plan = _plan(context, plan_id)
    element = _element(context, plan, element_id)
    _pruefe_element_version(element, version)
    if not isinstance(felder, dict):
        raise ValueError("felder muss ein Objekt sein.")
    verboten = set(felder) - _FELDER
    if verboten:
        raise ValueError("Nicht erlaubte Felder: " + ", ".join(sorted(verboten)))
    eingaben = list(felder)
    vorher = _feldwerte(context, element)
    verworfen: list[str] = []
    felder = dict(felder)
    if "geometrie_von" in felder:
        from app.services.katplan_ziel_service import ziel_laden, ziel_verfuegbar

        referenz = felder.pop("geometrie_von")
        if "geometrie" in felder:
            raise ValueError("geometrie_von nicht zusammen mit geometrie angeben.")
        if not isinstance(referenz, dict) or len(referenz) != 1 or not set(referenz) <= {
            "objekt_id", "wasserstelle_id",
        }:
            raise ValueError("geometrie_von braucht objekt_id oder wasserstelle_id.")
        key, ziel_id = next(iter(referenz.items()))
        ziel_typ = key.removesuffix("_id")
        if type(ziel_id) is not int or not ziel_verfuegbar(context.db, context.org_id, ziel_typ):
            raise ValueError("Stammdatenziel nicht verfügbar.")
        ziel = ziel_laden(context.db, context.org_id, ziel_typ, ziel_id)
        if ziel is None:
            raise ValueError("Stammdatenziel nicht gefunden.")
        if ziel.lat is None or ziel.lng is None:
            raise ValueError("Stammdatenziel hat keine Koordinaten.")
        felder["geometrie"] = {"type": "Point", "coordinates": [ziel.lng, ziel.lat]}
    if "adresse" in felder:
        # Teiladressen mit dem Bestand zusammenfuehren, damit {"hausnummer": "7"} die Straße nicht loescht.
        bestand = {k: getattr(element, k) or "" for k in _ADRESS_FELDER}
        felder["adresse"] = ({k: "" for k in _ADRESS_FELDER} if felder["adresse"] is None
                             else {**bestand, **_adresse(felder["adresse"])})
    verortung = None
    if verorten and "geometrie" not in felder:
        adresse_neu = felder.get("adresse") or {k: getattr(element, k) or "" for k in _ADRESS_FELDER}
        geom, ergaenzt, verortung = await _verorten(context, element.typ, adresse_neu)
        if geom is not None:
            felder["geometrie"] = geom
            felder["adresse"] = ergaenzt
    # Nur uebergebene Felder validieren und schreiben; gepruefte Bestandsgeometrie bleibt erhalten.
    scalar = {"titel", "beschreibung", "prioritaet", "daten", "tags", "adresse", "geometrie"}
    werte = {}
    if scalar & set(felder):
        defaults = {"titel": element.titel, "beschreibung": "", "prioritaet": element.prioritaet,
                    "daten": json.loads(element.daten_json or "{}"), "tags": json.loads(element.tags_json or "[]"),
                    "adresse": {k: getattr(element, k) or "" for k in ("strasse", "hausnummer", "plz", "ort")},
                    "geometrie": None}
        defaults.update({k: v for k, v in felder.items() if k in scalar})
        alle = _werte(element.typ, **defaults, verworfen=verworfen if "daten" in felder else None)
        if streng and verworfen:
            raise ValueError("Nicht gespeichert: " + "; ".join(verworfen))
        mapping = {"beschreibung": ("beschreibung_html",), "daten": ("daten_json",), "tags": ("tags_json",),
                   "adresse": ("strasse", "hausnummer", "plz", "ort"),
                   "geometrie": ("geometry_json", "lat", "lng", "geo_status")}
        for key in scalar & set(felder):
            for feld in mapping.get(key, (key,)):
                werte[feld] = alle[feld]
    if "akteure" in felder:
        keys = validiere_akteure(context.db, plan.org_id, [] if felder["akteure"] is None else felder["akteure"])
        werte["akteure_json"] = json.dumps(keys, ensure_ascii=False) if keys else None
    if "kapitel_id" in felder:
        werte["abschnitt_id"] = _kapitel(context, plan, None if felder["kapitel_id"] == "" else felder["kapitel_id"])
    for feld in ("gueltig_bis", "pruefen_am"):
        if feld in felder:
            if felder[feld] is not None and not isinstance(felder[feld], str):
                raise ValueError(f"{feld}: Datum als YYYY-MM-DD angeben.")
            werte[feld] = date.fromisoformat(felder[feld]) if felder[feld] else None
    if {"quelle_dokument_id", "quelle_seiten"} & set(felder):
        qid = felder.get("quelle_dokument_id", element.quelle_dokument_id)
        seiten = felder.get("quelle_seiten", "" if "quelle_dokument_id" in felder and qid is None
                            else element.quelle_seiten) or ""
        validiere_element_quelle(context.db, plan, qid, seiten)
        werte.update(quelle_dokument_id=qid, quelle_seiten=seiten or None)
    phase_ids = _phasen(context, plan, felder["phasen"]) if "phasen" in felder else None
    _schreibe_element_werte(context, plan, element, werte)
    if phase_ids is not None:
        rows = context.db.query(KatplanElementPhase).filter_by(org_id=plan.org_id, element_id=element.id).all()
        alt_ids = {r.phase_id for r in rows}
        if alt_ids != phase_ids:
            for row in rows:
                if row.phase_id not in phase_ids:
                    context.db.delete(row)
            for pid in phase_ids - alt_ids:
                context.db.add(KatplanElementPhase(org_id=plan.org_id, element_id=element.id, phase_id=pid))
            service.protokolliere(context.db, plan, "element", "geaendert", context.user, feld="phasen",
                                  alt=json.dumps(sorted(alt_ids)), neu=json.dumps(sorted(phase_ids)),
                                  element_id=element.id, quelle="mcp")
    _element_geaendert(context, plan, element)
    context.db.flush()
    nachher = _feldwerte(context, element)
    antwort = _antwort(context, plan, element,
                       geaendert=[k for k in eingaben if vorher[k] != nachher[k]],
                       unveraendert=[k for k in eingaben if vorher[k] == nachher[k]],
                       verworfen=verworfen, pruefen=pruefen)
    if verortung is not None:
        antwort["verortung"] = verortung
    return antwort


@_tool("katplan_anhang_upload_vorbereiten", "Bereitet einen kurzlebigen Upload für einen Anhang im Arbeitsstand vor.")
async def katplan_anhang_upload_vorbereiten(context: MCPContext, plan_id: int, dateiname: str,
                                           groesse_bytes: int | None = None,
                                           element_id: str | None = None) -> dict[str, object]:
    plan = _plan(context, plan_id)
    if element_id is not None:
        _element(context, plan, element_id)
    row, token = erstelle_upload(context.db, context.user, context.org_id, None, dateiname,
                                groesse_bytes, zweck="katplan_anhang")
    url = f"{settings.effective_public_base_url.rstrip('/')}/api/mcp/uploads/{row.upload_id}"
    return {"upload_id": row.upload_id, "upload_url": url, "upload_token": token,
            "gueltig_bis": row.expires_at.isoformat() + "Z", "max_bytes": effektives_limit(context.db),
            "curl_beispiel": f'curl -X POST "{url}" -H "Authorization: Bearer {token}" -F "datei=@{row.dateiname}"'}


@_tool("katplan_anhang_uebergeben",
       "Übergibt einen Anhang per upload_id, Base64 oder datei.download_url an den Arbeitsstand.")
async def katplan_anhang_uebergeben(
    context: MCPContext, plan_id: int, upload_id: str | None = None, inhalt_base64: str | None = None,
    datei: dict | None = None, dateiname: str | None = None, element_id: str | None = None,
    beschriftung: str = "", urheber: str = "",
) -> dict[str, object]:
    plan = _plan(context, plan_id)
    element = _element(context, plan, element_id) if element_id is not None else None
    if sum(w is not None for w in (upload_id, inhalt_base64, datei)) != 1:
        raise ValueError("Genau eines von upload_id, inhalt_base64 oder datei ist erforderlich.")
    upload = None
    pfad = None
    if upload_id is not None:
        upload = lade_upload_fuer_uebergabe(context.db, context.org_id, context.user.id, upload_id,
                                          zweck="katplan_anhang")
        assert upload.pfad is not None
        pfad = Path(settings.OBJEKT_MEDIA_DIR) / upload.pfad
        data = pfad.read_bytes()
        dateiname = dateiname or upload.dateiname
    elif datei is not None:
        if not isinstance(datei, dict) or not isinstance(datei.get("download_url"), str):
            raise ValueError("datei.download_url ist erforderlich.")
        dateiname = dateiname or datei.get("file_name")
        data = await asyncio.to_thread(lade_datei_von_url, datei["download_url"], effektives_limit(context.db))
        # Plan und Zuordnung nach dem externen Download erneut pruefen.
        context.db.refresh(plan)
        plan = _plan(context, plan_id)
        if element_id is not None:
            if element is not None:
                context.db.refresh(element)
            element = _element(context, plan, element_id)
    else:
        assert inhalt_base64 is not None
        data = _decode_inhalt(inhalt_base64)
    if not dateiname:
        raise ValueError("dateiname ist erforderlich.")
    anhang = anhang_hochladen(context.db, plan, context.user, data, dateiname, None,
                              element=element, beschriftung=beschriftung, urheber=urheber, quelle="mcp")
    if upload is not None:
        upload.uebergeben_am = _utcnow()
    context.db.flush()
    if pfad is not None:
        # Erst nach erfolgreicher Datenbankuebergabe aufraeumen.
        context.db.commit()
        pfad.unlink(missing_ok=True)
    return {"anhang_id": anhang.id, "uuid": anhang.uuid, "mime": anhang.mime, "hinweis": _HINWEIS}


@_tool("katplan_vorschlaege_aus_dokument", (
    "Stellt bis 30 strukturierte Seitenvorschläge in die offene Review-Queue; es entstehen keine Elemente. "
    "Je Eintrag: typ, titel, beschreibung (Liste von Absätzen, Aufzählungspunkte mit •), kapitel, phase, "
    "prioritaet (1-3), strasse/hausnummer/plz/ort oder adresse, kontakte [{name, organisation, telefon, rolle}], "
    "objekt, begruendung und optional daten mit typspezifischen Feldern (z. B. kontrollstelle: gefahren [Text], "
    "kontrollhinweise, zugaenglichkeit; ressource: bedarf [{bezeichnung, menge, einheit}], lagerort; "
    "massnahme: schritte [{text}], erwartetes_ergebnis). Telefonnummern und Mengen nur wörtlich aus der Quelle."
))
async def katplan_vorschlaege_aus_dokument(context: MCPContext, plan_id: int, dokument_id: int,
                                          seiten_nr: int, elemente: list[dict]) -> dict[str, object]:
    plan = _plan(context, plan_id)
    validiere_element_quelle(context.db, plan, dokument_id, str(seiten_nr))
    seite = context.db.query(KatplanDokumentSeite).filter_by(
        org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument_id, seiten_nr=seiten_nr,
    ).one_or_none()
    if seite is None:
        raise ValueError("Quellseite nicht gefunden.")
    if not isinstance(elemente, list) or len(elemente) > 30 or any(not isinstance(e, dict) for e in elemente):
        raise ValueError("Maximal 30 strukturierte Vorschläge je Aufruf.")
    # K9 nutzt flache Adressfelder; MCP darf sie auch als Adresse gruppieren.
    eintraege = []
    for eintrag in elemente:
        adresse = eintrag.get("adresse")
        if isinstance(adresse, dict):
            eintrag = {**{k: adresse.get(k) for k in ("strasse", "hausnummer", "plz", "ort")}, **eintrag}
        eintraege.append(eintrag)
    from app.services.katplan_abgleich_service import titel_aehnlichkeit
    from app.services.katplan_zugriff import sieht_vertraulich

    werte = _parse_antwort(json.dumps({"elemente": eintraege}))
    bestand = context.db.query(KatplanElement).filter_by(org_id=plan.org_id, plan_id=plan.id)
    if not sieht_vertraulich(context.user):
        bestand = bestand.filter_by(vertraulich=False)
    vorhandene = bestand.order_by(KatplanElement.sort, KatplanElement.id).all()
    warnungen = [
        f"Vorschlag {n} '{wert['titel']}' ähnelt {e.kennung or e.uuid} '{e.titel}'"
        for n, wert in enumerate(werte, 1) for e in vorhandene
        if e.typ == wert["typ"] and titel_aehnlichkeit(e.titel, wert["titel"]) >= 0.85
    ]
    for position, wert in enumerate(werte):
        vorschlag = KatplanImportVorschlag(org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument_id,
                                          seite_id=seite.id, seiten_nr=seiten_nr, reihenfolge=position,
                                          status="offen", **wert)
        _abgleichen(context.db, vorschlag)
        context.db.add(vorschlag)
    service.protokolliere(context.db, plan, "plan", "import_vorschlaege", context.user,
                          neu=f"{len(werte)} Vorschläge, Quelle {dokument_id}, Seite {seiten_nr}", quelle="mcp")
    return {"anzahl": len(werte), "warnungen": warnungen,
            "ui_link": f"/plaene/{plan.id}/import?quelle={dokument_id}&seite={seiten_nr}",
            "hinweis": _HINWEIS}


def _zu_element(context: MCPContext, plan: KatplanPlan, element_id: str) -> KatplanElement:
    # Kennungen sind nur innerhalb des Quellplans eindeutig; UUIDs orgweit.
    ziel = context.db.query(KatplanElement).filter_by(org_id=context.org_id, uuid=element_id).one_or_none()
    return ziel if ziel is not None else _element(context, plan, element_id)


def _link_antwort(context: MCPContext, plan: KatplanPlan, element: KatplanElement | None,
                  *, geaendert: list[str]) -> dict[str, object]:
    if element is not None:
        return _antwort(context, plan, element, geaendert=geaendert, verworfen=[], pruefen=False)
    context.db.flush()
    return {"plan_id": plan.id, "plan_version": plan.version, "geaendert": geaendert, "hinweis": _HINWEIS}


@_tool("katplan_verknuepfung_setzen", (
    "Verknüpft ein Stammdatenziel im Arbeitsstand, ohne element_id auf Planebene. "
    "ziel_typ: kontakt, kontakt_organisation, objekt, fahrzeug, wasserstelle. "
    "rolle: zu_verstaendigen, verantwortlich, zustaendig, ausfuehrend, info, sonstiges. "
    "Kontakte vorher mit kontakt_suchen / kontakt_organisation_suchen finden. Telefonnummern nicht in "
    "die Beschreibung kopieren, sondern Kontakt verknüpfen. Idempotent; pruefen=true rollt zurück."
))
async def katplan_verknuepfung_setzen(context: MCPContext, plan_id: int, ziel_typ: str, ziel_id: int,
                                     rolle: str = "info", element_id: str | None = None,
                                     hinweis: str | None = None, pruefen: bool = False) -> dict[str, object]:
    from app.models.katplan import KatplanLink
    from app.services import katplan_link_service as links

    plan = _plan(context, plan_id)
    element = _element(context, plan, element_id) if element_id is not None else None
    link = context.db.query(KatplanLink).filter_by(
        org_id=context.org_id, plan_id=plan.id, element_id=element.id if element else None,
        ziel_typ=ziel_typ, ziel_id=ziel_id, rolle=rolle).one_or_none()
    vorhanden = link is not None
    geaendert = []
    if link is None:
        link = links.link_anlegen(context.db, plan, context.user, element=element,
                                  ziel_typ=ziel_typ, ziel_id=ziel_id, rolle=rolle, hinweis=hinweis or "", quelle="mcp")
        geaendert = ["verknuepfung"]
    elif hinweis is not None and link.hinweis != hinweis:
        if len(hinweis) > 500:
            raise ValueError("Der Hinweis darf höchstens 500 Zeichen enthalten.")
        service.protokolliere(context.db, plan, "link", "geaendert", context.user, feld="hinweis",
                              alt=link.hinweis, neu=hinweis, element_id=link.element_id, quelle="mcp")
        link.hinweis = hinweis
        links._geaendert(context.db, plan, context.user, element, quelle="mcp")
        geaendert = ["hinweis"]
    antwort = _link_antwort(context, plan, element, geaendert=geaendert)
    antwort.update(verknuepfung=lesedaten.link_ansichten(
        context.db, context.user, [links.link_snapshot(context.db, link)])[0],
        bereits_vorhanden=vorhanden, gespeichert=not pruefen)
    if pruefen:
        context.db.rollback()
    return antwort


@_tool("katplan_verknuepfung_entfernen",
       "Entfernt passende Verknüpfungen im Arbeitsstand; rolle=null entfernt alle Rollen.")
async def katplan_verknuepfung_entfernen(context: MCPContext, plan_id: int, ziel_typ: str, ziel_id: int,
                                        element_id: str | None = None, rolle: str | None = None) -> dict[str, object]:
    from app.models.katplan import KatplanLink
    from app.services.katplan_link_service import link_entfernen

    plan = _plan(context, plan_id)
    element = _element(context, plan, element_id) if element_id is not None else None
    query = context.db.query(KatplanLink).filter_by(
        org_id=context.org_id, plan_id=plan.id, element_id=element.id if element else None,
        ziel_typ=ziel_typ, ziel_id=ziel_id)
    rows = (query.filter_by(rolle=rolle) if rolle is not None else query).all()
    if not rows:
        raise ValueError("Verknuepfung nicht gefunden.")
    for link in rows:
        link_entfernen(context.db, plan, context.user, link, quelle="mcp")
    return {"entfernt": len(rows), "hinweis": _HINWEIS}


@_tool("katplan_beziehung_setzen", (
    "Verknüpft Elemente im Arbeitsstand. Kennungen gelten im selben Plan, UUIDs in derselben Organisation. "
    "art: siehe_auch, folgt_auf, abhaengig_von, eskaliert_zu. Idempotent; pruefen=true rollt zurück."
))
async def katplan_beziehung_setzen(context: MCPContext, plan_id: int, element_id: str, zu_element_id: str,
                                  art: str = "siehe_auch", hinweis: str | None = None,
                                  pruefen: bool = False) -> dict[str, object]:
    from app.models.katplan import KatplanRelation
    from app.services import katplan_link_service as links

    plan = _plan(context, plan_id)
    element = _element(context, plan, element_id)
    ziel = _zu_element(context, plan, zu_element_id)
    relation = context.db.query(KatplanRelation).filter_by(
        org_id=context.org_id, plan_id=plan.id, von_element_id=element.id,
        zu_element_uuid=ziel.uuid, art=art).one_or_none()
    vorhanden = relation is not None
    geaendert = []
    if relation is None:
        relation = links.relation_anlegen(context.db, plan, context.user, element, ziel.uuid,
                                          art, hinweis or "", quelle="mcp")
        geaendert = ["beziehung"]
    elif hinweis is not None and relation.hinweis != hinweis:
        if len(hinweis) > 300:
            raise ValueError("Ungültige Beziehungsart oder zu langer Hinweis.")
        service.protokolliere(context.db, plan, "relation", "geaendert", context.user, feld="hinweis",
                              alt=relation.hinweis, neu=hinweis, element_id=element.id, quelle="mcp")
        relation.hinweis = hinweis
        links._geaendert(context.db, plan, context.user, element, quelle="mcp")
        geaendert = ["hinweis"]
    antwort = _link_antwort(context, plan, element, geaendert=geaendert)
    antwort.update(beziehung={"zu_element_id": ziel.uuid, "art": art, "hinweis": relation.hinweis},
                   bereits_vorhanden=vorhanden, gespeichert=not pruefen)
    if pruefen:
        context.db.rollback()
    return antwort


@_tool("katplan_beziehung_entfernen", "Entfernt Beziehungen im Arbeitsstand; art=null entfernt alle Arten zum Ziel.")
async def katplan_beziehung_entfernen(context: MCPContext, plan_id: int, element_id: str,
                                     zu_element_id: str, art: str | None = None) -> dict[str, object]:
    from app.models.katplan import KatplanRelation
    from app.services.katplan_link_service import relation_entfernen

    plan = _plan(context, plan_id)
    element = _element(context, plan, element_id)
    ziel = _zu_element(context, plan, zu_element_id)
    query = context.db.query(KatplanRelation).filter_by(
        org_id=context.org_id, plan_id=plan.id, von_element_id=element.id, zu_element_uuid=ziel.uuid)
    rows = (query.filter_by(art=art) if art is not None else query).all()
    if not rows:
        raise ValueError("Beziehung nicht gefunden.")
    for relation in rows:
        relation_entfernen(context.db, plan, context.user, relation, quelle="mcp")
    return {"entfernt": len(rows), "hinweis": _HINWEIS}


@_tool("katplan_stammdaten_aendern", (
    "Ändert nur übergebene Plan-Stammdaten im Arbeitsstand. version aus katplan_lesen(fassung=arbeitsstand), "
    "Feld plan_version. felder: titel, planart (alarm_einsatzplan, massnahmenplan, katastrophenplan), "
    "beschreibung (Text), gefahrenarten (Liste gültiger Codes), "
    "geltungsbereich, verantwortliche_stelle, verantwortlich_kontakt_id, wiedervorlage_am (YYYY-MM-DD), "
    "pruefintervall_monate (1..60), pruef_verantwortlich_user_id. "
    "null oder leer leert optionale Felder; titel und planart sind Pflicht, gefahrenarten nur bei katastrophenplan. "
    "Status bleibt menschlich. "
    "Unbekannte Felder sind Fehler; streng=true erlaubt keinen Datenverlust; pruefen=true rollt zurück."
))
async def katplan_stammdaten_aendern(context: MCPContext, plan_id: int, version: int, felder: dict,
                                    streng: bool = False, pruefen: bool = False) -> dict[str, object]:
    plan = _plan(context, plan_id)
    if version != plan.version:
        raise ValueError(f"Versionskonflikt: Plan hat version {plan.version}, übergeben {version}. "
                         'Mit katplan_lesen(fassung="arbeitsstand") neu lesen.')
    mapping = {"beschreibung": "beschreibung_html", "gefahrenarten": "gefahrenarten_json"}
    erlaubt = {"planart", "titel", "beschreibung", "gefahrenarten", "geltungsbereich", "verantwortliche_stelle",
               "verantwortlich_kontakt_id", "wiedervorlage_am", "pruefintervall_monate", "pruef_verantwortlich_user_id"}
    if not isinstance(felder, dict):
        raise ValueError("felder muss ein Objekt sein.")
    if set(felder) - erlaubt:
        raise ValueError("Nicht erlaubte Felder: " + ", ".join(sorted(set(felder) - erlaubt)))
    bestand: dict = {"planart": plan.planart, "titel": plan.titel, "gefahrenarten": plan.gefahrenarten,
               "beschreibung_html": plan.beschreibung_html or "", "geltungsbereich": plan.geltungsbereich or "",
               "verantwortliche_stelle": plan.verantwortliche_stelle or "",
               "wiedervorlage_am": plan.wiedervorlage_am.isoformat() if plan.wiedervorlage_am else ""}
    for feld, wert in felder.items():
        if feld in ("pruefintervall_monate", "pruef_verantwortlich_user_id"):
            if wert not in (None, "") and type(wert) is not int:
                raise ValueError(f"{feld} muss eine Ganzzahl oder null sein.")
            bestand[feld] = wert
            continue
        if feld == "verantwortlich_kontakt_id":
            if wert not in (None, "") and type(wert) is not int:
                raise ValueError("verantwortlich_kontakt_id muss eine Kontakt-ID oder null sein.")
            continue
        if feld == "gefahrenarten":
            if not isinstance(wert, list) or any(not isinstance(code, str) for code in wert):
                raise ValueError("Bitte mindestens eine gültige Gefahrenart auswählen.")
        else:
            if wert is not None and not isinstance(wert, str):
                raise ValueError(f"{feld} muss Text sein.")
            wert = wert or ""
        bestand["beschreibung_html" if feld == "beschreibung" else feld] = (
            text_zu_html(wert) if feld == "beschreibung" else wert)
    validiert = service.validiere_stammdaten(**bestand)
    werte = {mapping.get(k, k): validiert[mapping.get(k, k)] for k in felder if k != "verantwortlich_kontakt_id"}
    if "verantwortlich_kontakt_id" in felder:
        werte["verantwortlich_kontakt_id"] = felder["verantwortlich_kontakt_id"] or None
    geaendert = service.stammdaten_aendern(context.db, plan, context.user, version=version, werte=werte, quelle="mcp")
    context.db.flush()
    kopf, _, _, _ = lesedaten.fassung_laden(context.db, context.user, plan, "arbeitsstand")
    geaendert = [k for k in felder if mapping.get(k, k) in geaendert]
    antwort = {"plan_id": plan.id, "plan_version": plan.version, "geaendert": geaendert,
               "unveraendert": [k for k in felder if k not in geaendert], "verworfen": [],
               "gespeichert": not pruefen, "plan": lesedaten.kopf_ansicht(kopf), "hinweis": _HINWEIS}
    if pruefen:
        context.db.rollback()
    return antwort


@_tool("katplan_quellseite_verknuepfen", (
    "Verknüpft bis 50 Elemente (UUID/Kennung) mit dokument_id und seiten wie 3-4,7. "
    "Optionale version prüft Konflikte. Fehler je Eintrag; streng=true bricht alles ab. pruefen=true rollt zurück."
))
async def katplan_quellseite_verknuepfen(context: MCPContext, plan_id: int, zuordnungen: list[dict],
                                        pruefen: bool = False, streng: bool = False) -> dict[str, object]:
    from app.services.katplan_quelle_service import sichtbare_quellen

    plan = _plan(context, plan_id)
    if not isinstance(zuordnungen, list) or len(zuordnungen) > 50:
        raise ValueError("Maximal 50 Zuordnungen je Aufruf.")
    sichtbar = {d.id for d in sichtbare_quellen(context.db, plan, context.user)}
    ergebnisse = []
    for eintrag in zuordnungen:
        element = None
        try:
            if not isinstance(eintrag, dict) or not isinstance(eintrag.get("element_id"), str):
                raise ValueError("Zuordnung benötigt element_id, dokument_id und seiten.")
            element = _element(context, plan, eintrag["element_id"])
            if "version" in eintrag:
                _pruefe_element_version(element, eintrag["version"])
            qid, seiten = eintrag.get("dokument_id"), eintrag.get("seiten")
            if type(qid) is not int or qid not in sichtbar:
                raise ValueError("Quelle nicht gefunden.")
            if not isinstance(seiten, str):
                raise ValueError("Seiten müssen als Text angegeben werden.")
            validiere_element_quelle(context.db, plan, qid, seiten)
            werte = {"quelle_dokument_id": qid, "quelle_seiten": seiten or None}
            geaendert = any(getattr(element, k) != v for k, v in werte.items())
            if geaendert:
                _schreibe_element_werte(context, plan, element, werte)
                _element_geaendert(context, plan, element)
            ergebnisse.append({"element_id": element.uuid, "kennung": element.kennung,
                                "status": "geaendert" if geaendert else "unveraendert", "version": element.version})
        except ValueError as exc:
            if streng:
                raise
            ergebnisse.append({"element_id": eintrag.get("element_id") if isinstance(eintrag, dict) else None,
                                "kennung": element.kennung if element else None, "status": "fehler",
                                "fehler": str(exc), "version": element.version if element else None})
    context.db.flush()
    antwort = {"ergebnisse": ergebnisse, "gespeichert": not pruefen, "hinweis": _HINWEIS}
    if pruefen:
        context.db.rollback()
    return antwort


@_tool("katplan_anhang_aus_quellseite", (
    "Übernimmt eine sichtbare Quellseite als Anhang im Arbeitsstand: modus seite, bild (bild_index) "
    "oder ausschnitt (rahmen [x0,y0,x1,y1], relativ 0..1, mindestens 5 % je Achse). "
    "Ohne element_id auf Planebene. pruefen=true entfernt auch erzeugte Dateien."
))
async def katplan_anhang_aus_quellseite(
    context: MCPContext, plan_id: int, dokument_id: int, seiten_nr: int, element_id: str | None = None,
    modus: str = "seite", bild_index: int | None = None, rahmen: list[float] | None = None,
    beschriftung: str | None = None, vertraulich: bool = False, pruefen: bool = False,
) -> dict[str, object]:
    from app.mcp.tools.katplan import _quellseite
    from app.services import katplan_quellbild_service as bilder
    from app.services.katplan_quelle_service import absolute_pfad

    plan = _plan(context, plan_id)
    _, dokument, seite = _quellseite(context, plan_id, dokument_id, seiten_nr)
    element = _element(context, plan, element_id) if element_id is not None else None
    if modus == "seite":
        data = bilder.ausschnitt(seite, [0, 0, 1, 1])
    elif modus == "bild":
        if bild_index is None:
            raise ValueError("bild_index ist erforderlich.")
        data, _ = bilder.bild_bytes(seite, bild_index)
    elif modus == "ausschnitt":
        if rahmen is None:
            raise ValueError("rahmen ist erforderlich.")
        data = bilder.ausschnitt(seite, rahmen)
    else:
        raise ValueError("modus muss seite, bild oder ausschnitt sein.")
    beschriftung = (f"Quelle: {dokument.titel} v{dokument.versionsnummer}, S. {seiten_nr}"
                    if beschriftung is None else beschriftung)
    anhang = anhang_hochladen(context.db, plan, context.user, data, "quellseite.jpg", "image/jpeg",
                              element=element, beschriftung=beschriftung, vertraulich=vertraulich, quelle="mcp")
    # Pfade vor Rollback sichern; keine Quota-Freigabe nach Rollback (bereits zurueckgesetzt).
    pfade = [absolute_pfad(p) for p in (anhang.pfad, anhang.thumb_pfad) if p]
    context.db.info["katplan_mcp_dateien"].extend(pfade)
    context.db.flush()
    antwort: dict[str, object] = {"anhang_id": anhang.id, "uuid": anhang.uuid, "mime": anhang.mime,
                                  "beschriftung": anhang.beschriftung, "gespeichert": not pruefen,
                                  "geaendert": ["anhang"], "hinweis": _HINWEIS}
    if element is not None:
        antwort.update(_antwort(context, plan, element, geaendert=["anhang"], verworfen=[], pruefen=False))
        antwort["gespeichert"] = not pruefen
    if pruefen:
        context.db.rollback()
    return antwort


@_tool("katplan_elemente_verorten", (
    "Verortet bis zu 30 Elemente im Arbeitsstand. Ohne auswahl nur Ortskandidaten, gespeichert=false. "
    "element_ids: UUID oder Kennung; sonst Elemente des Plans, nur_ohne_ort filtert vorhandene Geometrien. "
    "auswahl={element_id: kandidat_id} berechnet Kandidaten neu und setzt gewählte Punkte ungeprüft; "
    "für bestehende geprüfte Orte ueberschreiben_geprueft=true und nur_ohne_ort=false verwenden. "
    "weitere zählt nicht bearbeitete Elemente. Nominatim-Suche wird gedrosselt und kann dauern. "
    "pruefen=true führt einen Trockenlauf mit Rollback aus. Lage vor Ort prüfen und im Editor als geprüft markieren."
))
async def katplan_elemente_verorten(
    context: MCPContext, plan_id: int, element_ids: list[str] | None = None, nur_ohne_ort: bool = True,
    auswahl: dict[str, str] | None = None, pruefen: bool = False, ueberschreiben_geprueft: bool = False,
) -> dict[str, object]:
    from app.services.katplan_typen import ELEMENT_TYPEN
    from app.services.katplan_verortung_service import kandidaten

    plan = _plan(context, plan_id)
    if element_ids is not None and (not isinstance(element_ids, list) or len(element_ids) > 30
                                    or any(not isinstance(e, str) for e in element_ids)):
        raise ValueError("element_ids muss eine Liste mit maximal 30 UUIDs/Kennungen sein.")
    if auswahl is not None and (not isinstance(auswahl, dict) or len(auswahl) > 30
                               or any(not isinstance(k, str) or not isinstance(v, str) for k, v in auswahl.items())):
        raise ValueError("auswahl muss maximal 30 element_id: kandidat_id enthalten.")
    # Eine Auswahl muss auch ohne wiederholte element_ids gezielt aufloesbar sein.
    ids = element_ids if element_ids is not None else list(auswahl) if auswahl is not None else None
    if ids is not None:
        elemente = list({e.id: e for e in (_element(context, plan, eid) for eid in ids)}.values())
        if auswahl is not None:
            erlaubte = {wert for e in elemente for wert in (e.uuid, e.kennung)}
            if set(auswahl) - erlaubte:
                raise ValueError("auswahl enthält Elemente außerhalb von element_ids.")
        elemente = [e for e in elemente if not nur_ohne_ort or not e.geometry_json]
        weitere = 0
    else:
        query = context.db.query(KatplanElement).filter_by(org_id=context.org_id, plan_id=plan.id)
        if nur_ohne_ort:
            query = query.filter((KatplanElement.geometry_json.is_(None)) | (KatplanElement.geometry_json == ""))
        weitere = max(0, query.count() - 30)
        elemente = query.order_by(KatplanElement.sort, KatplanElement.id).limit(30).all()
    org = context.user.org
    if org is None:
        raise ValueError("Organisation nicht gefunden.")
    ergebnisse = []
    gesetzt = False
    for element in elemente:
        version = element.version
        vorschlaege = await kandidaten(context.db, org, element)
        eintrag: dict = {
            "element_id": element.uuid, "kennung": element.kennung, "titel": element.titel,
            "status": "kandidaten" if vorschlaege else "keine_kandidaten",
            "kandidaten": vorschlaege, "version": version,
        }
        if ELEMENT_TYPEN[element.typ].geometrie not in ("punkt", "beliebig"):
            eintrag["hinweis"] = "Dieser Typ unterstützt keine Punktgeometrie."
        kandidat_id = auswahl.get(element.uuid, auswahl.get(element.kennung or "")) if auswahl is not None else None
        if kandidat_id is not None:
            treffer = next((k for k in vorschlaege if k["kandidat_id"] == kandidat_id), None)
            if treffer is None:
                eintrag.update(status="fehler", hinweis="Unbekannte kandidat_id; Kandidaten erneut lesen.")
            elif element.geo_status == "geprueft" and not ueberschreiben_geprueft:
                eintrag.update(status="fehler", hinweis="Geprüfter Ort: ueberschreiben_geprueft=true erforderlich.")
            else:
                _pruefe_element_version(element, version)
                geom = {"type": "Point", "coordinates": [treffer["lng"], treffer["lat"]]}
                _schreibe_element_werte(context, plan, element, {
                    "geometry_json": json.dumps(geom), "lat": treffer["lat"], "lng": treffer["lng"],
                    "geo_status": "ungeprueft",
                })
                _element_geaendert(context, plan, element)
                eintrag.update(status="gesetzt", gesetzt=geom, version=element.version)
                gesetzt = True
        ergebnisse.append(eintrag)
    context.db.flush()
    antwort = {"ergebnisse": ergebnisse, "weitere": weitere, "gespeichert": gesetzt and not pruefen,
               "hinweis": "Lage vor Ort prüfen und im Editor als geprüft markieren. " + _HINWEIS}
    if pruefen:
        context.db.rollback()
    return antwort


@_tool("katplan_ueberpruefung_bestaetigen", (
    "Bestätigt die Überprüfung eines freigegebenen Plans ohne neue Version. "
    "Nur verantwortliche Person oder Freigeber; Kommentar optional (maximal 2000 Zeichen). "
    "version dient dem Schutz vor gleichzeitigen Änderungen."
), lesen=True)
async def katplan_ueberpruefung_bestaetigen(context: MCPContext, plan_id: int,
                                           kommentar: str | None = None,
                                           version: int | None = None) -> dict[str, object]:
    from app.services.katplan_freigabe_service import ueberpruefung_bestaetigen

    plan = _plan(context, plan_id)
    if not darf_ueberpruefung_bestaetigen(context.user, plan):
        raise MCPPermissionError("Keine Berechtigung")
    if version is not None:
        service.pruefe_version(plan, version)
    ueberpruefung_bestaetigen(context.db, plan, context.user, kommentar)
    context.db.flush()
    return {"plan_id": plan.id, "plan_version": plan.version, "status": plan.status,
            **lesedaten.pruefdaten(context.db, plan)}
