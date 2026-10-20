"""MCP-Entwuerfe im Arbeitsstand; die Freigabe bleibt beim Menschen."""

import asyncio
import json
from datetime import date
from functools import wraps
from pathlib import Path

from sqlalchemy import func

from app.config import settings
from app.core.audit import write_audit
from app.core.permissions import KATPLAN_VERWALTER_ROLES
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
from app.services import katplan_service as service
from app.services.katplan_anhang_service import anhang_hochladen
from app.services.katplan_flags import katplan_effective_enabled
from app.services.katplan_ki_service import _abgleichen, _parse_antwort, text_zu_html
from app.services.katplan_quelle_service import validiere_element_quelle
from app.services.katplan_zugriff import darf_bearbeiten
from app.services.mcp_datei_url_service import lade_datei_von_url
from app.services.mcp_upload_service import effektives_limit, erstelle_upload, lade_upload_fuer_uebergabe

_FELDER = {
    "titel", "beschreibung", "prioritaet", "daten", "tags", "adresse", "geometrie", "kapitel_id",
    "phasen", "quelle_dokument_id", "quelle_seiten", "gueltig_bis", "pruefen_am",
}
_HINWEIS = "Nur Arbeitsstand: Ein Bearbeiter muss den Entwurf prüfen und ein Mensch muss ihn freigeben."


