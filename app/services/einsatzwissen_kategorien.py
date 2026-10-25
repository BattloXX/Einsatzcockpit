"""Fachliche Gliederung bestehender Pläne, unabhängig von ihrer technischen Planart."""

KATEGORIEN = {
    "einsatzplaene": "Einsatzpläne",
    "fuehrungswissen": "Führungswissen",
    "fachwissen": "Fachwissen & Lehrbehelfe",
    "katastrophenschutz": "Katastrophenschutz",
    "sondereinsatzmittel": "Sondereinsatzmittel",
    "nachschlagewerk": "Nachschlagewerk",
}


def kategorie_fuer(planart: str, kategorie: str | None = None) -> str:
    """Alte Arbeitsstände und unveränderte Freigabesnapshots ohne Migration zuordnen."""
    if kategorie in KATEGORIEN:
        return kategorie
    return "katastrophenschutz" if planart == "katastrophenplan" else "einsatzplaene"


def metadaten(daten: dict) -> dict:
    return {
        "kategorie": kategorie_fuer(daten.get("planart", "katastrophenplan"), daten.get("kategorie")),
        "unterkategorie": daten.get("unterkategorie") or "",
        "schlagwoerter": daten.get("schlagwoerter") or [],
        "kurzbeschreibung": daten.get("kurzbeschreibung") or "",
    }
