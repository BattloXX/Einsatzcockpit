"""Wasserstellen-Stammdaten: CSV-Import, Koordinaten-Konvertierung, Einsatz-Aufbereitung.

- MGI EPSG:31281 (Austria West, wie im EUS/GIS-Export) → WGS84 via pyproj.
- CSV-Import des Vorarlberger GIS-Formats (`;`-getrennt, cp1252, Spalten
  Bezeichnung … GRUPPE;UNTERGRUPP … RECHTSWERT;HOCHWERT).
- Umwandlung in das Hydranten-Dict-Format der Einsatzinfo-Karte.
- Dedupe der OSM-Hydranten gegen eigene Stammdaten (kein Doppelbild; OSM bleibt
  für Nachbarorte erhalten).
"""
from __future__ import annotations

import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.models.wasserstelle import (
    WASSERSTELLE_ICON_KAT,
    WASSERSTELLE_STATUS,
    WASSERSTELLE_TYPEN,
    Wasserstelle,
)
from app.services.hydrant_service import _haversine_m, _richtung

logger = logging.getLogger(__name__)


class WasserstelleFehler(ValueError):
    """Fachlicher Fehler beim Pflegen einer Wasserstelle."""


_ERLAUBTE_FELDER = {
    "bezeichnung", "typ", "lat", "lng", "hinweis", "ergiebigkeit_l_min", "status",
}


def status_aktiv(status: str) -> tuple[str, bool]:
    """Leitet das operative aktiv-Flag ab; nur defekte Stellen sind inaktiv."""
    return status, status != "defekt"


def _normalisiere_daten(daten: dict, *, create: bool = False) -> dict:
    unbekannt = set(daten) - _ERLAUBTE_FELDER
    if unbekannt:
        raise WasserstelleFehler("Unbekannte Wasserstellen-Felder: " + ", ".join(sorted(unbekannt)))
    out: dict = {}
    for feld, wert in daten.items():
        if feld == "bezeichnung":
            if not isinstance(wert, str) or not (bezeichnung := wert.strip()):
                raise WasserstelleFehler("Bezeichnung darf nicht leer sein.")
            if len(bezeichnung) > 250:
                raise WasserstelleFehler("Bezeichnung darf maximal 250 Zeichen haben.")
            out[feld] = bezeichnung
        elif feld == "typ":
            if wert not in WASSERSTELLE_TYPEN:
                raise WasserstelleFehler("Ungültiger Wasserstellen-Typ.")
            out[feld] = wert
        elif feld == "status":
            if wert not in WASSERSTELLE_STATUS:
                raise WasserstelleFehler("Ungültiger Wasserstellen-Status.")
            out[feld] = wert
        elif feld in {"lat", "lng"}:
            if wert is not None and (isinstance(wert, bool) or not isinstance(wert, (int, float))):
                raise WasserstelleFehler(f"{feld} muss eine Zahl oder None sein.")
            if wert is not None:
                wert = float(wert)
                minimum, maximum = (-90, 90) if feld == "lat" else (-180, 180)
                if not minimum <= wert <= maximum:
                    raise WasserstelleFehler(f"{feld} liegt außerhalb des gültigen Bereichs.")
            out[feld] = wert
        elif feld == "ergiebigkeit_l_min":
            if wert is not None and (isinstance(wert, bool) or not isinstance(wert, int) or wert < 0):
                raise WasserstelleFehler("ergiebigkeit_l_min muss eine ganze Zahl >= 0 oder None sein.")
            out[feld] = wert
        elif feld == "hinweis":
            if wert is not None and not isinstance(wert, str):
                raise WasserstelleFehler("hinweis muss ein Text oder None sein.")
            out[feld] = wert.strip() or None if wert is not None else None
    if create:
        for feld in ("bezeichnung", "typ"):
            if feld not in out:
                raise WasserstelleFehler(f"{feld} ist erforderlich.")
    return out