def _tool(name: str, description: str):
    def decorator(handler):
        @wraps(handler)
        async def aufruf(context: MCPContext, **kwargs) -> dict[str, object]:
            if not darf_bearbeiten(context.user):
                raise MCPPermissionError("Keine Berechtigung")
            if context.org_id != context.user.org_id or not katplan_effective_enabled(context.org_id, context.db):
                raise MCPPermissionError("Werkzeug nicht verfügbar")
            try:
                result = await handler(context, **kwargs)
                write_audit(context.db, "mcp." + name, org_id=context.org_id, user_id=context.user.id,
                            entity_type="katplan", entity_id=kwargs.get("plan_id"))
                context.db.commit()
                return result
            except service.KatplanKonflikt as exc:
                context.db.rollback()
                raise ValueError("Versionskonflikt: " + str(exc)) from exc
            except Exception:
                context.db.rollback()
                raise
        return register_tool(name=name, description=description, required_roles=tuple(KATPLAN_VERWALTER_ROLES),
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
        phase = context.db.query(KatplanPhase).filter_by(org_id=plan.org_id, plan_id=plan.id).filter(
            func.lower(KatplanPhase.name) == name.lower(),
        ).first()
        if phase is None:
            phase = service.lege_phase_an(context.db, plan, context.user, name=name, quelle="mcp")
        ids.add(phase.id)
    return ids


def _werte(typ: str, *, titel: str, beschreibung: str, prioritaet: int, daten: dict,
           tags: list[str], adresse: dict, geometrie: dict | None) -> dict:
    if not isinstance(titel, str) or not isinstance(beschreibung, str):
        raise ValueError("Titel und Beschreibung müssen Text sein.")
    if type(prioritaet) is not int:
        raise ValueError("Priorität muss 1, 2 oder 3 sein.")
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise ValueError("Tags müssen eine Textliste sein.")
    if not isinstance(adresse, dict) or any(not isinstance(v, str) for v in adresse.values()):
        raise ValueError("Adresse muss ein Objekt mit Textwerten sein.")
    if geometrie is not None and not isinstance(geometrie, dict):
        raise ValueError("Geometrie muss ein GeoJSON-Objekt sein.")
    return service.validiere_element_felder(
        typ, titel=titel, prioritaet=prioritaet, daten=daten,
        tags=[t.strip() for t in tags if t.strip()], adresse=adresse,
        beschreibung_html=text_zu_html(beschreibung),
        geometrie=json.dumps(geometrie) if geometrie is not None else None,
    )


def _antwort(element: KatplanElement) -> dict[str, object]:
    return {"element_id": element.uuid, "id": element.id, "kennung": element.kennung,
            "version": element.version, "hinweis": _HINWEIS}


@_tool("katplan_element_entwurf_anlegen", "Legt einen KI-Entwurf nur im Arbeitsstand an; menschliche Freigabe nötig.")
async def katplan_element_entwurf_anlegen(
    context: MCPContext, plan_id: int, typ: str, titel: str, beschreibung: str = "",
    kapitel_id: int | None = None, kapitel_titel: str | None = None, phasen: list[str] | None = None,
    prioritaet: int = 2, daten: dict | None = None, tags: list[str] | None = None,
    adresse: dict | None = None, geometrie: dict | None = None,
    quelle_dokument_id: int | None = None, quelle_seiten: str | None = None,
) -> dict[str, object]:
    plan = _plan(context, plan_id)
    werte = _werte(typ, titel=titel, beschreibung=beschreibung, prioritaet=prioritaet,
                   daten=daten if daten is not None else {}, tags=tags if tags is not None else [],
                   adresse=adresse if adresse is not None else {}, geometrie=geometrie)
    validiere_element_quelle(context.db, plan, quelle_dokument_id, quelle_seiten or "")
    if kapitel_id is not None and kapitel_titel is not None:
        raise ValueError("Entweder kapitel_id oder kapitel_titel angeben.")
    _kapitel(context, plan, kapitel_id)
    if kapitel_titel is not None:
        titel_kapitel = kapitel_titel.strip()
        kapitel = context.db.query(KatplanAbschnitt).filter_by(org_id=plan.org_id, plan_id=plan.id).filter(
            func.lower(KatplanAbschnitt.titel) == titel_kapitel.lower(),
        ).first()
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
    return _antwort(element)


@_tool("katplan_element_entwurf_aendern", "Ändert erlaubte Entwurfsfelder mit Versionsprüfung nur im Arbeitsstand.")
async def katplan_element_entwurf_aendern(context: MCPContext, plan_id: int, element_id: str,
                                         version: int, felder: dict) -> dict[str, object]:
    plan = _plan(context, plan_id)
    element = _element(context, plan, element_id)
    service.pruefe_version(element, version)
    if not isinstance(felder, dict):
        raise ValueError("felder muss ein Objekt sein.")
    verboten = set(felder) - _FELDER
    if verboten:
        raise ValueError("Nicht erlaubte Felder: " + ", ".join(sorted(verboten)))
    # Nur uebergebene Felder validieren und schreiben; gepruefte Bestandsgeometrie bleibt erhalten.
    scalar = {"titel", "beschreibung", "prioritaet", "daten", "tags", "adresse", "geometrie"}
    werte = {}
    if scalar & set(felder):
        defaults = {"titel": element.titel, "beschreibung": "", "prioritaet": element.prioritaet,
                    "daten": json.loads(element.daten_json or "{}"), "tags": json.loads(element.tags_json or "[]"),
                    "adresse": {k: getattr(element, k) or "" for k in ("strasse", "hausnummer", "plz", "ort")},
                    "geometrie": None}
        defaults.update({k: v for k, v in felder.items() if k in scalar})
        alle = _werte(element.typ, **defaults)
        mapping = {"beschreibung": ("beschreibung_html",), "daten": ("daten_json",), "tags": ("tags_json",),
                   "adresse": ("strasse", "hausnummer", "plz", "ort"),
                   "geometrie": ("geometry_json", "lat", "lng", "geo_status")}
        for key in scalar & set(felder):
            for feld in mapping.get(key, (key,)):
                werte[feld] = alle[feld]
    if "kapitel_id" in felder:
        werte["abschnitt_id"] = _kapitel(context, plan, felder["kapitel_id"])
    for feld in ("gueltig_bis", "pruefen_am"):
        if feld in felder:
            if felder[feld] is not None and not isinstance(felder[feld], str):
                raise ValueError(f"{feld}: Datum als YYYY-MM-DD angeben.")
            werte[feld] = date.fromisoformat(felder[feld]) if felder[feld] else None
    if {"quelle_dokument_id", "quelle_seiten"} & set(felder):
        qid = felder.get("quelle_dokument_id", element.quelle_dokument_id)
        seiten = felder.get("quelle_seiten", element.quelle_seiten) or ""
        validiere_element_quelle(context.db, plan, qid, seiten)
        werte.update(quelle_dokument_id=qid, quelle_seiten=seiten or None)
    phase_ids = _phasen(context, plan, felder["phasen"]) if "phasen" in felder else None
    for feld, wert in werte.items():
        alt = getattr(element, feld)
        if alt != wert:
            setattr(element, feld, wert)
            service.protokolliere(context.db, plan, "element", "geaendert", context.user,
                                  feld={"daten_json": "daten", "geometry_json": "geometrie"}.get(feld, feld),
                                  alt=str(alt) if alt is not None else None,
                                  neu=str(wert) if wert is not None else None, element_id=element.id, quelle="mcp")
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
    element.version += 1
    element.aktualisiert_am, element.aktualisiert_von_id = _utcnow(), context.user.id
    service.aktualisiere_element_suchtext(element)
    service.markiere_ueberarbeitung(context.db, plan, context.user, quelle="mcp")
    return _antwort(element)


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
    werte = _parse_antwort(json.dumps({"elemente": eintraege}))
    for position, wert in enumerate(werte):
        vorschlag = KatplanImportVorschlag(org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument_id,
                                          seite_id=seite.id, seiten_nr=seiten_nr, reihenfolge=position,
                                          status="offen", **wert)
        _abgleichen(context.db, vorschlag)
        context.db.add(vorschlag)
    service.protokolliere(context.db, plan, "plan", "import_vorschlaege", context.user,
                          neu=f"{len(werte)} Vorschläge, Quelle {dokument_id}, Seite {seiten_nr}", quelle="mcp")
    return {"anzahl": len(werte),
            "ui_link": f"/katastrophenplaene/{plan.id}/import?quelle={dokument_id}&seite={seiten_nr}",
            "hinweis": _HINWEIS}
