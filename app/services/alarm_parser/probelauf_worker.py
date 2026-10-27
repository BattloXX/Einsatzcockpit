"""Isolierter, abbrechbarer Parserlauf ohne Datenbankzugriff."""

import json
import sys

from .kontext import GemeindeInfo, ParserKontext
from .parser import parse_alarm
from .regelsatz import Regelsatz


def auswerten(payload: dict) -> list[dict]:
    snapshot = payload["kontext"]
    kontext = ParserKontext(
        gemeinden={key: GemeindeInfo(**value) for key, value in snapshot["gemeinden"].items()},
        zeitzone=snapshot["zeitzone"],
    )
    alt = Regelsatz.model_validate(payload["aktiv"])
    neu = Regelsatz.model_validate(payload["entwurf"])
    ergebnisse = []
    for datensatz in payload["daten"]:
        vorher = parse_alarm(datensatz["text"], kontext, alt).to_dict()
        nachher = parse_alarm(datensatz["text"], kontext, neu).to_dict()
        erwartet = datensatz.get("erwartet")
        bestand = None if erwartet is None else all(vorher.get(k) == v for k, v in erwartet.items())
        besteht = None if erwartet is None else all(nachher.get(k) == v for k, v in erwartet.items())
        rang = {"unklar": 0, "teilweise": 1, "ok": 2}
        schlechter = (rang[nachher["status"]] < rang[vorher["status"]]
                      or nachher["qualitaet"] < vorher["qualitaet"])
        besser = (rang[nachher["status"]] > rang[vorher["status"]]
                  or nachher["qualitaet"] > vorher["qualitaet"])
        if (bestand is True and besteht is False) or schlechter:
            klasse = "regression"
        elif (bestand is False and besteht is True) or besser:
            klasse = "neu_korrekt"
        else:
            klasse = "unveraendert"
        ergebnisse.append({
            "id": datensatz["id"], "art": datensatz["art"], "titel": datensatz["titel"],
            "klassifikation": klasse, "bestand": bestand, "besteht": besteht,
            "diff": [{"feld": key, "vorher": vorher[key], "nachher": nachher[key]}
                     for key in vorher if key != "parser_version" and vorher[key] != nachher[key]],
            "erwartungsfehler": [] if erwartet is None else [
                {"feld": key, "erwartet": value, "tatsaechlich": nachher.get(key)}
                for key, value in erwartet.items() if nachher.get(key) != value
            ],
        })
    return sorted(ergebnisse, key=lambda row: row["klassifikation"] != "regression")


if __name__ == "__main__":
    sys.stdout.write(json.dumps(auswerten(json.load(sys.stdin))))