def erstelle_wasserstelle(
    db: Session, *, org_id: int, user_id: int | None, daten: dict,
    quelle: str = "manuell", audit_aktion: str = "wasserstelle.created",
) -> Wasserstelle:
    """Erstellt eine strikt validierte Wasserstelle samt Audit-Eintrag."""
    werte = _normalisiere_daten(daten, create=True)
    status = werte.get("status", "bereit")
    status, aktiv = status_aktiv(status)
    w = Wasserstelle(
        org_id=org_id, bezeichnung=werte["bezeichnung"], typ=werte["typ"],
        lat=werte.get("lat"), lng=werte.get("lng"), hinweis=werte.get("hinweis"),
        ergiebigkeit_l_min=werte.get("ergiebigkeit_l_min"), status=status, aktiv=aktiv,
        quelle=quelle, erstellt_von_id=user_id, aktualisiert_von_id=user_id,
    )
    db.add(w)
    db.flush()
    write_audit(db, audit_aktion, org_id=org_id, user_id=user_id, entity_type="wasserstelle",
                entity_id=w.id, payload={"bezeichnung": w.bezeichnung, "typ": w.typ})
    return w


def aktualisiere_wasserstelle(
    db: Session, w: Wasserstelle, *, user_id: int | None, daten: dict,
    audit_aktion: str = "wasserstelle.updated",
) -> list[dict]:
    """Aktualisiert nur übergebene Felder und gibt tatsächlich geänderte zurück."""
    werte = _normalisiere_daten(daten)
    geaendert: list[dict] = []
    for feld, nachher in werte.items():
        vorher = getattr(w, feld)
        if vorher != nachher:
            setattr(w, feld, nachher)
            geaendert.append({"feld": feld, "vorher": vorher, "nachher": nachher})
    if "status" in werte:
        w.status, w.aktiv = status_aktiv(w.status)
    w.aktualisiert_von_id = user_id
    db.flush()
    write_audit(db, audit_aktion, org_id=w.org_id, user_id=user_id, entity_type="wasserstelle",
                entity_id=w.id, payload={"bezeichnung": w.bezeichnung, "typ": w.typ})
    return geaendert

# ── MGI EPSG:31281 → WGS84 ──────────────────────────────────────────────────────
# Expliziter PROJ-String (zuverlässiger als EPSG:31281, falls die lokale proj-DB
# den False-Northing -5.000.000 für MGI Austria West nicht korrekt liefert).
# lon_0 = 28° ab Ferro = 10°20' ab Greenwich = 10.3333°, y_0 = -5.000.000 m.
_MGI_PROJ = (
    "+proj=tmerc +lat_0=0 +lon_0=10.3333333333333 +k=1 +x_0=0 +y_0=-5000000"
    " +ellps=bessel +towgs84=577.326,90.129,463.919,5.137,1.474,5.297,2.4232"
    " +units=m +no_defs"
)

_transformer = None
_transformer_init = False


def _get_transformer():
    global _transformer, _transformer_init
    if not _transformer_init:
        _transformer_init = True
        try:
            from pyproj import Transformer
            _transformer = Transformer.from_crs(_MGI_PROJ, "EPSG:4326", always_xy=True)
        except Exception as exc:  # pyproj nicht installiert / proj-DB defekt
            logger.warning("pyproj nicht verfügbar — MGI-Konvertierung deaktiviert: %s", exc)
            _transformer = None
    return _transformer


def mgi_zu_wgs84(rechtswert: float, hochwert: float) -> tuple[float | None, float | None]:
    """MGI-Rechts-/Hochwert (EPSG:31281) → (lat, lng). (None, None) bei Fehler."""
    t = _get_transformer()
    if t is None:
        return None, None
    try:
        lng, lat = t.transform(rechtswert, hochwert)
        return round(lat, 7), round(lng, 7)
    except Exception:
        return None, None


# ── CSV-Import (Vorarlberger GIS-Export) ────────────────────────────────────────

# UNTERGRUPP-Text → fachlicher Wasserstellen-Typ
_UNTERGRUPP_MAP: dict[str, str] = {
    "hydrant": "ueberflur",
    "ueberflurhydrant": "ueberflur",
    "überflurhydrant": "ueberflur",
    "unterflurhydrant": "unterflur",
    "saugstelle": "saugstelle",
    "loeschteich": "loeschteich",
    "löschteich": "loeschteich",
    "loeschwasserbehaelter": "loeschbehaelter",
    "löschwasserbehälter": "loeschbehaelter",
    "loeschwasserbehälter": "loeschbehaelter",
    "brunnen": "brunnen",
    "relaisstation ts": "relais",
    "relaisstation": "relais",
    "pumpenstandort": "relais",
}


