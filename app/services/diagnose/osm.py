"""Lesender Bestandscheck fuer konfigurierte lokale OSM-Quellen."""
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import settings
from app.models.osm import OsmImportLauf
from app.services.diagnose.registry import CheckResult, Finding, register_check
from app.services.osm_gebaeude_import import quellen


@register_check("osm.gebaeude_bestand", "osm", "Lokalen Gebaeudebestand und Importzustaende pruefen", kosten="leicht")
def check(db, budget):
    ergebnis = CheckResult()
    for name, _ in quellen():
        budget.tick()
        laeufe = {}
        for status in ("aktiv", "pruefung", "fehler"):
            zeilen = budget.rows(db, select(OsmImportLauf).where(
                OsmImportLauf.quelle == name, OsmImportLauf.status == status,
            ).order_by(OsmImportLauf.gestartet_am.desc(), OsmImportLauf.id.desc()).limit(1), page_size=1)
            laeufe[status] = zeilen[0][0] if zeilen else None
        aktiv, pruefung, fehler = (laeufe[status] for status in ("aktiv", "pruefung", "fehler"))
        if aktiv is None:
            ergebnis.findings.append(Finding([name, "bestand_fehlt"], "Konfigurierte OSM-Quelle ohne aktiven Bestand",
                "fail", {"quelle": name}, empfehlung="Erstimport ausfuehren: python -m app.cli osm-gebaeude-import "
                f"--quelle {name} --erzwingen"))
        elif aktiv.datenstand is None or (
            datetime.now(UTC).replace(tzinfo=None) - aktiv.datenstand
        ).days > settings.OSM_GEBAEUDE_MAX_ALTER_TAGE:
            ergebnis.findings.append(Finding([name, "bestand_veraltet"],
                "OSM-Bestand veraltet oder Datenstand unbekannt",
                evidenz={"quelle": name}, empfehlung="Bestand aktualisieren: python -m app.cli osm-gebaeude-import "
                f"--quelle {name} --erzwingen"))
        if pruefung:
            ergebnis.findings.append(Finding([name, "pruefung"], "OSM-Import wartet auf Pruefung",
                evidenz={"quelle": name, "lauf_id": pruefung.id}, empfehlung="Lauf sichten: python -m app.cli "
                f"osm-gebaeude-status; nach Pruefung: python -m app.cli osm-gebaeude-aktivieren {pruefung.id}"))
        if fehler and (aktiv is None or fehler.gestartet_am > aktiv.gestartet_am):
            ergebnis.findings.append(Finding([name, "importfehler"], "Juengster OSM-Import fehlgeschlagen",
                evidenz={"quelle": name, "code": fehler.fehler_code},
                empfehlung="Importfehler sichten: python -m app.cli osm-gebaeude-status"))
    return ergebnis
