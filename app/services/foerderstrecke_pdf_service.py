"""PDF „Einsatzplan Wasserförderung" für eine gespeicherte Förderstrecke (PR 6).

Rechnet aus den persistierten Stationen (Katalog-Auflösung + gespeicherte Abschnitts-
Geometrie) die aktuelle Hydraulik neu, erzeugt das Höhenprofil-SVG und rendert das
PDF (WeasyPrint mit xhtml2pdf-Fallback, Muster pdf_service).
"""
from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime

from app.core.templating import templates
from app.models.foerderstrecke import (
    STATION_TYPEN,
    FoerderPumpenTyp,
    FoerderSchlauchTyp,
    Foerderstrecke,
)
from app.services import foerderstrecke_service as engine
from app.services.chart_svg import foerderprofil_svg
from app.services.pdf_service import strip_font_face_for_xhtml2pdf

logger = logging.getLogger(__name__)


def _kennlinie_der_station(st, db) -> tuple[list, dict]:
    if not st.pumpen_typ_id:
        return [], {}
    p = db.get(FoerderPumpenTyp, st.pumpen_typ_id)
    if p is None:
        return [], {}
    kl_alle = p.kennlinien
    rpm = str(st.rpm or "")
    kl = kl_alle.get(rpm) if rpm in kl_alle else (
        kl_alle.get(p.drehzahlstufen[0]) if p.drehzahlstufen else [])
    return list(kl or []), {
        "max_ausgangsdruck_bar": p.max_ausgangsdruck_bar,
        "min_eingangsdruck_bar": p.min_eingangsdruck_bar,
        "name": p.name,
    }


def _ansaug_eingangsdruck(a: dict) -> float | None:
    """Vordruck (Hydrant/Netz) aus dem gespeicherten Ansaug-Dict, sonst None."""
    if not a.get("druckspeisung"):
        return None
    wert = a.get("eingangsdruck_bar")
    if wert is None or wert == "":
        return None
    try:
        return float(wert)
    except (TypeError, ValueError):
        return None


