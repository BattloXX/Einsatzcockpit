"""Katalogdaten fuer die Druckregel-Verwaltung."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

EINSATZ_TRIGGER = ("einsatz_created", "einsatz_created_gsl", "einsatz_updated")


def _alarmzahl(code: str | None) -> int | None:
    treffer = re.search(r"\d+", code or "")
    return int(treffer.group()) if treffer else None


def _objekt_kurzlabels(elemente: list[str], labels: dict[str, str]) -> str:
    werte = _objekt_labels(elemente, labels)
    sichtbar = werte[:3]
    suffix = f" +{len(werte) - 3}" if len(werte) > 3 else ""
    return ", ".join(sichtbar) + suffix


def _objekt_labels(elemente: list[str], labels: dict[str, str]) -> list[str]:
    werte = []
    for element in elemente:
        label = labels.get(element, element)
        # Die Objekt-Auswahl kennzeichnet einzelne Einträge mit Symbolen; in der
        # kompakten Übersicht wäre das optisch nur Ballast.
        label = re.sub(r"^[^\wÄÖÜäöüß]+\s*", "", label).strip()
        werte.append(label)
    return werte


def _trigger_satz(trigger: str) -> str:
    from app.models.gateway import TRIGGER_LABELS

    saetze = {
        "einsatz_created": "Wenn ein Einsatz angelegt wird",
        "einsatz_created_gsl": "Wenn ein GSL-Einsatz angelegt wird",
        "einsatz_updated": "Wenn ein Einsatz aktualisiert wird",
    }
    return saetze.get(trigger, f"Auslöser: {TRIGGER_LABELS.get(trigger, trigger)}")


def _hinweise(rule) -> list[str]:
    f = rule.filters or {}
    hinweise = []
    if f.get("uebung") == "nur_echt":
        hinweise.append("nur Echteinsätze")
    elif f.get("uebung") == "nur_uebung":
        hinweise.append("nur Übungen")
    if f.get("nur_bma"):
        hinweise.append("nur BMA")
    fenster = f.get("zeitfenster") or {}
    if fenster.get("von") and fenster.get("bis"):
        hinweise.append(f"{fenster['von']}–{fenster['bis']}")
    if f.get("stichwort"):
        hinweise.append("Stichwort: " + ", ".join(f.get("stichwort") or []))
    return hinweise


def regel_beschreibung(
    rule, *, drucker_namen: dict[int, str], objekt_labels: dict[str, str],
    alarm_labels: dict[str, str] | None = None,
) -> str:
    """Formuliert eine Regel so, dass sie ohne Editor-Wissen lesbar bleibt."""
    from app.models.gateway import RULE_DOCUMENT_LABELS
    from app.services.print_dispatcher import effektive_regel_einstellungen

    f = rule.filters or {}
    teile = [_trigger_satz(rule.trigger)]
    alarmstufen = f.get("alarmstufen") or []
    if alarmstufen:
        teile.append("Stufen " + ", ".join(str(code).upper() for code in alarmstufen))
    elif f.get("min_alarmstufe") is not None:
        teile.append(f"ab Stufe {f['min_alarmstufe']}")
    teile.extend(_hinweise(rule))
    documents, elemente, optionen = effektive_regel_einstellungen(rule, {})
    kopien = optionen.get("copies", 1)
    inhalte = [RULE_DOCUMENT_LABELS.get(dokument, dokument) for dokument in documents]
    if elemente:
        inhalte.append("Objekt: " + _objekt_kurzlabels(elemente, objekt_labels))
    if optionen.get("media"):
        inhalte.append(str(optionen["media"]))
    drucker = [drucker_namen[pid] for pid in (rule.printer_ids or []) if pid in drucker_namen]
    ziel = "kein Drucker gewählt (druckt nicht)" if not drucker else ", ".join(drucker)
    satz = " · ".join(teile) + " → "
    satz += f"{kopien}× " + ", ".join(inhalte) if inhalte else "keine Inhalte"
    satz += " → " + ziel
    abweichungen = (rule.options or {}).get("je_alarmstufe") or {}
    if isinstance(abweichungen, dict) and abweichungen:
        satz += " · Abweichungen bei " + ", ".join(str(code).upper() for code in abweichungen)
    return ("(inaktiv) " if not rule.aktiv else "") + satz


def _zellen_kurz(rule, code: str, objekt_labels: dict[str, str]) -> str:
    from app.models.gateway import RULE_DOCUMENT_LABELS
    from app.services.print_dispatcher import effektive_regel_einstellungen

    documents, elemente, optionen = effektive_regel_einstellungen(rule, {"alarm_code": code})
    namen = {"einsatzinfo": "Info", "objektblatt": "Objektblatt", "site_karte": "Karte"}
    teile = [namen.get(dokument, RULE_DOCUMENT_LABELS.get(dokument, dokument)) for dokument in documents]
    if elemente:
        teile.append("Obj: " + _objekt_kurzlabels(elemente, objekt_labels))
    if optionen.get("media"):
        teile.append(str(optionen["media"]))
    return f"{optionen.get('copies', 1)}× " + " · ".join(teile)


def alarmstufen_matrix(db: Session, org_id: int, rules, *, objekt_labels: dict[str, str]) -> dict:
    """Statische Alarmstufen-Sicht; Zusatzfilter werden bewusst nur erläutert."""
    from app.models.gateway import TRIGGER_LABELS

    spalten_regeln = sorted(
        (r for r in rules if r.trigger in EINSATZ_TRIGGER),
        key=lambda r: r.sort_order,
    )
    spalten = [{
        "rule": r, "titel": r.name, "trigger_label": TRIGGER_LABELS.get(r.trigger, r.trigger),
        "hinweise": _hinweise(r), "ohne_drucker": not bool(r.printer_ids), "aktiv": r.aktiv,
    } for r in spalten_regeln]
    zeilen = []
    for alarm in alarmstufen_katalog(db, org_id):
        code = alarm["code"]
        zellen = []
        for rule in spalten_regeln:
            f = rule.filters or {}
            ausgewahlt = {str(x).strip().upper() for x in (f.get("alarmstufen") or [])}
            greift = not ausgewahlt or code in ausgewahlt
            if not ausgewahlt and f.get("min_alarmstufe") is not None:
                zahl = _alarmzahl(code)
                try:
                    greift = zahl is not None and zahl >= int(f["min_alarmstufe"])
                except (TypeError, ValueError):
                    greift = False
            abweichungen = (rule.options or {}).get("je_alarmstufe") or {}
            abweichung = isinstance(abweichungen, dict) and code in {
                str(key).strip().upper() for key in abweichungen
            }
            zellen.append({
                "greift": greift,
                "kurz": _zellen_kurz(rule, code, objekt_labels) if greift else "",
                "abweichung": abweichung,
            })
        zeilen.append({**alarm, "zellen": zellen})
    return {"spalten": spalten, "zeilen": zeilen}


def _nicht_greift_grund(rule, context: dict) -> str | None:
    f = rule.filters or {}
    if not rule.aktiv:
        return "Regel ist deaktiviert"
    alarm_code = context["alarm_code"]
    alarmstufen = {str(x).strip().upper() for x in (f.get("alarmstufen") or [])}
    if alarmstufen and alarm_code not in alarmstufen:
        return f"Alarmstufe {alarm_code} nicht ausgewählt"
    if f.get("min_alarmstufe") is not None:
        try:
            if context["alarmstufe"] is None or context["alarmstufe"] < int(f["min_alarmstufe"]):
                return f"ab Stufe {f['min_alarmstufe']} (alt) nicht erreicht"
        except (TypeError, ValueError):
            return f"ab Stufe {f['min_alarmstufe']} (alt) nicht erreicht"
    if f.get("uebung") == "nur_echt" and context["is_exercise"]:
        return "nur Echteinsätze"
    if f.get("uebung") == "nur_uebung" and not context["is_exercise"]:
        return "nur Übungen"
    if f.get("nur_bma") and not context["nur_bma"]:
        return "nur BMA-Alarme"
    fenster = f.get("zeitfenster") or {}
    von, bis, now = fenster.get("von"), fenster.get("bis"), context["now_hhmm"]
    if von and bis and now and not ((von <= bis and von <= now <= bis) or (von > bis and (now >= von or now <= bis))):
        return f"außerhalb Zeitfenster {von}–{bis}"
    stichworte = f.get("stichwort") or []
    if (stichworte and context.get("stichwort")
            and not any(s.lower() in context["stichwort"].lower() for s in stichworte)):
        return "Stichwort passt nicht"
    if not rule.printer_ids:
        return "kein Drucker gewählt"
    return None


def simuliere(
    db: Session, org_id: int, rules, *, trigger: str, alarm_code: str,
    is_exercise: bool, nur_bma: bool, uhrzeit: str, stichwort: str,
    drucker_namen: dict[int, str], objekt_labels: dict[str, str],
) -> list[dict]:
    """Prüft Regeln rein im Speicher und legt dabei absichtlich keine Jobs an."""
    from app.models.gateway import RULE_DOCUMENT_LABELS
    from app.services.print_dispatcher import _filter_matches, effektive_regel_einstellungen

    code = (alarm_code or "").strip().upper()
    context = {"alarm_code": code, "alarmstufe": _alarmzahl(code), "is_exercise": is_exercise,
               "nur_bma": nur_bma, "now_hhmm": uhrzeit, "stichwort": stichwort or None}
    ergebnis = []
    for rule in rules:
        if rule.trigger != trigger:
            continue
        grund = _nicht_greift_grund(rule, context)
        passt = _filter_matches(rule, context)
        greift = bool(rule.aktiv and passt and rule.printer_ids)
        if not greift and grund is None:
            grund = "Filter passt nicht"
        documents, elemente, optionen = effektive_regel_einstellungen(rule, context)
        kopien = optionen.get("copies", 1)
        drucker = [drucker_namen[pid] for pid in (rule.printer_ids or []) if pid in drucker_namen]
        ergebnis.append({"rule": rule, "greift": greift, "grund": None if greift else grund,
                         "druckt": [{"dokument": RULE_DOCUMENT_LABELS.get(doc, doc), "kopien": kopien,
                                      "format": optionen.get("media") or "Druckervorgabe", "drucker": drucker}
                                     for doc in documents] if greift else [],
                         "objekt_inhalte": _objekt_labels(elemente, objekt_labels) if greift else []})
    return ergebnis


def alarmstufen_katalog(db: Session, org_id: int) -> list[dict]:
    """Alarmstufen einer Organisation in der Reihenfolge F, T, sonstige liefern."""
    from app.models.master import AlarmType

    eintraege: dict[str, dict] = {}
    for alarmtyp in db.query(AlarmType).filter(AlarmType.org_id == org_id).all():
        code = alarmtyp.code.strip().upper()
        if not code or code in eintraege:
            continue
        kategorie = (alarmtyp.category or "").strip().upper()
        gruppe = "F" if code.startswith("F") else "T" if code.startswith("T") else (
            kategorie if kategorie in ("F", "T") else "S"
        )
        eintraege[code] = {"code": code, "label": alarmtyp.label, "gruppe": gruppe}

    def sortierung(eintrag: dict) -> tuple[int, int, str]:
        zahl = re.search(r"\d+", eintrag["code"])
        return ("FTS".index(eintrag["gruppe"]), int(zahl.group()) if zahl else -1, eintrag["code"])

    return sorted(eintraege.values(), key=sortierung)