def _num(roh: str) -> float | None:
    """Parst eine Zahl mit Dezimalkomma (deutsches CSV-Format)."""
    if roh is None:
        return None
    s = roh.strip().replace(" ", "")
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _mach_import_key(bezeichnung: str, lat: float, lng: float) -> str:
    """Stabiler Schlüssel für idempotenten Import (Bezeichnung + gerundete Koordinate)."""
    roh = f"{bezeichnung.strip().lower()}|{round(lat, 5)}|{round(lng, 5)}"
    return hashlib.sha1(roh.encode("utf-8")).hexdigest()[:32]


def parse_wasserstellen_csv(inhalt: bytes) -> dict:
    """Parst den Vorarlberger Wasserstellen-CSV-Export.

    Rückgabe: {"eintraege": [ {bezeichnung, typ, lat, lng, aktiv, import_key} … ],
               "gesamt": int, "uebersprungen": int, "hinweise": [str, …]}.
    """
    # Encoding: Export ist cp1252/latin-1 (ß/ä kommen als 0xDF/0xE4). utf-8 zuerst
    # versuchen, dann tolerant cp1252.
    try:
        text = inhalt.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = inhalt.decode("cp1252", errors="replace")

    zeilen = text.splitlines()
    eintraege: list[dict] = []
    uebersprungen = 0
    hinweise: list[str] = []
    gesehen: set[str] = set()

    for i, zeile in enumerate(zeilen):
        if not zeile.strip():
            continue
        felder = zeile.split(";")
        # Kopfzeile erkennen (enthält RECHTSWERT/HOCHWERT-Header)
        if i == 0 and ("RECHTSWERT" in zeile.upper() or "OBJECTID" in zeile.upper()):
            continue
        if len(felder) < 15:
            uebersprungen += 1
            continue

        bezeichnung = (felder[0] or "").strip() or "Wasserstelle"
        untergrupp = (felder[8] or "").strip()
        rechtswert = _num(felder[13])
        hochwert = _num(felder[14])

        if rechtswert is None or hochwert is None:
            uebersprungen += 1
            continue

        lat, lng = mgi_zu_wgs84(rechtswert, hochwert)
        if lat is None or lng is None:
            uebersprungen += 1
            continue

        ug_norm = untergrupp.lower().strip()
        aktiv = "geloescht" not in ug_norm and "gelöscht" not in ug_norm
        # "Hydrant geloescht" → als inaktiv importieren (Historie), aber Typ ableiten
        ug_key = ug_norm.replace(" geloescht", "").replace(" gelöscht", "")
        typ = _UNTERGRUPP_MAP.get(ug_key, "sonstige")

        key = _mach_import_key(bezeichnung, lat, lng)
        if key in gesehen:
            uebersprungen += 1
            continue
        gesehen.add(key)

        eintraege.append({
            "bezeichnung": bezeichnung[:250],
            "typ": typ,
            "lat": lat,
            "lng": lng,
            "aktiv": aktiv,
            "import_key": key,
        })

    if uebersprungen:
        hinweise.append(f"{uebersprungen} Zeile(n) ohne gültige Koordinaten übersprungen.")
    return {
        "eintraege": eintraege,
        "gesamt": len(eintraege),
        "uebersprungen": uebersprungen,
        "hinweise": hinweise,
    }