def berechne_gespeicherte_strecke(strecke: Foerderstrecke, db) -> dict:
    """Baut Engine-Eingaben aus den ORM-Stationen und rechnet Modus A.

    Rückgabe: {ergebnis, material, svg, stationen_info (angereichert)}.
    """
    a = strecke.ansaug or {}
    ansaug = engine.Ansaugpunkt(
        seehoehe_m=float(a.get("seehoehe_m") or 430.0),
        geodaetische_saughoehe_m=float(a.get("geodaetische_saughoehe_m") or 3.0),
        saug_k=float(a.get("saug_k") or 0.23),
        saug_n_parallel=int(a.get("saug_n_parallel") or 1),
        saugleitung_laenge_m=float(a.get("saugleitung_laenge_m") or 0.0),
        max_ansaughoehe_m=float(a.get("max_ansaughoehe_m") or 7.5),
        saug_scheitel_m=float(a.get("saug_scheitel_m") or 0.0),
        eingangsdruck_bar=_ansaug_eingangsdruck(a),
    )
    full_profil = json.loads(strecke.hoehenprofil_json) if strecke.hoehenprofil_json else None
    stationen_orm = sorted(strecke.stationen, key=lambda s: (s.strang_nr, s.sort))
    stationen: list[engine.PumpenStation] = []
    material_abschnitte: list[dict] = []
    info: list[dict] = []
    s_kumuliert = 0.0
    for st in stationen_orm:
        kl, meta = _kennlinie_der_station(st, db)
        schlauch = db.get(FoerderSchlauchTyp, st.schlauch_typ_id) if st.schlauch_typ_id else None
        laenge = float(st.abschnitt_laenge_m or 0.0)
        # Zwischengelände (Damm etc.) aus dem gespeicherten Gesamtprofil je Abschnitt
        stuetz = None
        delta = float(st.abschnitt_delta_hoehe_m or 0.0)
        if full_profil and laenge > 0:
            aus_profil = engine.abschnitt_hoehen_stuetzpunkte(
                full_profil, s_kumuliert, s_kumuliert + laenge)
            if aus_profil:
                stuetz = aus_profil
                delta = aus_profil[-1]
        abschnitt = engine.Abschnitt(
            schlauch_k=float(schlauch.k_verlust) if schlauch else 0.0,
            laenge_m=laenge,
            n_parallel=int(st.druck_parallel or 1),
            delta_hoehe_m=delta,
            max_betriebsdruck_bar=schlauch.max_betriebsdruck_bar if schlauch else None,
            hoehen_stuetzpunkte=stuetz,
        ) if laenge > 0 else None
        s_kumuliert += laenge
        stationen.append(engine.PumpenStation(
            kennlinie=kl, typ=st.typ,
            max_ausgangsdruck_bar=meta.get("max_ausgangsdruck_bar"),
            min_eingangsdruck_bar=float(meta.get("min_eingangsdruck_bar")
                                        or engine.DEFAULT_MIN_EINGANGSDRUCK_BAR),
            behaelter_volumen_l=st.behaelter_volumen_l,
            name=meta.get("name") or STATION_TYPEN.get(st.typ, st.typ),
            abschnitt_danach=abschnitt,
        ))
        info.append({
            "name": meta.get("name") or "", "typ": st.typ,
            "typ_label": STATION_TYPEN.get(st.typ, st.typ),
            "lat": st.lat, "lng": st.lng, "schlauch": schlauch.kuerzel if schlauch else None,
            "laenge_m": laenge, "n_parallel": st.druck_parallel,
        })
        if abschnitt and schlauch:
            material_abschnitte.append({
                "kuerzel": schlauch.kuerzel, "laenge_m": laenge,
                "n_parallel": st.druck_parallel, "element_laenge_m": schlauch.element_laenge_m,
                "wasserinhalt_l_m": schlauch.wasserinhalt_l_m,
            })

    param = strecke.parameter or {}
    if stationen:
        ergebnis: dict = engine.berechne_modus_a(
            ansaug, stationen, ziel_druck_bar=0.0,
            armaturen_zuschlag=float(param.get("armaturen_zuschlag") or 0.05),
            hochpunkt_min_bar=float(param.get("hochpunkt_min_bar") or engine.HOCHPUNKT_MIN_BAR),
        )
    else:
        ergebnis = {"q_max_l_min": 0, "machbar": False, "druckprofil": [],
                    "stationswerte": [], "warnungen": ["Keine Stationen"], "engpass": None}
    material = engine.materialbilanz(material_abschnitte, float(ergebnis["q_max_l_min"]))

    marken, s = [], 0.0
    for ps in stationen:
        marken.append({"s_m": s, "label": ps.name})
        if ps.abschnitt_danach:
            s += ps.abschnitt_danach.laenge_m
    grenzen = [st.abschnitt_danach.max_betriebsdruck_bar for st in stationen
               if st.abschnitt_danach and st.abschnitt_danach.max_betriebsdruck_bar]
    svg = foerderprofil_svg(ergebnis["druckprofil"], hoehenprofil=full_profil,
                            p_max_bar=min(grenzen) if grenzen else None, stationen=marken,
                            titel=strecke.name)
    return {"ergebnis": ergebnis, "material": material, "svg": svg,
            "stationen_info": info, "stationswerte": ergebnis["stationswerte"],
            "gesamt_laenge_m": _route_laenge_m(strecke, s_kumuliert)}


