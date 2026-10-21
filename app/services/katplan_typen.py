"""Elementtypen und Begrenzung der typspezifischen Freitextdaten."""

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class TypDef:
    code: str
    label: str
    icon: str  # Kompatibles Emoji-Feld fuer bestehende JSON-Kartendaten.
    icon_name: str
    felder: tuple[str, ...]
    geometrie: Literal["keine", "punkt", "linie_flaeche", "beliebig"]


ELEMENT_TYPEN: dict[str, TypDef] = {
    "info": TypDef("info", "Information / Hinweis", "ℹ️", "info-circle", ("kategorie", "warnhinweis"), "beliebig"),
    "massnahme": TypDef(
        "massnahme", "Maßnahme / Checkliste", "✅", "circle-check", ("schritte", "erwartetes_ergebnis"), "beliebig",
    ),
    "kontrollstelle": TypDef(
        "kontrollstelle", "Kontroll-/Gefahrenstelle", "📍", "map-pin",
        ("gefahren", "kontrollhinweise", "zugaenglichkeit"), "punkt",
    ),
    "gefahrenbereich": TypDef(
        "gefahrenbereich", "Gefahrenbereich / Kontrollstrecke", "⚠️", "alert-triangle",
        ("raeumliche_hinweise",), "linie_flaeche",
    ),
    "ablauf": TypDef(
        "ablauf", "Ablauf / Entscheidungshilfe", "🔀", "arrows-shuffle", ("ausloeser", "bedingungen"), "keine",
    ),
    "ressource": TypDef("ressource", "Ressourcen-/Materialbedarf", "📦", "package", ("bedarf", "lagerort"), "beliebig"),
    "medien": TypDef("medien", "Dokument-/Medienverweis", "📄", "file-text", (), "keine"),
    "zustaendigkeit": TypDef("zustaendigkeit", "Kontakt-/Zuständigkeitsverweis", "👤", "user", ("ausloeser",), "keine"),
    "objektverweis": TypDef("objektverweis", "Objektverweis", "🏢", "building", ("hinweis",), "punkt"),
}


@dataclass(frozen=True)
class VorlageDef:
    code: str
    label: str
    typ: str
    titel: str
    beschreibung_html: str
    daten: dict[str, Any]


ELEMENT_VORLAGEN: dict[str, VorlageDef] = {
    "gewaesser_kontrollstelle": VorlageDef(
        "gewaesser_kontrollstelle", "Gewässer-Kontrollstelle", "kontrollstelle", "Gewässer-Kontrollstelle",
        "<p>Gewässer und Einlaufbereiche regelmäßig kontrollieren.</p>",
        {"gefahren": ["Verklausung", "Überflutung"],
         "kontrollhinweise": "Einlauf, Rechen und Schotterfang auf Verlegung prüfen; Wasserstand beobachten."},
    ),
    "verstaendigung": VorlageDef(
        "verstaendigung", "Verständigung", "zustaendigkeit", "Verständigung",
        "<p>Zuständige Stellen gemäß Verständigungsplan informieren.</p>",
        {"ausloeser": "Bei Erreichen der Phase …"},
    ),
    "materialbereitstellung": VorlageDef(
        "materialbereitstellung", "Materialbereitstellung", "ressource", "Materialbereitstellung",
        "<p>Benötigtes Material bereitstellen.</p>",
        {"bedarf": [{"bezeichnung": "Sandsäcke", "menge": "", "einheit": "Stk"}]},
    ),
    "sicherheitshinweis": VorlageDef(
        "sicherheitshinweis", "Sicherheitshinweis", "info", "Sicherheitshinweis",
        "<p>Örtliche Gefahren vor Beginn der Arbeiten beurteilen.</p>",
        {"kategorie": "Sicherheit", "warnhinweis": ""},
    ),
}

# Anzeigenamen der typspezifischen Felder (Druck, PDF, Review).
FELD_LABELS = {
    "kategorie": "Kategorie", "warnhinweis": "Warnhinweis", "schritte": "Schritte",
    "erwartetes_ergebnis": "Erwartetes Ergebnis", "gefahren": "Gefahren", "kontrollhinweise": "Kontrollhinweise",
    "zugaenglichkeit": "Zugänglichkeit", "raeumliche_hinweise": "Räumliche Hinweise", "ausloeser": "Auslöser",
    "bedingungen": "Bedingungen", "bedarf": "Bedarf", "lagerort": "Lagerort", "hinweis": "Hinweis",
}

_LISTEN_FELDER = {
    "schritte": ("text", "erledigt_hinweis"),
    "gefahren": None,
    "bedingungen": ("wenn", "dann"),
    "bedarf": ("bezeichnung", "menge", "einheit"),
}


def bereinige_daten(typ: str, daten: dict[str, Any], verworfen: list[str] | None = None) -> dict[str, Any]:
    """Freitexte begrenzen und jeden Datenverlust optional mit Pfad melden."""
    if typ not in ELEMENT_TYPEN:
        raise ValueError(f"Unbekannter Elementtyp: {typ}")

    def melde(pfad: str, grund: str) -> None:
        if verworfen is not None:
            verworfen.append(f"{pfad}: {grund}")

    def text(wert: Any, pfad: str) -> str | None:
        if isinstance(wert, (int, float)) and not isinstance(wert, bool):
            wert = str(int(wert)) if isinstance(wert, float) and wert.is_integer() else str(wert)
        if not isinstance(wert, str):
            melde(pfad, f"ungueltiger Typ ({type(wert).__name__})")
            return None
        wert = wert.strip()
        if len(wert) > 5000:
            melde(pfad, "auf 5000 Zeichen gekuerzt")
        return wert[:5000]

    ergebnis: dict[str, Any] = {}
    for feld, wert in daten.items():
        pfad = f"daten.{feld}"
        if feld not in ELEMENT_TYPEN[typ].felder:
            melde(pfad, "unbekanntes Feld")
            continue
        if feld not in _LISTEN_FELDER:
            bereinigt_text = text(wert, pfad)
            if bereinigt_text is not None:
                ergebnis[feld] = bereinigt_text
            continue
        if not isinstance(wert, list):
            melde(pfad, f"ungueltiger Typ ({type(wert).__name__})")
            continue
        if len(wert) > 100:
            melde(pfad, "nur 100 Eintraege uebernommen")
        eintraege: list[Any] = []
        schluessel = _LISTEN_FELDER[feld]
        for index, eintrag in enumerate(wert[:100]):
            epfad = f"{pfad}[{index}]"
            if isinstance(eintrag, (str, int, float)) and not isinstance(eintrag, bool) and feld != "bedarf":
                eintraege.append(text(eintrag, epfad))
            elif isinstance(eintrag, dict):
                bereinigt = {}
                for key, value in eintrag.items():
                    if not isinstance(key, str) or (schluessel is not None and key not in schluessel):
                        melde(f"{epfad}.{key}", "unbekanntes Feld")
                        continue
                    t = text(value, f"{epfad}.{key}")
                    if t is not None:
                        bereinigt[key] = t
                if bereinigt:
                    eintraege.append(bereinigt)
                else:
                    melde(epfad, "leerer oder ungueltiger Eintrag")
            else:
                melde(epfad, "leerer oder ungueltiger Eintrag")
        ergebnis[feld] = eintraege
    return ergebnis
