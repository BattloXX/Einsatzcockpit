"""Wissenspflege und Einsatzdokumente über dieselben Fachservices wie die Weboberfläche."""

import json
from datetime import date
from functools import wraps

from fastapi import HTTPException

from app.mcp.context import MCPContext, MCPPermissionError
from app.mcp.tools.katplan import _fassung
from app.mcp.tools.katplan import _tool as lesetool
from app.mcp.tools.katplan_schreiben import _element, _plan
from app.mcp.tools.katplan_schreiben import _tool as schreibtool
from app.models.einsatz_dokument import EinsatzDokument
from app.models.katplan import KatplanAnhang
from app.services import einsatz_dokument_service as einsatz
from app.services import katplan_anhang_service as anhaenge
from app.services.katplan_zugriff import darf_bearbeiten, sieht_vertraulich


def fachfehler(handler):
    @wraps(handler)
    async def aufruf(*args, **kwargs):
        try:
            return await handler(*args, **kwargs)
        except HTTPException as exc:
            if exc.status_code == 403:
                raise MCPPermissionError(str(exc.detail)) from exc
            raise ValueError(str(exc.detail)) from exc
    return aufruf


@lesetool("einsatzwissen_kategorien", "Listet die sechs fachlichen Einsatzwissen-Kategorien mit Codes.")
async def einsatzwissen_kategorien(context: MCPContext) -> dict:
    from app.services.einsatzwissen_kategorien import KATEGORIEN

    return {"kategorien": [{"code": code, "titel": titel} for code, titel in KATEGORIEN.items()]}


@lesetool("katplan_dokumentbibliothek", "Listet mehrfach zuordenbare Dateien und PDF-Quellen; nur für Bearbeiter.")
async def katplan_dokumentbibliothek(context: MCPContext) -> dict:
    if not darf_bearbeiten(context.user):
        raise MCPPermissionError("Keine Berechtigung für den Arbeitsstand.")
    return {"dokumente": anhaenge.dokumentbibliothek(context.db, context.user)}


@schreibtool("katplan_dokument_referenzieren", (
    "Ordnet eine Referenz aus katplan_dokumentbibliothek ohne Dateikopie einem Plan oder einer Maßnahme zu. "
    "version ist die aktuelle plan_version des Arbeitsstands. seiten optional, z. B. 3-5,8. Keine Freigabe."
))
@fachfehler
async def katplan_dokument_referenzieren(context: MCPContext, plan_id: int, version: int, referenz: str,
                                         element_id: str | None = None, seiten: str = "",
                                         beschriftung: str = "") -> dict:
    plan = _plan(context, plan_id)
    anhaenge.pflege_version(context.db, plan, context.user, version)
    element = _element(context, plan, element_id) if element_id is not None else None
    neu = anhaenge.dokument_referenzieren(context.db, plan, context.user, referenz, element=element,
        seiten=seiten, beschriftung=beschriftung, quelle="mcp")
    return {"anhang_uuid": neu.uuid, "plan_version": plan.version, "status": plan.status,
            "hinweis": "Nur Arbeitsstand. Menschliche Prüfung und Freigabe erforderlich."}


@schreibtool("katplan_dokument_pflegen", (
    "Ändert Dokumentmetadaten im Arbeitsstand mit plan_version als version. felder: beschriftung, sort, "
    "pruefdatum, gueltig_bis (YYYY-MM-DD/null), pruefintervall_monate (1..60/null), zustaendig_user_id (ID/null)."
))
@fachfehler
async def katplan_dokument_pflegen(context: MCPContext, plan_id: int, version: int,
                                   anhang_uuid: str, felder: dict) -> dict:
    plan = _plan(context, plan_id)
    anhaenge.pflege_version(context.db, plan, context.user, version)
    a = context.db.query(KatplanAnhang).filter_by(org_id=context.org_id, plan_id=plan.id,
                                                uuid=anhang_uuid, geloescht_am=None).one_or_none()
    if a is None:
        raise ValueError("Dokument nicht gefunden.")
    if not isinstance(felder, dict) or set(felder) - {"beschriftung", "sort", *anhaenge.PFLEGE_FELDER}:
        raise ValueError("Unbekannte Dokumentmetadaten.")
    if "beschriftung" in felder and not isinstance(felder["beschriftung"], str):
        raise ValueError("Beschriftung muss Text sein.")
    if "sort" in felder and type(felder["sort"]) is not int:
        raise ValueError("Reihenfolge muss eine Ganzzahl sein.")
    pflege = {k: v for k, v in felder.items() if k in anhaenge.PFLEGE_FELDER}
    for k in ("pruefdatum", "gueltig_bis"):
        if pflege.get(k) is not None:
            if not isinstance(pflege[k], str):
                raise ValueError("Datum muss YYYY-MM-DD oder null sein.")
            pflege[k] = date.fromisoformat(pflege[k])
    anhaenge.anhang_aendern(context.db, plan, context.user, a,
        beschriftung=felder.get("beschriftung", a.beschriftung), urheber=a.urheber,
        aufnahme_datum=a.aufnahme_datum, vertraulich=a.vertraulich, sort=felder.get("sort", a.sort),
        pflege=pflege, quelle="mcp")
    return {"anhang": anhaenge.anhang_snapshot(a), "plan_version": plan.version, "status": plan.status}


