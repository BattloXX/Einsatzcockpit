"""Lesende MCP-Werkzeuge fuer freigegebene Einsatz- und Katastrophenplaene."""

from functools import wraps

from app.config import settings
from app.core.audit import write_audit
from app.core.permissions import KATPLAN_LESE_ROLLEN
from app.mcp.context import MCPContext, MCPPermissionError
from app.mcp.registry import register_tool
from app.models.katplan import KatplanDokument, KatplanDokumentSeite
from app.services import katplan_mcp_service as daten
from app.services import katplan_suche_service as suche_service
from app.services.katplan_flags import katplan_effective_enabled
from app.services.katplan_freigabe_service import lade_version
from app.services.katplan_quelle_service import quellen_ids_aus_version, sichtbare_quellen
from app.services.katplan_zugriff import darf_lesen, sieht_vertraulich
from app.services.mcp_download_service import erstelle_download_token


def _tool(name: str, description: str):
    def decorator(handler):
        @wraps(handler)
        async def aufruf(context: MCPContext, **kwargs) -> dict[str, object]:
            if not darf_lesen(context.user):
                raise MCPPermissionError("Keine Berechtigung")
            if context.org_id != context.user.org_id or not katplan_effective_enabled(context.org_id, context.db):
                raise MCPPermissionError("Werkzeug nicht verfügbar")
            try:
                result = await handler(context, **kwargs)
            except LookupError as exc:
                raise ValueError("Nicht gefunden") from exc
            except PermissionError as exc:
                raise MCPPermissionError(str(exc)) from exc
            write_audit(context.db, "mcp." + name, org_id=context.org_id, user_id=context.user.id,
                        entity_type="katplan", entity_id=kwargs.get("plan_id"))
            context.db.commit()
            return result
        return register_tool(name=name, description=description, required_roles=KATPLAN_LESE_ROLLEN,
                             module_check=katplan_effective_enabled)(aufruf)
    return decorator


def _fassung(context: MCPContext, plan_id: int, fassung: str, version_id: int | None = None):
    plan = daten.plan_laden(context.db, context.user, plan_id)
    return plan, *daten.fassung_laden(context.db, context.user, plan, fassung, version_id)


def _element(elemente: list[dict], element_id: str) -> dict:
    e = next((e for e in elemente if element_id in (e["element_id"], e["kennung"])), None)
    if e is None:
        raise LookupError("Nicht gefunden")
    return e


@_tool("katplan_liste", (
    "Listet sichtbare Einsatz- und Katastrophenpläne und aktuelle Freigaben mit Planart-Code und Label; "
    "optionaler Filter planart (alarm_einsatzplan, massnahmenplan, katastrophenplan)."
))
async def katplan_liste(context: MCPContext, gefahr: str = "", status: str = "",
                        suchbegriff: str = "", fassung: str = "freigegeben", planart: str = "") -> dict[str, object]:
    arbeit = daten.pruefe_fassung(context.user, fassung)
    result = []
    for p in suche_service.sichtbare_plaene(context.db, context.user, arbeit):
        plan = p["plan"]
        plan_status = plan.status if arbeit else "freigegeben"
        if (gefahr and gefahr not in p["gefahrenarten"] or status and status != plan_status
                or planart and planart != p["planart"]
                or suchbegriff.casefold() not in p["titel"].casefold()):
            continue
        version = None
        termin = plan.wiedervorlage_am.isoformat() if plan.wiedervorlage_am else None
        anzahl = None
        if plan.aktuelle_version_id:
            v, kopf, elemente = lade_version(context.db, plan)
            version = {"id": v.id, "nummer": v.versionsnummer, "freigegeben_am": v.freigegeben_am.isoformat() + "Z"}
            anzahl = sum(not e.vertraulich or sieht_vertraulich(context.user) for e in elemente)
        result.append({"id": plan.id, "titel": p["titel"],
                       **{k: p[k] for k in ("kategorie", "unterkategorie", "kurzbeschreibung", "schlagwoerter")},
                       "planart": p["planart"], "planart_label": p["planart_label"],
                       "gefahrenarten": p["gefahrenarten"],
                       "status": plan_status, "aktuelle_version": version, "wiedervorlage_am": termin,
                       "anzahl_elemente": anzahl, **daten.pruefdaten(context.db, plan),
                       "warnings": (["Aktuelle Version und Elementzahl nicht vorhanden."] if version is None else [])
                       + (["wiedervorlage_am: nicht vorhanden."] if termin is None else [])})
    return {"plaene": result, "warnings": []}


