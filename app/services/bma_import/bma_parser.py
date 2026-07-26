"""BMA-Webplattform (Landeswarnzentrale Vorarlberg): reine Parsing-Funktionen.

Kein Netz, keine DB - siehe app/services/rettungskarten_katalog_service.py::parse_variants
fuer das gleiche Muster (Client holt Rohdaten, hier wird nur normalisiert, damit
die Logik ohne Netzzugriff gegen Fixtures testbar bleibt).

Aus einem echten Mitschnitt der Plattform entnommen (angemeldet als Org-Nutzer,
2026-07-26), NICHT aus einer Dokumentation:
- Die Anlagenliste (POST AlarmSystem/GetAllAlarmSystems) ist ein Kendo-Grid-JSON
  ({"Data": [...], "Total": N}). Feldnamen 1:1 aus der echten Antwort.
- Latitude/Longitude kommen als String mit KOMMA als Dezimaltrennzeichen.
- Datumsfelder sind ISO-Zeitstempel ohne Zeitzone (Serverzeit), teils mit mehr
  als 6 Nachkommastellen (mehr als Python-Mikrosekunden-Praezision).
- Die Kontaktpersonen stehen NUR auf der Detailseite (GET AlarmSystem/Details/{Id},
  server-gerendertes HTML). Je Kontakt ein <div class="card"> mit der Rolle in
  <b class="text-success">, die stabile ID im onclick-Attribut eines Buttons
  ("editContactData(personId, alarmSystemId, kontakttypId)"). Die uebrigen Felder
  sind NICHT in eigenen Tags, sondern bare Text-Labels gefolgt von <b>Wert</b> -
  optionale Felder (Telefon/E-Mail/Pager) werden vom Template komplett weg-
  gelassen, wenn leer; NUR "Zuletzt aktualisiert:" wird auch leer gerendert.
  Deshalb: nur bekannte Feld-Labels werden gepaart, und ein "Wert" wird nur
  uebernommen, wenn er nicht selbst wie ein Label aussieht (endet nicht auf ":").
"""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

# Rollen der Plattform -> objekt_kontakt.art (siehe app/models/objekt.py::KONTAKT_ARTEN
# fuer "brandschutzbeauftragter"; "bma_alarmperson" ist neu und wird von bma_sync.py
# bei Bedarf in objekt_auswahl (typ="kontaktart") angelegt).
ROLLEN_MAPPING = {
    "Brandschutzbeauftragte(r)": "brandschutzbeauftragter",
    "BMA Alarmperson": "bma_alarmperson",
}

# Bekannte Feld-Labels je Kontaktkarte (Text 1:1 aus dem Mitschnitt, inkl. Doppelpunkt).
# "Adresse:" (Privatadresse) und "Zuletzt aktualisiert:" werden bewusst NICHT
# gemappt - Privatadresse ist einsatztaktisch ohne Nutzen, "Zuletzt aktualisiert:"
# wird laut Mitschnitt auch leer gerendert und wuerde bei falscher Paarung die
# naechste Zeile (einen Button-Text) als Wert einfangen.
_KONTAKT_FELD_LABELS = {
    "Name:": "name",
    "Telefon Beruf:": "telefon_beruf",
    "Telefon Privat:": "telefon_privat",
    "Tel.Mobil Beruf:": "mobil_beruf",
    "Tel.Mobil Privat:": "mobil_privat",
    "Pager:": "pager",
    "EMail Beruf:": "email_beruf",
    "EMail Privat:": "email_privat",
}

_TELEFON_PRAEFIXE = (
    ("telefon_beruf", "Telefon beruflich"),
    ("telefon_privat", "Telefon privat"),
    ("mobil_beruf", "Mobil beruflich"),
    ("mobil_privat", "Mobil privat"),
    ("pager", "Pager"),
)

_EDIT_CONTACT_RE = re.compile(r"editContactData\(\s*(-?\d+|null)\s*,\s*(-?\d+|null)\s*,\s*(-?\d+|null)\s*\)")


def _komma_float(wert: object) -> float | None:
    """Konvertiert einen Kendo-Grid-Koordinatenwert (Komma-Dezimaltrennzeichen) zu float."""
    if wert in (None, ""):
        return None
    text = str(wert).strip().replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_datetime(wert: object) -> datetime | None:
    """Parst einen ISO-Zeitstempel ohne Zeitzone; toleriert >6 Nachkommastellen."""
    if not wert:
        return None
    text = str(wert).strip()
    if "." in text:
        kopf, _, bruch = text.partition(".")
        text = f"{kopf}.{bruch[:6]}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _text_oder_none(wert: object, max_len: int | None = None) -> str | None:
    text = str(wert or "").strip()
    if not text:
        return None
    return text[:max_len] if max_len else text