@lesetool("katplan_pruefstatus", (
    "Liest Planprüfstatus und sichtbare Dokumentprüfangaben aus der gewählten Fassung. "
    "Arbeitsstand nur für Bearbeiter; bestehende Freigaben bleiben verbindlich."
))
async def katplan_pruefstatus(context: MCPContext, plan_id: int, fassung: str = "freigegeben") -> dict:
    from app.services.katplan_mcp_service import anhang_ansichten, pruefdaten

    plan, kopf, elemente, vid, _ = _fassung(context, plan_id, fassung)
    dokumente = anhang_ansichten(context.user, kopf.get("anhaenge", []), vid)
    for e in elemente:
        dokumente.extend(e.get("attachments", []))
    return {"plan_id": plan.id, "version_id": vid, "fassung": fassung,
            **pruefdaten(context.db, plan), "dokumente": dokumente}


@lesetool("einsatz_dokumente_auflisten", (
    "Listet berechtigte Einsatzdokumente mit Herkunft, Seiten und Revision. art: einsatz oder lage."
))
@fachfehler
async def einsatz_dokumente_auflisten(context: MCPContext, art: str, einsatz_id: int) -> dict:
    from app.services.dokumentdruck_service import _quell_request

    einsatz.vollzugang(_quell_request(context.user, "/"), context.user)
    einsatz.ziel(context.db, context.user, art, einsatz_id)
    query = context.db.query(EinsatzDokument).filter(einsatz.ziel_filter(art, einsatz_id),
        EinsatzDokument.org_id == context.org_id, EinsatzDokument.entfernt_am.is_(None))
    if not sieht_vertraulich(context.user):
        query = query.filter(EinsatzDokument.vertraulich.is_(False))
    return {"dokumente": [{"id": d.id, "titel": d.titel, "seiten": d.seiten, "seitenzahl": d.seitenzahl,
        "revision": d.revision, "quelle": json.loads(d.quelle_json), "url": f"/einsatz-dokumente/datei/{d.id}"}
        for d in query.order_by(EinsatzDokument.id)]}


@lesetool("einsatz_dokument_zuordnen", (
    "Übernimmt freigegebene Dokumente in einen aktiven Einsatz/eine Lage; zusätzliche Einsatz-Schreibrechte nötig. "
    "daten wie Web-Übernahme: documents:[{url,pages}], art:einsatz/lage, einsatz_id, optional auftrag_id, "
    "einheit_ids, auftrag_freigeben und vertraulich_bestaetigt. Keine automatische Veröffentlichung."
))
@fachfehler
async def einsatz_dokument_zuordnen(context: MCPContext, daten: dict) -> dict:
    from app.routers.ui_einsatz_dokumente import Uebernahme
    from app.services.dokumentdruck_service import _quell_request

    einsatz.vollzugang(_quell_request(context.user, "/"), context.user)
    werte = Uebernahme.model_validate(daten)
    try:
        docs = einsatz.uebernehmen(context.db, context.user, werte.model_dump())
        return {"dokument_ids": [d.id for d in docs]}
    except Exception:
        context.db.rollback()
        raise


@lesetool("dokumentdruck_vorbereiten", (
    "Bereitet die geprüfte Dokumentauswahl als PDF vor. Sendet keinen Druckauftrag. "
    "documents:[{url,pages}], format:original/A4/A3, orientation:original/portrait/landscape."
))
@fachfehler
async def dokumentdruck_vorbereiten(context: MCPContext, documents: list[dict],
                                   format: str = "original", orientation: str = "original") -> dict:
    from app.routers.ui_dokumentdruck import Auswahl, vorbereiten
    from app.services.dokumentdruck_service import _quell_request

    werte = Auswahl.model_validate({"documents": documents, "format": format, "orientation": orientation})
    return vorbereiten(werte, _quell_request(context.user, "/"), context.db, context.user)


@lesetool("katplan_dokument_zusammenfassung", (
    "Lokaler, extraktiver Überblick aus bis 20 berechtigten PDF-Quellseiten mit Seitenbelegen. "
    "Kein externes Modell, keine fachliche Empfehlung. Für weitere Seiten von/bis wählen."
))
async def katplan_dokument_zusammenfassung(context: MCPContext, plan_id: int, dokument_id: int,
                                          von: int = 1, bis: int | None = None) -> dict:
    from app.mcp.tools.katplan import katplan_dokument_auszug

    # Unverpackter Handler: dieselbe Quellprüfung ohne vorzeitiges Commit/Audit.
    result = await katplan_dokument_auszug.__wrapped__(context, plan_id=plan_id,
        dokument_id=dokument_id, von=von, bis=bis if bis is not None else von)
    abschnitte = []
    for seite in result.get("seiten", []):
        text = (seite.get("text") or "").strip()
        if text:
            abschnitte.append({"seite": seite["seiten_nr"], "auszug": text[:600],
                               "gekuerzt": len(text) > 600})
    return {"plan_id": plan_id, "dokument_id": dokument_id, "verfahren": "extraktiv_lokal",
            "abschnitte": abschnitte, "warnings": result.get("warnings", []),
            "hinweis": "Quellenauszüge, keine fachlich geprüfte Handlungsanweisung. Original und Kontext prüfen."}