@_tool("katplan_lesen", (
    "Liest Kopf mit Planart-Code und Label, Akteurskatalog, Kapitel, Phasen, Links, Anhänge und Quellen; "
    "optional bis 200 Elemente."
))
async def katplan_lesen(context: MCPContext, plan_id: int, fassung: str = "freigegeben",
                        version_id: int | None = None, mit_elementen: bool = False) -> dict[str, object]:
    plan, kopf, elemente, vid, warnings = _fassung(context, plan_id, fassung, version_id)
    knoten = {a["id"]: {"id": a["id"], "titel": a["titel"], "kinder": [], "elementkennungen": []}
              for a in kopf.get("abschnitte", [])}
    abschnitte = {a["id"]: a for a in kopf.get("abschnitte", [])}
    kapitel = []
    for a in abschnitte.values():
        pfad: list[str] = []
        aid = a["id"]
        gesehen = set()
        while aid in abschnitte and aid not in gesehen:
            gesehen.add(aid)
            pfad.insert(0, abschnitte[aid]["titel"])
            aid = abschnitte[aid]["parent_id"]
        knoten[a["id"]]["elementkennungen"] = [e["kennung"] for e in elemente
                                                if e["chapter"] == " > ".join(pfad)]
        parent = a["parent_id"]
        if parent in knoten and aid not in gesehen:
            knoten[parent]["kinder"].append(knoten[a["id"]])
        else:
            kapitel.append(knoten[a["id"]])
    ohne_kapitel = [e["kennung"] for e in elemente if e["chapter"] is None]
    if ohne_kapitel:
        kapitel.append({"id": None, "titel": None, "kinder": [], "elementkennungen": ohne_kapitel})
    if vid is not None:
        ids = quellen_ids_aus_version(context.db, plan, vid, context.user)
        dokumente = context.db.query(KatplanDokument).filter(
            KatplanDokument.org_id == context.org_id, KatplanDokument.plan_id == plan.id,
            KatplanDokument.id.in_(ids),
        ).order_by(KatplanDokument.id).all()
    else:
        dokumente = sichtbare_quellen(context.db, plan, context.user)
    quellen = [{k: getattr(d, k) for k in ("id", "titel", "versionsnummer", "seitenzahl")} for d in dokumente]
    if mit_elementen and len(elemente) > 200:
        warnings.append("Elemente auf 200 begrenzt; katplan_elemente mit Filtern verwenden.")
    return {"plan_id": plan.id, "plan_version_id": vid, "fassung": fassung,
            "plan_version": plan.version if vid is None else None,
            "kopf": daten.kopf_ansicht(kopf),
            "akteure": [{k: a[k] for k in ("schluessel", "bezeichnung")} for a in kopf.get("akteure", [])],
            "kapitelbaum": kapitel, "phasen": kopf.get("phasen", []),
            "links": daten.link_ansichten(context.db, context.user, kopf.get("links", [])),
            "anhaenge": daten.anhang_ansichten(context.user, kopf.get("anhaenge", []), vid),
            "quellen": quellen, "elemente": elemente[:200] if mit_elementen else [], "warnings": warnings}


@_tool("katplan_suchen", "Sucht Elemente und Quellseiten; maximal 50 Treffer, Arbeitsstand nur für Bearbeiter.")
async def katplan_suchen(context: MCPContext, q: str = "", plan_id: int | None = None,
                         gefahr: str = "", typ: str = "", prioritaet: int | None = None, phase: str = "",
                         ort: str = "", nur_geprueft: bool = False, lat: float | None = None,
                         lng: float | None = None, radius_m: float | None = None,
                         fassung: str = "freigegeben", limit: int = 50) -> dict[str, object]:
    arbeit = daten.pruefe_fassung(context.user, fassung)
    if not 1 <= limit <= 50:
        raise ValueError("limit muss zwischen 1 und 50 liegen.")
    result = suche_service.suche(context.db, context.user, q=q, plan_id=plan_id, gefahr=gefahr, typ=typ,
                                 prioritaet=prioritaet, phase=phase, ort=ort, nur_geprueft=nur_geprueft,
                                 lat=lat, lng=lng, radius_m=radius_m, arbeitsstand=arbeit, limit=limit)
    result["warnings"] = ["Treffer begrenzt."] if result["abgeschnitten"] else []
    return result


@_tool("katplan_elemente", "Liest Elemente in Planreihenfolge, gefiltert nach Typ, Phase, Akteur oder Kapitel. "
       "Zum Schreiben version aus fassung=arbeitsstand verwenden, nicht element_version_id.")
