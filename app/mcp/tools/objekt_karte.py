"""MCP-Workflow fuer Lagekarten: lesen, georeferenzieren, vorschlagen und freigeben."""

import asyncio
import json

from app.core.permissions import EINSATZ_LESE_ROLLEN
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.mcp.tools.objekt import objekt_modul_aktiv
from app.models.objekt import OBJEKT_SYMBOL_TYPEN, Objekt, ObjektBspOverlay, ObjektDokumentSeite
from app.services.objekt_karte_service import _karten_objekt_dict, karten_objekte_lesen, symbolkatalog_lesen
from app.services.objekt_symbol_service import lade_symbol_katalog


def _bsp_overlay_dict(context: MCPContext, overlay: ObjektBspOverlay) -> dict:
    from app.config import settings
    from app.services import objekt_bsp_overlay_service as bsp

    auslieferbar = bsp.overlay_ist_auslieferbar(context.db, overlay)
    seite = context.db.query(ObjektDokumentSeite).filter_by(
        org_id=context.org_id, objekt_id=overlay.objekt_id,
        dokument_id=overlay.dokument_id, seiten_nr=overlay.seite,
    ).first()
    daten = {feld: getattr(overlay, feld) for feld in (
        "id", "name", "status", "plan_art", "geschoss", "bereich", "dokument_id", "seite",
        "freigabe_nr", "georef_id", "georef_revision",
    )}
    daten.update({
        "auslieferbar": auslieferbar,
        "ausschnitt": json.loads(overlay.ausschnitt_json),
        "editor_link": (
            f"{settings.effective_public_base_url.rstrip('/')}/objekte/{overlay.objekt_id}"
            f"/dokumente/seite/{seite.id}/georef#bsp-overlay"
        ) if seite else None,
    })
    if auslieferbar:
        daten["raster_bounds"] = json.loads(overlay.raster_bounds_json or "[]")
    else:
        daten["auslieferbar_grund"] = (
            "Menschliche Freigabe erforderlich" if overlay.status != "freigegeben"
            else "Georeferenz, Dokumentstand oder Raster nicht mehr aktuell bzw. gueltig"
        )
    return daten


def _bsp_overlays(context: MCPContext, objekt: Objekt) -> list[dict]:
    return [_bsp_overlay_dict(context, overlay) for overlay in context.db.query(ObjektBspOverlay).filter_by(
        objekt_id=objekt.id, org_id=context.org_id,
    ).order_by(ObjektBspOverlay.id).all()]


