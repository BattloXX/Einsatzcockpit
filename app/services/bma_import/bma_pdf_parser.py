"""Parst ein einzelnes BMA-Datenblatt-PDF (Landeswarnzentrale Vorarlberg,
RFL-Aufschaltung, z.B. per E-Mail vom Betreiber erhalten) in dieselbe
{"anlage": {...}, "kontakte": [...]}-Form wie bma_parser.py aus der Live-
Schnittstelle (parse_anlagen()/parse_kontakte()) — so kann ein manuell
hochgeladenes Datenblatt exakt denselben Sync-/Review-Pfad durchlaufen wie ein
per Web-Scrape geholter Datensatz (siehe bma_sync.py::verarbeite_pdf_anlage()).

Reiner Text-Layer-Parser (pypdf) — kein OCR nötig: das Datenblatt ist ein
digital erzeugtes PDF mit vollständigem Textlayer (kein Scan). Trotz
Identity-H-Subset-Font liefert pypdf hier korrekte Unicode-Umlaute (mit einem
echten Datenblatt verifiziert) — anders als bei gescannten Objektdokumenten
(objekt_dokument_service.py) ist daher kein Tesseract-Fallback nötig.

Layout-Beispiel (echtes Datenblatt "BMA 1238", Seite 1 — Straße/PLZ-Ort-Werte
hier zur Lesbarkeit ohne Umlaut-Sonderzeichen):
    BMA 1238
    Boehler Fenster Wolfurt
    1. Angaben zur Brandmeldeanlage
    Standort: Boehler Fenster Wolfurt Anlagedatum: 19.02.2020 10:01:04
    Strasse: Wiesenweg 33 PLZ/Ort: 6922 Wolfurt
    Telefon Beruf: +43 5574 74550 Fax Beruf: +43 5574 74550-20
    EMail Beruf: servicecenter@boehlerfenster.com
    Aufschaltung RFL:Ja - 17.10.2002
    Rechnungsadresse:
    Firma: Boehler Fenster GmbH EMail:
    Zusatz:
    Strasse: Wiesenweg 23 PLZ/Ort: 6922 Wolfurt
    Brandschutzbeauftragte(r)
    Name: ...
    2. Alarmierung Feuerwehr
    Alarmierung der oertlichen Feuerwehr mit Stichwort F14 (Brandmeldeanlagen):
    Feuerwehr: FW - Wolfurt
    3. Verstaendigung
    BMA Alarmperson
    Name: ...

Zeilen tragen bis zu zwei "Label: Wert"-Paare (mal mit, mal ohne Leerzeichen
nach dem Doppelpunkt, je nach PDF-Layout-Engine) — _zeile_in_felder() zerlegt
das generisch anhand der bekannten Label-Liste, ohne feste Spaltenpositionen
anzunehmen.

Nicht abgebildete Abschnitte ("Rechnungsadresse", "2. Alarmierung Feuerwehr"):
werden mitgeparst und im Rückgabewert mitgeführt (landen so unverändert im
BmaImportSatz.rohdaten_json und sind im Review sichtbar), aber NICHT
angewendet — es gibt dafür (noch) keine Zielspalte in ObjektBMA/ObjektKontakt,
und die Rechnungsadresse aus der Live-Schnittstelle (parse_anlagen()) wird von
wende_anlage_auf_objekt_an() ebenfalls nirgends geschrieben (siehe dort) —
dasselbe, bereits bestehende Verhalten wird hier nur gespiegelt, nicht neu
erfunden.
"""
from __future__ import annotations

import io
import re

# Bekannte Feld-Label aus dem Datenblatt (Reihenfolge fuer die Regex-Alternation
# nach Laenge absteigend, damit z.B. "Tel.Mobil Privat:" nicht durch einen
# kuerzeren, zufaellig als Praefix passenden Eintrag verdeckt wird).
_LABELS = sorted([
    "Standort:", "Anlagedatum:", "Straße:", "PLZ/Ort:", "Telefon Beruf:",
    "Fax Beruf:", "EMail Beruf:", "Aufschaltung RFL:", "Firma:", "EMail:",
    "Zusatz:", "Name:", "Telefon Privat:", "Tel.Mobil Beruf:",
    "Tel.Mobil Privat:", "EMail Privat:", "Pager:", "Feuerwehr:",
], key=len, reverse=True)
_LABEL_RE = re.compile("(" + "|".join(re.escape(label) for label in _LABELS) + r")\s*")

_BMA_NUMMER_RE = re.compile(r"^BMA\s+(\S+)", re.IGNORECASE)
_STRASSE_HAUSNR_RE = re.compile(r"^(.*?)\s+(\d+[a-zA-Z]?(?:\s*/\s*\d+[a-zA-Z]?)?)$")
_STICHWORT_RE = re.compile(r"Stichwort\s+(\S+)", re.IGNORECASE)
_RFL_DATUM_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
_AKTUALISIERT_RE = re.compile(r"Datenblatt zuletzt aktualisiert:\s*(\d{2}\.\d{2}\.\d{4})")
_ABSCHNITT_RE = re.compile(r"^\d+\.\s")