def _route_laenge_m(strecke: Foerderstrecke, fallback_m: float) -> float:
    """Gesamtlänge der Förderleitung: bevorzugt aus dem gezeichneten Weg (route_geojson),
    sonst die Summe der Abschnittslängen (Pumpe → Pumpe → Ziel)."""
    if strecke.route_geojson:
        try:
            gj = json.loads(strecke.route_geojson)
            coords = gj.get("coordinates") if isinstance(gj, dict) else None
            pts = [(float(c[1]), float(c[0])) for c in (coords or []) if len(c) >= 2]
            if len(pts) >= 2:
                from app.services.hoehen_service import haversine_m
                return round(sum(haversine_m(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                                 for i in range(len(pts) - 1)))
        except (ValueError, TypeError, KeyError):
            pass
    return round(fallback_m)


# Markerfarben je Stationstyp (Kartenbild): Quelle grün, Relais violett, Ziel orange.
_MARKER_FARBEN = {"quellpumpe": "#16a34a"}
_MARKER_FARBE_RELAIS = "#7c3aed"
_MARKER_FARBE_ZIEL = "#ea580c"


def _karte_route_und_marker(strecke: Foerderstrecke, stationen_info: list[dict]):
    """Baut (route, marker) für das Kartenbild aus Strecke + angereicherten Stationen.

    route: Wegstrecken-Linie [(lat, lng), …] — bevorzugt das gespeicherte
    route_geojson (tatsächlich gezeichneter Weg), sonst die Pumpenfolge.
    marker: Pumpenstandorte (nach Typ eingefärbt) + optionaler Ziel-/Auslasspunkt.
    """
    marker: list[dict] = []
    for i in stationen_info:
        if i.get("lat") is None or i.get("lng") is None:
            continue
        marker.append({"lat": i["lat"], "lng": i["lng"],
                       "color": _MARKER_FARBEN.get(str(i.get("typ")), _MARKER_FARBE_RELAIS)})
    auslass = strecke.auslass or {}
    if auslass.get("lat") is not None and auslass.get("lng") is not None:
        marker.append({"lat": auslass["lat"], "lng": auslass["lng"],
                       "color": _MARKER_FARBE_ZIEL, "radius": 11})

    route: list[tuple[float, float]] = []
    if strecke.route_geojson:
        try:
            gj = json.loads(strecke.route_geojson)
            coords = gj.get("coordinates") if isinstance(gj, dict) else None
            # GeoJSON-Koordinaten sind [lng, lat]
            route = [(float(c[1]), float(c[0])) for c in (coords or []) if len(c) >= 2]
        except (ValueError, TypeError, KeyError):
            route = []
    if len(route) < 2:
        # Fallback: Linie über die Pumpenfolge (+ Ziel)
        route = [(m["lat"], m["lng"]) for m in marker]
    return route, marker


def karte_png_datauri(strecke: Foerderstrecke, stationen_info: list[dict]) -> str | None:
    """Rendert das Strecken-Kartenbild als data:-URI (Wegstrecke + alle Pumpen + Ziel).

    Gibt None zurück, wenn keine Koordinaten vorliegen oder das Tile-Rendering
    fehlschlägt (Karte ist ein optionaler Baustein von PDF/Maschinisten-Zettel).
    """
    route, marker = _karte_route_und_marker(strecke, stationen_info)
    if not marker and len(route) < 2:
        return None
    try:
        import base64

        from app.services.staticmap_service import render_route_map_png
        png = render_route_map_png(route if len(route) >= 2 else None, marker)
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    except Exception as exc:  # Netz/Tiles nicht verfügbar → ohne Karte
        logger.info("Förderstrecke-Kartenbild nicht gerendert: %s", exc)
        return None


def render_foerderstrecke_pdf(strecke: Foerderstrecke, org, db, base_url: str = "") -> bytes:
    daten = berechne_gespeicherte_strecke(strecke, db)
    # Kartenausschnitt (optional, Netz): Wegstrecke + alle Pumpenstandorte + Ziel
    karte_png = karte_png_datauri(strecke, daten["stationen_info"])

    from app.core.timezones import format_local_datetime
    erstellt_str = format_local_datetime(datetime.now(UTC), org)
    template = templates.env.get_template("pdf/foerderstrecke.html")
    html_str = template.render(
        strecke=strecke, org=org, erstellt_str=erstellt_str, base_url=base_url,
        ergebnis=daten["ergebnis"], material=daten["material"], svg=daten["svg"],
        stationen_info=daten["stationen_info"], stationswerte=daten["stationswerte"],
        gesamt_laenge_m=daten["gesamt_laenge_m"],
        karte_png=karte_png,
    )
    try:
        from weasyprint import HTML  # noqa: PLC0415
        buf = io.BytesIO()
        HTML(string=html_str, base_url=base_url or ".").write_pdf(buf)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("WeasyPrint fehlgeschlagen (Förderstrecke-PDF), Fallback xhtml2pdf: %s", exc)
        from xhtml2pdf import pisa  # noqa: PLC0415
        buf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(strip_font_face_for_xhtml2pdf(html_str)), dest=buf)
        return buf.getvalue()