async def katplan_elemente(context: MCPContext, plan_id: int, fassung: str = "freigegeben",
                           typ: str = "", phase: str = "", kapitel: str = "", limit: int = 100,
                           akteur: str = "") -> dict[str, object]:
    if not 1 <= limit <= 200:
        raise ValueError("limit muss zwischen 1 und 200 liegen.")
    _, kopf, elemente, vid, warnings = _fassung(context, plan_id, fassung)
    phase = next((p["name"] for p in kopf.get("phasen", []) if str(p["id"]) == phase), phase)
    kapitel = next((a["titel"] for a in kopf.get("abschnitte", []) if str(a["id"]) == kapitel), kapitel)
    elemente = [e for e in elemente if (not akteur or any(a["schluessel"] == akteur for a in e["akteure"]))
                and (not typ or e["type"] == typ) and (not phase or phase in e["phases"])
                and (not kapitel or kapitel in (e["chapter"] or "").split(" > ") or kapitel == e["chapter"])]
    if len(elemente) > limit:
        warnings.append("Elemente begrenzt.")
    return {"plan_id": plan_id, "plan_version_id": vid, "elemente": elemente[:limit], "warnings": warnings}


@_tool("katplan_element_lesen", "Liest ein Element anhand seiner UUID oder Kennung (z. B. M-01). "
       "Zum Schreiben version aus fassung=arbeitsstand verwenden, nicht element_version_id.")
async def katplan_element_lesen(context: MCPContext, plan_id: int, element_id: str,
                                fassung: str = "freigegeben", version_id: int | None = None) -> dict[str, object]:
    _, _, elemente, _, _ = _fassung(context, plan_id, fassung, version_id)
    return _element(elemente, element_id)


@_tool("katplan_orte_suchen", "Sucht verortete freigegebene Elemente im Umkreis, nach Distanz sortiert.")
async def katplan_orte_suchen(context: MCPContext, lat: float, lng: float, radius_m: float = 1000,
                             plan_id: int | None = None, typ: str = "") -> dict[str, object]:
    result = suche_service.suche(context.db, context.user, plan_id=plan_id, typ=typ,
                                 lat=lat, lng=lng, radius_m=radius_m, limit=50, mit_quellen=False)
    result["warnings"] = ["Treffer begrenzt."] if result["abgeschnitten"] else []
    plaene = {}
    for t in result["treffer"]:
        if t["plan_id"] not in plaene:
            plaene[t["plan_id"]] = _fassung(context, t["plan_id"], "freigegeben")[2]
        elemente = plaene[t["plan_id"]]
        t["geometry"] = _element(elemente, t["element_id"])["geometry"]
    return result


@_tool("katplan_dokument_auszug",
       "Liest bis 20 Quellseiten oder die Übersicht; download=True liefert einen kurzlebigen PDF-Link.")
async def katplan_dokument_auszug(context: MCPContext, plan_id: int, dokument_id: int,
                                 seite: int | None = None, von: int | None = None,
                                 bis: int | None = None, download: bool = False) -> dict[str, object]:
    plan = daten.plan_laden(context.db, context.user, plan_id)
    dokument = next((d for d in sichtbare_quellen(context.db, plan, context.user) if d.id == dokument_id), None)
    if dokument is None:
        raise LookupError("Nicht gefunden")
    if seite is not None and (von is not None or bis is not None):
        raise ValueError("Entweder seite oder von/bis angeben.")
    start, ende = (seite, seite) if seite is not None else (von, bis)
    bereich = start is not None or ende is not None
    if bereich and (start is None or ende is None or not 1 <= start <= ende <= dokument.seitenzahl
                    or ende - start >= 20):
        raise ValueError("Gültigen Seitenbereich mit maximal 20 Seiten angeben.")
    if bereich:
        assert start is not None and ende is not None
    rows = context.db.query(KatplanDokumentSeite).filter_by(
        org_id=context.org_id, plan_id=plan.id, dokument_id=dokument.id).order_by(KatplanDokumentSeite.seiten_nr).all()
    warnings = []
    seiten = []
    for s in rows:
        if bereich and start is not None and ende is not None and not (start <= s.seiten_nr <= ende):
            continue
        item = {"seiten_nr": s.seiten_nr, "titel": s.titel or None}
        if bereich:
            item["text"] = s.volltext or None
            if not s.volltext:
                warnings.append(f"Seite {s.seiten_nr}: Volltext nicht vorhanden.")
        if not s.titel:
            warnings.append(f"Seite {s.seiten_nr}: Titel nicht vorhanden.")
        seiten.append(item)
    if bereich and start is not None and ende is not None and len(seiten) != ende - start + 1:
        warnings.append("Nicht alle angeforderten Seiten sind vorhanden.")
    result: dict[str, object] = {"plan_id": plan.id, "dokument_id": dokument.id, "titel": dokument.titel,
                                "versionsnummer": dokument.versionsnummer, "seitenzahl": dokument.seitenzahl,
                                "seiten": seiten, "warnings": warnings}
    if download:
        token, ablauf = erstelle_download_token(
            context.org_id, context.user.id, dokument.id, seite, art="katplan_quelle")
        result.update(download_url=f"{settings.effective_public_base_url.rstrip('/')}/api/mcp/downloads/{token}",
                      gueltig_bis=ablauf.isoformat() + "Z")
    return result