def parse_anlagen(payload: dict | None) -> list[dict]:
    """Normalisiert die Kendo-Grid-Antwort von GetAllAlarmSystems zu Anlagen-Dicts.

    Ueberspringt Datensaetze ohne Id (kann nicht dedupliziert/zugeordnet werden).
    """
    ergebnisse: list[dict] = []
    for roh in (payload or {}).get("Data") or []:
        if not isinstance(roh, dict):
            continue
        extern_id = _text_oder_none(roh.get("Id"), 50)
        if not extern_id:
            continue
        adresse = roh.get("Address") or {}
        zahlungsadresse = roh.get("PaymentAddress") or {}
        ergebnisse.append({
            "extern_id": extern_id,
            "extern_guid": _text_oder_none(roh.get("Guid"), 64),
            "bma_nummer": _text_oder_none(roh.get("BMANR"), 50),
            "bezeichnung": _text_oder_none(roh.get("Bezeichnung"), 200),
            "strasse": _text_oder_none(adresse.get("Strasse"), 200),
            "hausnummer": _text_oder_none(adresse.get("Hausnummer"), 20),
            "plz": _text_oder_none(adresse.get("PLZ"), 10),
            "ort": _text_oder_none(adresse.get("Ort"), 100),
            "lat": _komma_float(adresse.get("Latitude")),
            "lng": _komma_float(adresse.get("Longitude")),
            "is_rfl": bool(roh.get("IsRFL")),
            "is_active": bool(roh.get("IsActive")),
            "anlagedatum": _parse_datetime(roh.get("Anlagedatum")),
            "aufschaltdatum": _parse_datetime(roh.get("Aufschaltdatum")),
            "change_date": _parse_datetime(roh.get("ChangeDate")),
            "esz": _text_oder_none(roh.get("ESZ")),
            "abschnitt": _text_oder_none(roh.get("Abschnitt"), 100),
            "bezirk": _text_oder_none(roh.get("Bezirk"), 100),
            # Rechnungsadresse: nur mitgefuehrt, wenn die Org das Opt-in gesetzt hat
            # (siehe OrgBmaImportConfig.import_rechnungsadresse) - der Sync entscheidet,
            # ob daraus ein Kontakt entsteht.
            "rechnungsadresse": {
                "name": _text_oder_none(zahlungsadresse.get("PaymentName"), 150),
                "email": _text_oder_none(zahlungsadresse.get("PaymentEmail"), 200),
                "strasse": _text_oder_none(zahlungsadresse.get("Strasse"), 200),
                "plz": _text_oder_none(zahlungsadresse.get("PLZ"), 10),
                "ort": _text_oder_none(zahlungsadresse.get("Ort"), 100),
            } if zahlungsadresse else None,
        })
    return ergebnisse


def _parse_kontakt_karte(card) -> dict | None:
    """Parst eine einzelne Kontakt-Karte; None wenn keine Rolle/ID gefunden wird
    (z. B. die allgemeine 'Neue Person hinzufuegen'-Karte ohne Rolle)."""
    rolle_el = card.find("b", class_="text-success")
    if rolle_el is None:
        return None
    rolle = rolle_el.get_text(strip=True)

    edit_link = None
    for a in card.find_all("a", onclick=True):
        m = _EDIT_CONTACT_RE.match(a["onclick"].strip())
        if m:
            edit_link = m
            break
    if edit_link is None:
        return None
    person_id, _alarm_id, kontakttyp_id = edit_link.groups()
    if person_id == "null":
        return None

    zeilen = [z.strip() for z in card.get_text(separator="\n").split("\n") if z.strip()]
    felder: dict[str, str] = {}
    for i, zeile in enumerate(zeilen):
        feld = _KONTAKT_FELD_LABELS.get(zeile)
        if feld is None or i + 1 >= len(zeilen):
            continue
        wert = zeilen[i + 1]
        # Ein "Wert", der selbst wie ein Label aussieht (endet auf ":"), gehoert
        # nicht zu diesem Feld - das Feld wurde vom Template leer gelassen (siehe
        # Moduldoc: nur "Zuletzt aktualisiert:" wird leer gerendert).
        if not wert.endswith(":"):
            felder[feld] = wert

    telefone = [f"{praefix}: {felder[key]}" for key, praefix in _TELEFON_PRAEFIXE if felder.get(key)]
    email = felder.get("email_beruf") or felder.get("email_privat")

    return {
        "extern_id": f"{person_id}:{kontakttyp_id}",
        "person_id": person_id,
        "kontakttyp_id": kontakttyp_id,
        "rolle_quelle": rolle,
        "art": ROLLEN_MAPPING.get(rolle, "sonstig"),
        "name": felder.get("name"),
        "telefone": telefone,
        "email": email,
    }


def parse_kontakte(html: str | None) -> list[dict]:
    """Extrahiert die Kontaktpersonen-Karten der Detailseite (AlarmSystem/Details/{Id}).

    Ueberspringt Kontakte ohne Namen (kann nicht sinnvoll als objekt_kontakt.name
    gespeichert werden) und ohne stabile ID.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    ergebnisse: list[dict] = []
    for card in soup.find_all("div", class_="card"):
        kontakt = _parse_kontakt_karte(card)
        if kontakt and kontakt.get("name"):
            ergebnisse.append(kontakt)
    return ergebnisse