# 1:1 aus bma_parser.py::_KONTAKT_FELD_LABELS bzw. ROLLEN_MAPPING übernommen —
# dieselben Label-Texte wie in den HTML-Kontaktkarten, da PDF und HTML-
# Detailseite aus denselben Stammdaten gerendert werden.
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
_ROLLEN_MAPPING = {
    "Brandschutzbeauftragte(r)": "brandschutzbeauftragter",
    "BMA Alarmperson": "bma_alarmperson",
}
_TELEFON_PRAEFIXE = (
    ("telefon_beruf", "Telefon beruflich"),
    ("telefon_privat", "Telefon privat"),
    ("mobil_beruf", "Mobil beruflich"),
    ("mobil_privat", "Mobil privat"),
    ("pager", "Pager"),
)


def _zeile_in_felder(zeile: str) -> dict[str, str]:
    """Zerlegt eine Zeile mit bis zu zwei "Label: Wert"-Paaren in {Label: Wert}.
    Ein Wert reicht bis zum nächsten bekannten Label oder Zeilenende."""
    treffer = list(_LABEL_RE.finditer(zeile))
    felder = {}
    for i, m in enumerate(treffer):
        start = m.end()
        ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(zeile)
        felder[m.group(1)] = zeile[start:ende].strip()
    return felder