@_tool("katplan_verknuepfungen", "Liest Live-Links und sichtbare ausgehende sowie eingehende Elementbeziehungen.")
async def katplan_verknuepfungen(context: MCPContext, plan_id: int, element_id: str | None = None,
                                fassung: str = "freigegeben", version_id: int | None = None) -> dict[str, object]:
    _, kopf, elemente, _, warnings = _fassung(context, plan_id, fassung, version_id)
    ausgewählt = [_element(elemente, element_id)] if element_id is not None else elemente
    ids = {e["element_id"] for e in ausgewählt}
    eingehend = []
    for p in suche_service.sichtbare_plaene(context.db, context.user, fassung == "arbeitsstand"):
        _, _, andere, _, _ = _fassung(context, p["id"], fassung)
        for e in andere:
            for r in e["relations"]:
                if r["element_id"] in ids:
                    eingehend.append({"plan_id": p["id"], "element_id": e["element_id"],
                                      "kennung": e["kennung"], "title": e["title"], "relation": r})
    return {"plan_id": plan_id, "links": daten.link_ansichten(context.db, context.user, kopf.get("links", [])),
            "elemente": [{k: e[k] for k in ("element_id", "kennung", "linked_entities", "relations")}
                         for e in ausgewählt],
            "eingehende_beziehungen": eingehend, "warnings": warnings}


def _quellseite(context: MCPContext, plan_id: int, dokument_id: int, seiten_nr: int):
    """Dieselbe Sichtbarkeit wie dokument_auszug, einschliesslich Arbeitsstandquellen."""
    from app.services.katplan_quelle_service import quelle_der_seite

    plan = daten.plan_laden(context.db, context.user, plan_id)
    dokument = next((d for d in sichtbare_quellen(context.db, plan, context.user) if d.id == dokument_id), None)
    if dokument is None:
        raise ValueError("Nicht gefunden")
    seite = quelle_der_seite(context.db, plan, dokument.id, seiten_nr)
    if seite is None:
        raise ValueError("Quellseite nicht gefunden.")
    return plan, dokument, seite


@_tool("katplan_quellseite_bilder", (
    "Listet bis 20 eingebettete Quellbilder ab 200 px je Achse und die Seitenbildgröße. "
    "vorschau=true liefert bis 8 JPEG-Vorschauen (maximal 512 px). Arbeitsquellen nur für Bearbeiter."
))
async def katplan_quellseite_bilder(context: MCPContext, plan_id: int, dokument_id: int,
                                   seiten_nr: int, vorschau: bool = True) -> dict[str, object]:
    import base64
    from dataclasses import asdict
    from io import BytesIO

    from PIL import Image

    from app.services import katplan_quellbild_service as bilder

    _, _, seite = _quellseite(context, plan_id, dokument_id, seiten_nr)
    warnings: list[str] = []
    infos = bilder.eingebettete_bilder(seite, warnings=warnings)
    vorschauen = []
    if vorschau:
        for info in infos[:8]:
            try:
                data, _ = bilder.bild_bytes(seite, info.index)
                with Image.open(BytesIO(data)) as img:
                    data = bilder.jpeg_bytes(img, 512)
                vorschauen.append({"index": info.index, "bild_base64": base64.b64encode(data).decode("ascii"),
                                   "bild_mime": "image/jpeg"})
            except ValueError:
                warnings.append(f"Bild {info.index}: Vorschau nicht verfügbar.")
    return {"plan_id": plan_id, "dokument_id": dokument_id, "seiten_nr": seiten_nr,
            "bilder": [asdict(info) for info in infos], "seitenbild": bilder.seitenbild_info(seite),
            "vorschauen": vorschauen, "warnings": warnings}


@_tool("katplan_import_abgleichen", "Gleicht eine Quelle mit dem Arbeitsstand ab: Seitenlücken, fehlende und "
       "veraltete Quellbezüge, mögliche Dubletten und offene Vorschläge. Nur für Bearbeiter; rein lesend.")
async def katplan_import_abgleichen(context: MCPContext, plan_id: int, dokument_id: int) -> dict[str, object]:
    from app.services.katplan_abgleich_service import abgleich

    daten.pruefe_fassung(context.user, "arbeitsstand")
    plan = daten.plan_laden(context.db, context.user, plan_id)
    return abgleich(context.db, plan, context.user, dokument_id)