def importiere_eintraege(
    db: Session, org_id: int, eintraege: list[dict], user_id: int | None,
    ersetzen: bool = False,
) -> dict:
    """Legt Wasserstellen aus geparsten Einträgen an / aktualisiert sie (idempotent
    per import_key). `ersetzen=True` löscht zuvor alle bisherigen Import-Datensätze
    dieser Org.

    Rückgabe: {"neu": int, "aktualisiert": int, "gesamt": int}.
    """
    if ersetzen:
        db.query(Wasserstelle).filter(
            Wasserstelle.org_id == org_id,
            Wasserstelle.quelle == "import",
        ).delete(synchronize_session=False)
        db.flush()

    # Bestehende Import-Keys der Org einlesen (nur relevant ohne ersetzen)
    vorhanden: dict[str, Wasserstelle] = {}
    if not ersetzen:
        for w in db.execute(
            select(Wasserstelle).where(
                Wasserstelle.org_id == org_id,
                Wasserstelle.import_key.is_not(None),
            )
        ).scalars():
            if w.import_key:
                vorhanden[w.import_key] = w

    neu = aktualisiert = 0
    for e in eintraege:
        best = vorhanden.get(e["import_key"])
        if best is not None:
            best.bezeichnung = e["bezeichnung"]
            best.typ = e["typ"]
            best.lat = e["lat"]
            best.lng = e["lng"]
            best.aktiv = e["aktiv"]
            best.quelle = "import"
            best.aktualisiert_von_id = user_id
            aktualisiert += 1
        else:
            db.add(Wasserstelle(
                org_id=org_id,
                bezeichnung=e["bezeichnung"],
                typ=e["typ"],
                lat=e["lat"],
                lng=e["lng"],
                aktiv=e["aktiv"],
                quelle="import",
                import_key=e["import_key"],
                erstellt_von_id=user_id,
                aktualisiert_von_id=user_id,
            ))
            neu += 1
    db.flush()
    return {"neu": neu, "aktualisiert": aktualisiert, "gesamt": neu + aktualisiert}


# ── Einsatzinfo-Aufbereitung ────────────────────────────────────────────────────

def lade_wasserstellen_im_umkreis(
    db: Session, org_id: int, ref_lat: float, ref_lng: float, radius_m: int,
) -> list[dict]:
    """Aktive Wasserstellen der Org im Umkreis als Hydranten-Dicts (sortiert nach
    Entfernung). Format kompatibel zu hydrant_service (id, lat, lng, typ, icon_kat,
    ref, entfernung_m, richtung, quelle='stammdaten')."""
    rows = db.query(Wasserstelle).filter(
        Wasserstelle.org_id == org_id,
        Wasserstelle.aktiv.is_(True),
        Wasserstelle.lat.is_not(None),
        Wasserstelle.lng.is_not(None),
    ).all()

    out: list[dict] = []
    for w in rows:
        dist = _haversine_m(ref_lat, ref_lng, w.lat, w.lng)  # type: ignore[arg-type]
        if dist > radius_m:
            continue
        out.append({
            "id": "ws-" + str(w.id),
            "lat": w.lat,
            "lng": w.lng,
            "typ": w.typ,
            "icon_kat": WASSERSTELLE_ICON_KAT.get(w.typ, "loeschwasser"),
            "typ_label": WASSERSTELLE_TYPEN.get(w.typ, w.typ),
            "ref": w.bezeichnung,
            "entfernung_m": int(round(dist)),
            "richtung": _richtung(ref_lat, ref_lng, w.lat, w.lng),  # type: ignore[arg-type]
            "quelle": "stammdaten",
        })
    out.sort(key=lambda h: h["entfernung_m"])
    return out


def dedupe_osm_gegen_stammdaten(
    osm: list[dict], stammdaten: list[dict], schwelle_m: float = 25.0,
) -> list[dict]:
    """Entfernt OSM-Hydranten, die näher als `schwelle_m` an einer eigenen
    Wasserstelle liegen (kein Doppelbild). OSM-Punkte fernab (Nachbarorte, für die
    keine Stammdaten existieren) bleiben erhalten."""
    if not stammdaten:
        return osm
    behalten: list[dict] = []
    for h in osm:
        h_lat, h_lng = h.get("lat"), h.get("lng")
        if h_lat is None or h_lng is None:
            continue
        doppelt = any(
            _haversine_m(h_lat, h_lng, s["lat"], s["lng"]) <= schwelle_m
            for s in stammdaten
        )
        if not doppelt:
            behalten.append(h)
    return behalten
