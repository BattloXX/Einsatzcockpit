"""Elementtypen und Begrenzung der typspezifischen Freitextdaten."""

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class TypDef:
    code: str
    label: str
    icon: str
    felder: tuple[str, ...]
    geometrie: Literal["keine", "punkt", "linie_flaeche", "beliebig"]


ELEMENT_TYPEN: dict[str, TypDef] = {
    "info": TypDef("info", "Information / Hinweis", "ℹ️", ("kategorie", "warnhinweis"), "beliebig"),
    "massnahme": TypDef(
        "massnahme", "Maßnahme / Checkliste", "✅", ("schritte", "erwartetes_ergebnis"), "beliebig",
    ),
    "kontrollstelle": TypDef(
        "kontrollstelle", "Kontroll-/Gefahrenstelle", "📍",
        ("gefahren", "kontrollhinweise", "zugaenglichkeit"), "punkt",
    ),
    "gefahrenbereich": TypDef(
        "gefahrenbereich", "Gefahrenbereich / Kontrollstrecke", "⚠️", ("raeumliche_hinweise",), "linie_flaeche",
    ),
    "ablauf": TypDef("ablauf", "Ablauf / Entscheidungshilfe", "🔀", ("ausloeser", "bedingungen"), "keine"),
    "ressource": TypDef("ressource", "Ressourcen-/Materialbedarf", "📦", ("bedarf", "lagerort"), "beliebig"),
    "medien": TypDef("medien", "Dokument-/Medienverweis", "📄", (), "keine"),
    "zustaendigkeit": TypDef("zustaendigkeit", "Kontakt-/Zuständigkeitsverweis", "👤", ("ausloeser",), "keine"),
    "objektverweis": TypDef("objektverweis", "Objektverweis", "🏢", ("hinweis",), "punkt"),
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

_LISTEN_FELDER = {
    "schritte": ("text", "erledigt_hinweis"),
    "gefahren": None,
    "bedingungen": ("wenn", "dann"),
    "bedarf": ("bezeichnung", "menge", "einheit"),
}


def bereinige_daten(typ: str, daten: dict[str, Any]) -> dict[str, Any]:
    """Unbekannte Felder und ungueltige Werte verwerfen; Freitexte begrenzen."""
    if typ not in ELEMENT_TYPEN:
        raise ValueError(f"Unbekannter Elementtyp: {typ}")
    ergebnis: dict[str, Any] = {}
    for feld in ELEMENT_TYPEN[typ].felder:
        wert = daten.get(feld)
        if feld not in _LISTEN_FELDER:
            if isinstance(wert, str):
                ergebnis[feld] = wert.strip()[:5000]
            continue
        if not isinstance(wert, list):
            continue
        eintraege: list[Any] = []
        schluessel = _LISTEN_FELDER[feld]
        for eintrag in wert[:100]:
            if isinstance(eintrag, str) and feld != "bedarf":
                eintraege.append(eintrag.strip()[:5000])
            elif isinstance(eintrag, dict):
                bereinigt = {
                    key: value.strip()[:5000]
                    for key, value in eintrag.items()
                    if isinstance(key, str) and (schluessel is None or key in schluessel)
                    and isinstance(value, str)
                }
                if bereinigt:
                    eintraege.append(bereinigt)
        ergebnis[feld] = eintraege
    return ergebnis
