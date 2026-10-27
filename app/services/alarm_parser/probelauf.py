"""Org-begrenzter Probelauf: nur SELECTs, Parser isoliert mit hartem Zeitlimit."""

import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.models.gateway import AlarmIngest, AlarmParserTestfall

from .katalog import baue_kontext
from .laufzeit import aktiver_regelsatz
from .regelsatz import Regelsatz

logger = logging.getLogger("einsatzleiter.alarm")
ZEITLIMIT_SEKUNDEN = 10


@dataclass
class ProbelaufErgebnis:
    datensaetze: list[dict]
    zusammenfassung: dict[str, int]

    @property
    def freigabefaehig(self) -> bool:
        return self.zusammenfassung["regression"] == 0 and self.zusammenfassung["testfall_fehler"] == 0


def feld_diff(vorher: dict, nachher: dict, pfad: str = "") -> list[dict]:
    """Strukturierter Regeldiff; verschachtelte Alias-/Gewichtsfelder einzeln."""
    result = []
    for key in sorted(vorher.keys() | nachher.keys()):
        if not pfad and key == "version":
            continue
        feld = f"{pfad}.{key}" if pfad else key
        alt, neu = vorher.get(key), nachher.get(key)
        if key in vorher and key in nachher and isinstance(alt, dict) and isinstance(neu, dict):
            result.extend(feld_diff(alt, neu, feld))
        elif key not in vorher or key not in nachher or alt != neu:
            result.append({"feld": feld, "art": "hinzugefuegt" if key not in vorher else
                           "entfernt" if key not in nachher else "geaendert", "vorher": alt, "nachher": neu})
    return result


def probelauf(
    db: Session, org_id: int, regelsatz_entwurf: Regelsatz, *,
    ingest_ids: list[int] | None = None, letzte_n: int = 200,
) -> ProbelaufErgebnis:
    """Vergleicht aktuelle Katalog-Snapshots; schreibt oder verarbeitet niemals Ingests."""
    entwurf = Regelsatz.model_validate(regelsatz_entwurf.model_dump(mode="json"))
    with db.no_autoflush:
        aktiv = aktiver_regelsatz(db, org_id)
        kontext = baue_kontext(db, org_id)
        query = db.query(AlarmIngest).filter(AlarmIngest.org_id == org_id)
        if ingest_ids is not None:
            if len(ingest_ids) > 200:
                raise ValueError("Maximal 200 Rohdatensaetze")
            query = query.filter(AlarmIngest.id.in_(ingest_ids))
        rows = query.order_by(AlarmIngest.received_at.desc(), AlarmIngest.id.desc()).limit(
            max(0, min(letzte_n, 200)),
        ).all()
        tests = db.query(AlarmParserTestfall).filter(
            AlarmParserTestfall.org_id == org_id, AlarmParserTestfall.aktiv.is_(True),
        ).order_by(AlarmParserTestfall.id).all()
        daten = [{"id": row.id, "art": "ingest", "titel": f"Rohdaten {row.id}", "text": row.raw_text or ""}
                 for row in rows]
        daten.extend({"id": test.id, "art": "testfall", "titel": test.titel,
                      "text": test.rohtext, "erwartet": test.erwartet} for test in tests)
    payload = {"kontext": asdict(kontext), "aktiv": aktiv.model_dump(mode="json"),
               "entwurf": entwurf.model_dump(mode="json"), "daten": daten}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "app.services.alarm_parser.probelauf_worker"],
            input=json.dumps(payload), text=True, capture_output=True, check=True, timeout=ZEITLIMIT_SEKUNDEN,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("Parser-Probelauf Zeitlimit erreicht (org %s)", org_id)
        raise ValueError("Probelauf nach 10 Sekunden abgebrochen; keine Freigabe moeglich") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("Parser-Probelauf fehlgeschlagen (org %s, exit %s)", org_id, exc.returncode)
        raise ValueError("Probelauf fehlgeschlagen; keine Freigabe moeglich") from exc
    datensaetze = json.loads(completed.stdout)
    zaehler = {key: sum(row["klassifikation"] == key for row in datensaetze)
               for key in ("regression", "neu_korrekt", "unveraendert")}
    zaehler["testfall_fehler"] = sum(row["besteht"] is False for row in datensaetze)
    return ProbelaufErgebnis(datensaetze, zaehler)