def _splitte_strasse(wert: str | None) -> tuple[str | None, str | None]:
    """"Wiesenweg 33" -> ("Wiesenweg", "33"); ohne erkennbare Hausnummer bleibt
    der ganze Text in strasse (z.B. eine Straße ohne Hausnummer)."""
    if not wert:
        return None, None
    m = _STRASSE_HAUSNR_RE.match(wert.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return wert.strip(), None


def _splitte_plz_ort(wert: str | None) -> tuple[str | None, str | None]:
    """"6922 Wolfurt" -> ("6922", "Wolfurt")."""
    if not wert:
        return None, None
    teile = wert.strip().split(None, 1)
    if len(teile) == 2:
        return teile[0], teile[1]
    return None, wert.strip() or None


def _extrahiere_kontaktbloecke(zeilen: list[str]) -> list[dict]:
    """Findet alle Kontakt-Zeilen (siehe parse_datenblatt_text(), das die
    Stammdaten-/Rechnungsadresse-/Feuerwehr-Abschnitte bereits herausgefiltert
    hat) und gruppiert sie je Rollen-Header ("Brandschutzbeauftragte(r)" /
    "BMA Alarmperson") zu einem Block."""
    bloecke: list[dict] = []
    aktuell: dict | None = None
    zaehler = 0
    for zeile in zeilen:
        stripped = zeile.strip()
        if stripped in _ROLLEN_MAPPING:
            if aktuell is not None:
                bloecke.append(aktuell)
            zaehler += 1
            aktuell = {"rolle_quelle": stripped, "art": _ROLLEN_MAPPING[stripped], "_id": zaehler}
            continue
        if aktuell is None:
            continue
        felder = _zeile_in_felder(zeile)
        for label, wert in felder.items():
            feldname = _KONTAKT_FELD_LABELS.get(label)
            if feldname:
                aktuell[feldname] = wert
            # "Straße:"/"PLZ/Ort:" der Kontaktperson (nicht der Anlage) haben
            # kein Zielfeld in ObjektKontakt (siehe app/models/objekt.py) und
            # werden hier bewusst NICHT übernommen — Muster bma_parser.py::
            # _parse_kontakt_karte() ("Adresse wird nicht übernommen").
    if aktuell is not None:
        bloecke.append(aktuell)
    return bloecke


def _baue_kontakt(block: dict) -> dict | None:
    name = (block.get("name") or "").strip()
    if not name:
        return None
    telefone = [
        f"{label}: {block[feld]}" for feld, label in _TELEFON_PRAEFIXE if block.get(feld)
    ]
    return {
        "extern_id": f"pdf:{block['_id']}",
        "rolle_quelle": block["rolle_quelle"],
        "art": block["art"],
        "name": name,
        "telefone": telefone,
        "email": block.get("email_beruf") or block.get("email_privat"),
    }


def parse_datenblatt_text(text: str) -> dict:
    """Kernparser (reine Funktion, kein PDF-Zugriff) — testbar ohne echte
    PDF-Datei. Wirft ValueError, wenn keine BMA-Nummer erkennbar ist (Zeile 1
    muss "BMA <nummer>" sein) — ohne Nummer gibt es keinen stabilen
    extern_id-Schlüssel für BmaImportSatz."""
    zeilen = [z for z in text.splitlines() if z.strip()]
    if not zeilen:
        raise ValueError("Leeres Datenblatt")

    bma_match = _BMA_NUMMER_RE.match(zeilen[0])
    if not bma_match:
        raise ValueError(
            "Keine BMA-Nummer erkannt — erste Zeile muss 'BMA <Nummer>' sein "
            f"(gefunden: {zeilen[0]!r})"
        )
    bma_nummer = bma_match.group(1)
    bezeichnung_kopf = zeilen[1].strip() if len(zeilen) > 1 else None

    stammdaten: dict[str, str] = {}
    rechnungsadresse_felder: dict[str, str] = {}
    feuerwehr_zeilen: list[str] = []
    kontakt_zeilen: list[str] = []
    modus = "stammdaten"

    for zeile in zeilen[2:]:
        stripped = zeile.strip()
        if stripped == "Rechnungsadresse:":
            modus = "rechnungsadresse"
            continue
        if stripped in _ROLLEN_MAPPING:
            modus = "kontakt"
            kontakt_zeilen.append(zeile)
            continue
        if _ABSCHNITT_RE.match(stripped):
            # "1. Angaben zur Brandmeldeanlage" / "3. Verständigung" sind reine
            # Abschnittstitel OHNE eigene Feldzeilen — modus bleibt unveraendert,
            # die naechste echte Grenze (Rollen-Header/"Rechnungsadresse:")
            # entscheidet die Einordnung. NUR "2. Alarmierung Feuerwehr" hat
            # eigene Feldzeilen direkt darunter (Stichwort/Feuerwehr).
            if "Alarmierung" in stripped:
                modus = "feuerwehr"
            continue
        if modus == "stammdaten":
            stammdaten.update(_zeile_in_felder(zeile))
        elif modus == "rechnungsadresse":
            rechnungsadresse_felder.update(_zeile_in_felder(zeile))
        elif modus == "feuerwehr":
            feuerwehr_zeilen.append(zeile)
        elif modus == "kontakt":
            kontakt_zeilen.append(zeile)
        # "ignorieren": Zeilen zwischen "3. Verständigung" und dem ersten
        # Rollen-Header gibt es im echten Layout nicht — werden hier verworfen.

    strasse, hausnummer = _splitte_strasse(stammdaten.get("Straße:"))
    plz, ort = _splitte_plz_ort(stammdaten.get("PLZ/Ort:"))

    aufschaltung = stammdaten.get("Aufschaltung RFL:") or ""
    is_rfl = aufschaltung.strip().lower().startswith("ja")
    rfl_datum_match = _RFL_DATUM_RE.search(aufschaltung) if is_rfl else None

    feuerwehr_text = " ".join(feuerwehr_zeilen)
    stichwort_match = _STICHWORT_RE.search(feuerwehr_text)
    feuerwehr_felder = _zeile_in_felder(feuerwehr_text)

    rechnung_strasse, rechnung_hausnummer = _splitte_strasse(rechnungsadresse_felder.get("Straße:"))
    rechnungsadresse = None
    if rechnungsadresse_felder.get("Firma:"):
        rechnung_plz, rechnung_ort = _splitte_plz_ort(rechnungsadresse_felder.get("PLZ/Ort:"))
        rechnungsadresse = {
            "name": rechnungsadresse_felder.get("Firma:"),
            "email": rechnungsadresse_felder.get("EMail:") or None,
            "strasse": f"{rechnung_strasse or ''} {rechnung_hausnummer or ''}".strip() or None,
            "plz": rechnung_plz,
            "ort": rechnung_ort,
        }

    aktualisiert_match = _AKTUALISIERT_RE.search(text)

    anlage = {
        "extern_id": f"pdf:{bma_nummer}",
        "bma_nummer": bma_nummer,
        "bezeichnung": stammdaten.get("Standort:") or bezeichnung_kopf,
        "strasse": strasse,
        "hausnummer": hausnummer,
        "plz": plz,
        "ort": ort,
        "is_rfl": is_rfl,
        "rfl_aufschaltdatum": rfl_datum_match.group(1) if rfl_datum_match else None,
        "anlagedatum": stammdaten.get("Anlagedatum:"),
        "telefon": stammdaten.get("Telefon Beruf:"),
        "fax": stammdaten.get("Fax Beruf:"),
        "email": stammdaten.get("EMail Beruf:"),
        # Kein Zielfeld in ObjektBMA/ObjektKontakt (siehe Modul-Docstring) —
        # trotzdem mitgeführt, damit die Rohdaten im Review (rohdaten_json)
        # sichtbar bleiben statt stillschweigend verworfen zu werden.
        "feuerwehr_stichwort": stichwort_match.group(1) if stichwort_match else None,
        "feuerwehr": feuerwehr_felder.get("Feuerwehr:"),
        "rechnungsadresse": rechnungsadresse,
        "datenblatt_aktualisiert_am": aktualisiert_match.group(1) if aktualisiert_match else None,
    }

    kontakte = [k for k in (_baue_kontakt(b) for b in _extrahiere_kontaktbloecke(kontakt_zeilen)) if k]

    return {"anlage": anlage, "kontakte": kontakte}


def parse_datenblatt_pdf(data: bytes) -> dict:
    """Liest den Textlayer aller Seiten eines BMA-Datenblatt-PDFs (pypdf) und
    parst ihn via parse_datenblatt_text(). Wirft ValueError bei leerem/nicht
    lesbarem PDF oder fehlender BMA-Nummer."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    text = "\n".join((seite.extract_text() or "") for seite in reader.pages)
    return parse_datenblatt_text(text)