@register_tool(
    name="objekt_karte_lesen",
    description="Liest Objekt-Lagekarte und optional die vorhandene Arbeitskopie. Koordinaten nie erfinden.",
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_lesen(context: MCPContext, objekt_id: int, arbeitskopie: bool = False) -> dict[str, object]:
    db, org_id = context.db, context.org_id
    objekt = db.query(Objekt).filter(Objekt.id == objekt_id, Objekt.org_id == org_id).first()
    if objekt is None:
        raise ValueError("Objekt nicht gefunden.")
    kopie = db.query(Objekt).filter(Objekt.entwurf_von_id == objekt.id, Objekt.org_id == org_id).first()
    basis_objekt_id = objekt.entwurf_von_id
    if arbeitskopie and kopie is not None:
        basis_objekt_id = objekt.id
        objekt = kopie
    # Auch inaktive Katalogeintraege koennen noch von Bestandsmarkern verwendet werden.
    labels = dict(OBJEKT_SYMBOL_TYPEN) | {
        s.code: s.name for s in lade_symbol_katalog(db, org_id, nur_aktive=False)
    }
    marker = [
        _karten_objekt_dict(k) | {
            "symbolname": labels.get(k.typ, k.typ), "sort": k.sort,
            "position_status": k.position_status, "revision": k.revision,
            "quelle_dokument_id": k.quelle_dokument_id, "quelle_seite": k.quelle_seite,
            "genauigkeit_m": k.genauigkeit_m, "geprueft_von": k.geprueft_von,
            "geprueft_am": k.geprueft_am.isoformat() if k.geprueft_am else None,
        }
        for k in karten_objekte_lesen(db, objekt)
    ]
    return {
        "objekt": {"id": objekt.id, "name": objekt.name, "lat": objekt.lat, "lng": objekt.lng},
        "marker": marker,
        "bsp_overlays": _bsp_overlays(context, objekt),
        "anzahl_marker": len(marker),
        "basis_objekt_id": basis_objekt_id,
        "hat_arbeitskopie": kopie is not None,
        "arbeitskopie_id": kopie.id if kopie else None,
        "hinweis": "Legacy-Marker sind ungeprüfter Bestand. Koordinaten nie erfinden.",
    }


@register_tool(
    name="objekt_karte_symbolkatalog",
    description=("Liest den Symbolkatalog der eigenen Organisation. Legendenbegriffe ueber aliase abgleichen; "
                 "bei traegt_richtung bild_richtung angeben. Koordinaten nie erfinden."),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_symbolkatalog(context: MCPContext) -> dict[str, object]:
    return {"symbole": symbolkatalog_lesen(context.db, context.org_id)}


@register_tool(
    name="objekt_karte_bsp_arbeitsliste",
    description=(
        "Arbeitsliste offener Lageplan-/BSP-Seiten mit naechstem Schritt. Ablauf: "
        "1) objekt_dokument_seite_ansehen (rotation merken, ausschnitt fuer Details), "
        "2) georef_uebernehmen oder georef_zuordnen erst ohne speichern, bei Klasse A speichern, "
        "bei C editor_link an Menschen, 3) objekt_karte_symbolkatalog (aliase) und "
        "objekt_karte_vorschlaege mit georef_id, bild_x/bild_y, bild_richtung und batch_id, "
        "4) objekt_karte_pruefen und objekt_karte_vorschau, "
        "5) Freigabe nur nach ausdruecklicher Bestaetigung des Menschen. Koordinaten nie erfinden."
    ),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_bsp_arbeitsliste(
    context: MCPContext, objekt_id: int | None = None, nur_offen: bool = True, limit: int = 50,
) -> dict[str, object]:
    from app.mcp.context import MCPPermissionError
    from app.services.objekt_bsp_arbeitsliste_service import arbeitsliste_lesen

    if context.org_id != context.user.org_id:
        raise MCPPermissionError("Organisation stimmt nicht ueberein")
    return arbeitsliste_lesen(context.db, context.org_id, objekt_id, nur_offen, limit)


@register_tool(
    name="objekt_karte_vorschlaege",
    description=(
        "Verwaltet bis 30 Kartenvorschläge: anlegen, aendern, verwerfen, auflisten. "
        "Zuerst Karte, Symbolkatalog und Dokumentseiten lesen; Planpositionen georeferenzieren. "
        "Koordinaten nie erfinden; ohne belastbare Position lat/lng weglassen. "
        "Vorschläge ändern die produktive Karte nicht. Freigabe erfolgt separat durch Berechtigte. "
        "pruefen=true: Trockenlauf mit Rollback. aendern: genau ein Eintrag und vorschlag_id. "
        "Eintrag: symbol_code, operation (hinzufuegen|aendern|entfernen), lat/lng oder geometry, label, "
        "georef_id, bild_x, bild_y (normiert 0..1, Ursprung oben links). Mit georef_id keine lat/lng oder geometry; "
        "der Server rechnet um. Bildpositionen ergeben ohne gueltige Georeferenz keine Position. "
        "Fuer Pfeile/Zugaenge bild_richtung {dx,dy} statt rotation: sichtbare Richtung im Bild "
        "(rechts +dx, unten +dy, Pixelverhaeltnis); Server rechnet Kartenrotation. "
        "Wiederholung erzeugt keine Dubletten. batch_id kennzeichnet einen Lauf (1-64 Zeichen, A-Za-z0-9_.:-). "
        "Fehlen eines Symbols im neuen Plan ist kein Grund fuer entfernen. "
        "rotation, position_methode, genauigkeit_m, erkennungs_konfidenz (0..1), quelle_dokument_id, "
        "quelle_seite, quelle_ausschnitt ([x0,y0,x1,y1], 0..1), begruendung. "
        "Markeränderungen benötigen basis_marker_id und expected_revision. filter_status ist der Freigabestatus. "
        "veraltete_markieren=true ist eine explizite Schreibaktion (nur Objektverwaltung), auch beim Auflisten."
    ),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_vorschlaege(
    context: MCPContext, objekt_id: int, aktion: str,
    vorschlaege: list[dict] | None = None, vorschlag_id: int | None = None,
    filter_status: str | None = None, pruefen: bool = False, veraltete_markieren: bool = False,
    batch_id: str | None = None,
) -> dict[str, object]:
    from app.core.permissions import has_role
    from app.mcp.context import MCPPermissionError
    from app.services import objekt_karte_vorschlag_service as service

    if aktion not in {"anlegen", "aendern", "verwerfen", "auflisten"}:
        raise ValueError("Unbekannte Aktion")
    if context.org_id != context.user.org_id:
        raise MCPPermissionError("Organisation stimmt nicht ueberein")
    if (aktion != "auflisten" or veraltete_markieren) and not has_role(context.user, "objekt_verwalter"):
        raise MCPPermissionError("Objektverwaltung erforderlich")
    if batch_id is not None:
        service.batch_id_validieren(batch_id)
    db = context.db
    objekt = service.produktives_objekt(db, context.org_id, objekt_id)
    if aktion == "auflisten":
        try:
            if veraltete_markieren:
                from app.services.objekt_karte_freigabe_service import markiere_veraltete
                markiere_veraltete(db, objekt)
            antwort_liste: dict[str, object] = {"vorschlaege": [
                service.vorschlag_dict(v, db, objekt)
                for v in service.vorschlaege_auflisten(db, objekt, filter_status, batch_id)
            ]}
            if veraltete_markieren:
                if pruefen:
                    db.rollback()
                else:
                    db.commit()
            return antwort_liste
        except Exception:
            db.rollback()
            raise
    if vorschlaege is not None and (not isinstance(vorschlaege, list) or len(vorschlaege) > 30
                                   or any(not isinstance(v, dict) for v in vorschlaege)):
        raise ValueError("Maximal 30 Vorschlaege als Liste von Objekten")
    if aktion == "anlegen" and (not vorschlaege or vorschlag_id is not None):
        raise ValueError("anlegen benoetigt Vorschlaege ohne vorschlag_id")
    if aktion == "aendern" and (vorschlag_id is None or not vorschlaege or len(vorschlaege) != 1):
        raise ValueError("aendern benoetigt vorschlag_id und genau einen Vorschlag")
    if aktion == "verwerfen" and (vorschlag_id is None or vorschlaege):
        raise ValueError("verwerfen benoetigt nur vorschlag_id")
    try:
        if veraltete_markieren:
            from app.services.objekt_karte_freigabe_service import markiere_veraltete
            markiere_veraltete(db, objekt)
        if aktion == "verwerfen":
            assert vorschlag_id is not None
            ergebnisse = [service.vorschlag_verwerfen(db, objekt, vorschlag_id, user_id=context.user.id)]
        else:
            ergebnisse = [service.vorschlag_speichern(
                          db, objekt, daten | ({"batch_id": batch_id} if batch_id is not None else {}),
                          user_id=context.user.id,
                          vorschlag_id=vorschlag_id) for daten in (vorschlaege or [])]
        antwort: dict[str, object] = {"vorschlaege": ergebnisse, "gespeichert": not pruefen}
        if aktion == "anlegen":
            antwort["zusammenfassung"] = {
                ergebnis: sum(e["ergebnis"] == ergebnis for e in ergebnisse)
                for ergebnis in ("neu", "aktualisiert", "unveraendert", "unveraendert_offen")
            }
        if pruefen:
            db.rollback()
        else:
            db.commit()
        return antwort
    except Exception:
        db.rollback()
        raise


@register_tool(
    name="objekt_karte_pruefen",
    description=("Prüft die Objektkarte lesend auf Dubletten, fehlende Positionen und veraltete Quellen. "
                 "Koordinaten nie erfinden. Veraltet wird nur in der Antwort markiert; Freigabe erfolgt separat."),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_pruefen(context: MCPContext, objekt_id: int) -> dict[str, object]:
    from app.services.objekt_karte_vorschlag_service import produktives_objekt, pruefe_karte

    return pruefe_karte(context.db, produktives_objekt(context.db, context.org_id, objekt_id))


@register_tool(
    name="objekt_karte_referenzpunkte",
    description=(
        "Setzt, liest oder verwirft Georeferenzen einer Planseite. Zuerst aktuelle Dokumentseite ansehen "
        "und Referenzquellen identifizieren; zuerst mit pruefen=true testen. Bildkoordinaten x,y normiert 0..1, "
        "Ursprung oben links auf der gerenderten, angezeigten Seite. Mindestens 3 gut verteilte, eindeutig "
        "identifizierte Punkte (affin: 4, Homographie: 6). Quellen: OSM-Ecken (osm_gebaeude), "
        "Orthofoto-Klick (orthofoto_klick), dokumentierte Koordinate (dokument_koordinate), manuell. "
        "Punkte: id, x, y, lat, lng, quelle, beschreibung. Keine Koordinaten schaetzen oder selbst umrechnen. "
        "RMSE maximal 2 m, Einzelfehler maximal 4 m; Genauigkeit durch Referenzquelle begrenzt. "
        "Abgelehnte/veraltete Referenzen ergeben keine Position. pruefen=true: Rollback. "
        "Bei unsicherer Zuordnung den editor_link an den Menschen weitergeben "
        "(vier Gebäudeecken in Plan und Orthofoto anklicken)."
    ),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_referenzpunkte(
    context: MCPContext, objekt_id: int, dokument_id: int, seite: int, aktion: str,
    punkte: list[dict] | None = None, modell: str | None = None, pruefen: bool = False,
) -> dict[str, object]:
    from app.config import settings
    from app.core.permissions import has_role
    from app.mcp.context import MCPPermissionError
    from app.services.objekt_georef_service import _seite, referenzpunkte
    from app.services.objekt_karte_vorschlag_service import produktives_objekt

    if aktion not in {"setzen", "lesen", "verwerfen"}:
        raise ValueError("Unbekannte Aktion")
    if context.org_id != context.user.org_id:
        raise MCPPermissionError("Organisation stimmt nicht ueberein")
    if aktion != "lesen" and not has_role(context.user, "objekt_verwalter"):
        raise MCPPermissionError("Objektverwaltung erforderlich")
    db = context.db
    objekt = produktives_objekt(db, context.org_id, objekt_id)
    try:
        antwort = referenzpunkte(db, objekt, dokument_id, seite, aktion, punkte, modell, context.user.id)
        s = _seite(db, objekt, dokument_id, seite)
        antwort["editor_link"] = (
            f"{settings.effective_public_base_url.rstrip('/')}/objekte/{objekt.id}/dokumente/seite/{s.id}/georef"
        )
        antwort["hinweise"] = (antwort.get("georeferenz") or {}).get("hinweise", [])
        if aktion != "lesen":
            antwort["gespeichert"] = not pruefen
            if pruefen:
                db.rollback()
            else:
                db.commit()
        return antwort
    except Exception:
        db.rollback()
        raise


@register_tool(
    name="objekt_karte_gebaeude_umrisse",
    description=("Liest OSM-Gebaeudeumrisse mit nummerierten Ecken als Referenzquelle. "
                 "Nur eindeutig im Plan identifizierte OSM-Ecken verwenden, keine Koordinaten schaetzen. "
                 "Mindestens 3 gut verteilte Referenzpunkte nutzen und Restfehler beachten. "
                 "Alternativen: Orthofoto-Klick oder dokumentierte Koordinate."),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_gebaeude_umrisse(context: MCPContext, objekt_id: int, radius_m: int = 150) -> dict:
    from app.mcp.context import MCPPermissionError
    from app.services import objekt_gebaeude_service
    from app.services.objekt_karte_vorschlag_service import produktives_objekt

    if context.org_id != context.user.org_id:
        raise MCPPermissionError("Organisation stimmt nicht ueberein")
    objekt = produktives_objekt(context.db, context.org_id, objekt_id)
    lat, lng = _overpass_freigabe_pruefen(context, objekt)
    return await objekt_gebaeude_service.gebaeude_umrisse(lat, lng, radius_m)


def _overpass_freigabe_pruefen(context: MCPContext, objekt: Objekt) -> tuple[float, float]:
    from app.config import settings
    from app.models.master import OrgSettings

    if objekt.lat is None or objekt.lng is None:
        raise ValueError("Objekt hat keine belastbare Bezugskoordinate")
    org_settings = context.db.query(OrgSettings).filter_by(org_id=context.org_id).first()
    if not settings.HYDRANT_ENABLED or (org_settings is not None and not org_settings.hydrant_layer_enabled):
        raise ValueError("OSM-/Overpass-Abfragen sind fuer diese Organisation deaktiviert")
    return objekt.lat, objekt.lng


@register_tool(
    name="objekt_karte_georef_zuordnen",
    description=(
        "Ordnet Bildecken deterministisch OSM-Gebaeudeecken zu. Seite vorher mit "
        "objekt_dokument_seite_ansehen ansehen; expected_rotation aus der angesehenen Seite angeben. "
        "Alle Aussenecken EINES Gebaeudes entlang der Kontur markieren: bild_ecken als {x,y}, "
        "normiert 0..1, mindestens 4 Ecken; Startpunkt und Umlaufrichtung egal. "
        "Keine Koordinaten liefern oder schaetzen. Erst ohne speichern pruefen. "
        "Nur eindeutige Klasse A kann die Objektverwaltung speichern. Bei Klasse C keine Hypothese "
        "selbst auswaehlen, sondern editor_link an den Menschen geben. Bei Nachbargebaeuden "
        "osm_id aus objekt_karte_gebaeude_umrisse angeben."
    ),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_georef_zuordnen(
    context: MCPContext, objekt_id: int, dokument_id: int, seite: int, bild_ecken: list[dict],
    expected_rotation: int, osm_id: int | None = None, radius_m: int = 150,
    modell: str = "aehnlichkeit", speichern: bool = False,
) -> dict:
    from app.config import settings
    from app.core.permissions import has_role
    from app.mcp.context import MCPPermissionError
    from app.services import objekt_gebaeude_service, objekt_georef_service, objekt_georef_zuordnung_service
    from app.services.objekt_karte_service import ObjektKartenFehler
    from app.services.objekt_karte_vorschlag_service import produktives_objekt

    if context.org_id != context.user.org_id:
        raise MCPPermissionError("Organisation stimmt nicht ueberein")
    if speichern and not has_role(context.user, "objekt_verwalter"):
        raise MCPPermissionError("Objektverwaltung erforderlich")
    db = context.db
    objekt = produktives_objekt(db, context.org_id, objekt_id)
    lat, lng = _overpass_freigabe_pruefen(context, objekt)
    s = objekt_georef_service._seite(db, objekt, dokument_id, seite)
    if expected_rotation != s.rotation:
        raise ObjektKartenFehler("Dokumentseite wurde inzwischen gedreht; Seite neu ansehen")
    breite, hoehe = objekt_georef_service._bildgroesse(s)
    umrisse = await objekt_gebaeude_service.gebaeude_umrisse(lat, lng, radius_m)
    if speichern and umrisse.get("veraltet"):
        alter = umrisse.get("alter_tage")
        alter_text = f"sind {alter} Tage alt" if alter is not None else "haben keinen bekannten Datenstand"
        raise ObjektKartenFehler(
            f"OSM-Gebaeudedaten {alter_text} "
            f"(Grenze {settings.OSM_GEBAEUDE_MAX_ALTER_TAGE}); Import aktualisieren, dann erneut speichern"
        )
    gebaeude = umrisse["gebaeude"]
    if osm_id is not None:
        gebaeude = [g for g in gebaeude if g["osm_id"] == osm_id]
        if not gebaeude:
            raise ObjektKartenFehler("OSM-Gebaeude nicht im Umkreis gefunden")
    # Bis zu MAX_HYPOTHESEN Ausgleichsrechnungen: nicht im Event-Loop rechnen.
    ergebnis = await asyncio.to_thread(
        objekt_georef_zuordnung_service.hypothesen_bewerten, bild_ecken, gebaeude, breite, hoehe, modell
    )
    georeferenz = None
    if speichern and ergebnis["klasse"] == "A":
        try:
            antwort = objekt_georef_service.referenzpunkte(
                db, objekt, dokument_id, seite, "setzen", ergebnis["punkte"], modell, context.user.id,
                nur_gueltig=True, expected_rotation=expected_rotation,
            )
            georeferenz = antwort["georeferenz"]
            db.commit()
        except Exception:
            db.rollback()
            raise
    return ergebnis | {
        "gespeichert": georeferenz is not None, "georeferenz": georeferenz,
        "bildgroesse": [breite, hoehe], "rotation": s.rotation,
        "quelle": umrisse.get("quelle", "OpenStreetMap / Overpass"),
        **{feld: umrisse.get(feld) for feld in ("datenstand", "alter_tage", "veraltet")},
        "editor_link": (
            f"{settings.effective_public_base_url.rstrip('/')}/objekte/{objekt.id}/dokumente/seite/{s.id}/georef"
        ),
    }


@register_tool(
    name="objekt_karte_georef_uebernehmen",
    description=(
        "Uebernimmt eine gueltige alte Georeferenz nur innerhalb derselben Dokumentgruppe. "
        "Beide Seiten vorher mit objekt_dokument_seite_ansehen ansehen; rotation der neuen Seite "
        "als expected_rotation verwenden. Mindestens 4 identische, gut verteilte Planmerkmale "
        "(Gebaeudeecken, Achsen-Schnittpunkte) in beiden Seiten markieren. "
        "punktpaare: 4 bis 30 Objekte {von_x,von_y,nach_x,nach_y}, normiert auf die gedrehte Gesamtseite. "
        "Erst ohne speichern pruefen; nur die Objektverwaltung darf Klasse A speichern. "
        "Bei Klasse C editor_link an den Menschen geben."
    ),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_georef_uebernehmen(
    context: MCPContext, objekt_id: int, von_dokument_id: int, von_seite: int,
    nach_dokument_id: int, nach_seite: int, punktpaare: list[dict], expected_rotation: int,
    speichern: bool = False,
) -> dict:
    from app.config import settings
    from app.core.permissions import has_role
    from app.mcp.context import MCPPermissionError
    from app.services import objekt_georef_service, objekt_georef_uebernahme_service
    from app.services.objekt_karte_vorschlag_service import produktives_objekt

    if context.org_id != context.user.org_id:
        raise MCPPermissionError("Organisation stimmt nicht ueberein")
    if speichern and not has_role(context.user, "objekt_verwalter"):
        raise MCPPermissionError("Objektverwaltung erforderlich")
    db = context.db
    try:
        objekt = produktives_objekt(db, context.org_id, objekt_id)
        neue_seite = objekt_georef_service._seite(db, objekt, nach_dokument_id, nach_seite)
        editor_link = (
            f"{settings.effective_public_base_url.rstrip('/')}/objekte/{objekt.id}"
            f"/dokumente/seite/{neue_seite.id}/georef"
        )
        ergebnis = objekt_georef_uebernahme_service.uebernehmen(
            db, objekt, von_dokument_id, von_seite, nach_dokument_id, nach_seite,
            punktpaare, expected_rotation, context.user.id, speichern,
        )
        ergebnis["editor_link"] = editor_link
        if ergebnis["gespeichert"]:
            db.commit()
        else:
            db.rollback()
        return ergebnis
    except Exception:
        db.rollback()
        raise


@register_tool(
    name="objekt_bsp_overlay_entwurf",
    description=(
        "BSP-Overlay als Entwurf anlegen, aendern oder status lesen (overlay_id fuer aendern/status). "
        "Nur Objektverwaltung; pruefen=true ist standardmaessig ein Trockenlauf mit Rollback. "
        "Seite vorher mit objekt_dokument_seite_ansehen ansehen und expected_rotation angeben. "
        "ausschnitt: {art: seite}, {art: rechteck, x0,y0,x1,y1} oder {art: polygon, punkte: [{x,y}, ...]}. "
        "Polygon: 3 bis 100 Punkte, keine Selbstueberschneidung; Box berechnet der Server. Normiert 0..1 auf der "
        "gedrehten Seite. Gueltige gespeicherte Georef erforderlich; Server berechnet Vorschau-Bounds. "
        "Keine Koordinaten erfinden oder liefern. Nur Klasse A wird als entwurf gespeichert. "
        "editor_link zur menschlichen Pruefung/Freigabe weitergeben. "
        "Overlays erscheinen nie ohne menschliche Freigabe im Einsatz. Keine Freigabe per MCP."
    ),
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_bsp_overlay_entwurf(
    context: MCPContext, objekt_id: int, aktion: str, dokument_id: int | None = None,
    seite: int | None = None, overlay_id: int | None = None, name: str | None = None,
    plan_art: str | None = None, bereich: str | None = None, geschoss: str | None = None,
    ausschnitt: dict | None = None, expected_rotation: int | None = None, pruefen: bool = True,
) -> dict:
    from app.config import settings
    from app.core.permissions import has_role
    from app.mcp.context import MCPPermissionError
    from app.services import objekt_bsp_overlay_service as bsp
    from app.services import objekt_georef_service as geo
    from app.services.objekt_karte_service import ObjektKartenFehler
    from app.services.objekt_karte_vorschlag_service import produktives_objekt

    if context.org_id != context.user.org_id or not has_role(context.user, "objekt_verwalter"):
        raise MCPPermissionError("Objektverwaltung erforderlich")
    if aktion not in {"anlegen", "aendern", "status"}:
        raise ValueError("Nur anlegen, aendern oder status erlaubt; Freigabe ausschliesslich durch Menschen im Editor")
    db = context.db
    try:
        objekt = produktives_objekt(db, context.org_id, objekt_id)
        overlay = None
        if aktion != "anlegen":
            overlay = db.query(ObjektBspOverlay).filter_by(
                id=overlay_id, objekt_id=objekt.id, org_id=context.org_id,
            ).first()
            if overlay is None:
                raise ObjektKartenFehler("Overlay nicht gefunden", 404)
            if ((dokument_id is not None and dokument_id != overlay.dokument_id)
                    or (seite is not None and seite != overlay.seite)):
                raise ValueError("Dokument und Seite eines Overlays sind unveraenderlich")
            dokument_id, seite = overlay.dokument_id, overlay.seite
        elif overlay_id is not None or dokument_id is None or seite is None:
            raise ValueError("anlegen benoetigt dokument_id und seite ohne overlay_id")
        assert dokument_id is not None and seite is not None
        s = geo._seite(db, objekt, dokument_id, seite)
        if aktion != "status" and (type(expected_rotation) is not int or expected_rotation != s.rotation):
            raise ObjektKartenFehler(
                "Dokumentseite wurde inzwischen gedreht oder expected_rotation fehlt; Seite neu ansehen"
            )
        kandidat = overlay if overlay is not None else ObjektBspOverlay(
            objekt_id=objekt.id, org_id=context.org_id, dokument_id=dokument_id, seite=seite,
        )
        try:
            _, g = bsp._stand(db, kandidat)
        except ObjektKartenFehler:
            return {
                "gespeichert": False, "klasse": "C", "code": "BSP_GEOREF_UNGUELTIG",
                "fehler": "Aktuelles freigegebenes Dokument mit gueltiger Georeferenz erforderlich",
                "hinweis": "objekt_karte_georef_zuordnen oder objekt_karte_georef_uebernehmen nutzen",
                "editor_link": (
                    f"{settings.effective_public_base_url.rstrip('/')}"
                    f"/objekte/{objekt.id}/dokumente/seite/{s.id}/georef#bsp-overlay"
                ),
                "overlay": _bsp_overlay_dict(context, overlay) if overlay is not None else None,
            }
        daten = {k: v for k, v in {
            "name": name, "plan_art": plan_art, "bereich": bereich, "geschoss": geschoss,
            "ausschnitt": ausschnitt,
        }.items() if v is not None}
        if aktion == "status" and daten:
            raise ValueError("status erlaubt keine Aenderungen")
        crop = bsp.ausschnitt_validieren(ausschnitt if ausschnitt is not None else (
            json.loads(overlay.ausschnitt_json) if overlay else {"art": "seite"}
        ))
        _, groesse, bounds = bsp._raster_rechnung(json.loads(g.parameter_json or "{}"), crop, *geo._bildgroesse(s))
        if aktion == "anlegen":
            overlay = bsp.entwurf_anlegen(db, objekt.id, dokument_id, seite, daten, context.user.id)
        elif aktion == "aendern":
            assert overlay is not None
            overlay = bsp.entwurf_aendern(db, overlay, daten, context.user.id)
        assert overlay is not None
        overlay_daten = _bsp_overlay_dict(context, overlay)
        antwort = {
            "overlay": overlay_daten, "klasse": "A",
            "gespeichert": aktion != "status" and not pruefen,
            "vorschau": {"bounds": bounds, "raster_groesse": list(groesse), "georef_id": g.id,
                         "georef_revision": g.revision},
            "qualitaet": {"rmse_m": g.rmse_m, "punktzahl": len(json.loads(g.referenzpunkte_json)),
                          "modell": g.modell, "freigabe_warnungen": bsp.freigabe_warnungen(db, overlay)},
            "editor_link": overlay_daten["editor_link"],
            "hinweis": "Vorschau aus gueltiger Georef; menschliche Pruefung und Freigabe im Editor erforderlich",
        }
        if aktion != "status":
            if pruefen:
                db.rollback()
            else:
                db.commit()
        return antwort
    except Exception:
        db.rollback()
        raise


@register_tool(
    name="objekt_karte_vorschau",
    description=("Liest Marker und Vorschlaege getrennt mit Warnungen, Dubletten und Prueflink. "
                 "Nach Vorschlagserstellung den ui_link zur menschlichen Pruefung weitergeben. Keine Schreibwirkung."),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_vorschau(context: MCPContext, objekt_id: int,
                               status_filter: str | None = None) -> dict:
    from app.config import settings
    from app.services.objekt_karte_freigabe_service import review_liste
    from app.services.objekt_karte_vorschlag_service import produktives_objekt, pruefe_karte

    objekt = produktives_objekt(context.db, context.org_id, objekt_id)
    return {"marker": (await objekt_karte_lesen(context, objekt_id))["marker"],
            "bsp_overlays": _bsp_overlays(context, objekt),
            "vorschlaege": review_liste(context.db, objekt, status_filter),
            "pruefung": pruefe_karte(context.db, objekt),
            "ui_link": f"{settings.effective_public_base_url.rstrip('/')}/objekte/{objekt.id}/karte"}


@register_tool(
    name="objekt_karte_freigeben",
    description=("Uebernimmt Kartenvorschlaege atomar. Zuerst objekt_karte_vorschau und menschliche Pruefung. "
                 "Nur nach ausdruecklicher Bestaetigung durch den Benutzer "
                 "aufrufen; einsatzkritische Symbole nie ohne konkrete Nutzerfreigabe. "
                 "bestaetigung muss woertlich ICH_BESTAETIGE_FREIGABE sein. Jede Warnung je Vorschlags-ID "
                 "in bestaetigte_warnungen bestaetigen. pruefen=true: Trockenlauf mit Rollback. "
                 "Quellenalterung separat mit objekt_karte_vorschlaege und veraltete_markieren=true persistieren."),
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_freigeben(
    context: MCPContext, objekt_id: int, vorschlag_ids: list[int], bestaetigte_warnungen: dict,
    bestaetigung: str, pruefen: bool = False,
) -> dict:
    from app.core.permissions import has_role
    from app.mcp.context import MCPPermissionError
    from app.services import objekt_karte_freigabe_service as service
    from app.services.objekt_karte_vorschlag_service import produktives_objekt

    if context.org_id != context.user.org_id or not has_role(context.user, "objekt_verwalter"):
        raise MCPPermissionError("Objektverwaltung erforderlich")
    if bestaetigung != "ICH_BESTAETIGE_FREIGABE":
        raise ValueError("Ausdrueckliche Bestaetigung ICH_BESTAETIGE_FREIGABE erforderlich")
    db = context.db
    try:
        objekt = produktives_objekt(db, context.org_id, objekt_id)
        antwort = service.freigeben(db, objekt, vorschlag_ids, context.user, bestaetigte_warnungen, "mcp")
        antwort["gespeichert"] = not pruefen
        if pruefen:
            db.rollback()
        else:
            db.commit()
        return antwort
    except service.FreigabeFehler as exc:
        db.rollback()
        return {"gespeichert": False, "status_code": exc.status_code, "fehler": str(exc),
                "ergebnisse": exc.ergebnisse}
    except Exception:
        db.rollback()
        raise
