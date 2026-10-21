"""Freigabeworkflow und unveraenderliche, tenantgebundene Snapshots."""

import calendar
import difflib
import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.timezones import now_local
from app.models.katplan import (
    KatplanAbschnitt,
    KatplanAnhang,
    KatplanDokument,
    KatplanElement,
    KatplanElementPhase,
    KatplanLink,
    KatplanPhase,
    KatplanPlan,
    KatplanPruefung,
    KatplanRelation,
    KatplanVersion,
    KatplanVersionElement,
    _utcnow,
)
from app.models.master import FireDept
from app.models.user import User
from app.services.katplan_anhang_service import HASH_FELDER, anhang_snapshot
from app.services.katplan_link_service import link_snapshot
from app.services.katplan_link_service import links_aus_version as links_aus_version
from app.services.katplan_service import _HTMLText, protokolliere


def pruefintervall(plan: KatplanPlan) -> int:
    from app.services.katplan_typen import PLANARTEN

    art = PLANARTEN.get(plan.planart)
    return plan.pruefintervall_monate or (art.pruefintervall_monate if art else 24)


def naechste_pruefung(von: date, monate: int) -> date:
    jahr, monat = divmod(von.year * 12 + von.month - 1 + monate, 12)
    monat += 1
    return date(jahr, monat, min(von.day, calendar.monthrange(jahr, monat)[1]))


def _setze_geprueft(plan: KatplanPlan, user: User, heute: date) -> None:
    plan.zuletzt_geprueft_am = heute
    plan.zuletzt_geprueft_von_id = user.id
    plan.wiedervorlage_am = naechste_pruefung(heute, pruefintervall(plan))
    plan.wiedervorlage_erinnert_am = None
    plan.wiedervorlage_vorab_erinnert_am = None
    plan.wiedervorlage_eskaliert_am = None


def ueberpruefung_bestaetigen(db: Session, plan: KatplanPlan, user: User,
                             kommentar: str | None = None) -> None:
    if not plan.aktuelle_version_id:
        raise ValueError("Nur freigegebene Pläne können als überprüft bestätigt werden.")
    if plan.status != "freigegeben":
        raise ValueError("Der Plan hat unveröffentlichte Änderungen – bitte zuerst freigeben.")
    if kommentar and len(kommentar) > 2000:
        raise ValueError("Der Kommentar darf höchstens 2000 Zeichen enthalten.")
    db.add(KatplanPruefung(org_id=plan.org_id, plan_id=plan.id, aktion="ueberprueft",
                         version_id=plan.aktuelle_version_id, kommentar=kommentar, user_id=user.id))
    protokolliere(db, plan, "plan", "ueberprueft", user, neu=kommentar)
    _setze_geprueft(plan, user, now_local(db.get(FireDept, plan.org_id) if plan.org_id else None).date())
    plan.version += 1
    plan.aktualisiert_am, plan.aktualisiert_von_id = _utcnow(), user.id


def _json(wert: Any) -> str:
    return json.dumps(wert, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _link_snapshot(db: Session, element: KatplanElement) -> list:
    return [link_snapshot(db, link) for link in db.query(KatplanLink).filter_by(
        org_id=element.org_id, plan_id=element.plan_id, element_id=element.id,
    ).order_by(KatplanLink.sort, KatplanLink.id)]


def erstelle_snapshot(db: Session, plan: KatplanPlan, *, flush: bool = True) -> tuple[dict, list[dict]]:
    if flush:
        db.flush()
    kopf = {feld: getattr(plan, feld) for feld in (
        "uuid", "planart", "titel", "beschreibung_html", "geltungsbereich",
        "verantwortliche_stelle", "verantwortlich_kontakt_id")}
    kopf["gefahrenarten"] = plan.gefahrenarten
    kopf["wiedervorlage_am"] = plan.wiedervorlage_am.isoformat() if plan.wiedervorlage_am else None
    for name, modell, felder in (
        ("abschnitte", KatplanAbschnitt, ("id", "parent_id", "titel", "beschreibung_html", "sort")),
        ("phasen", KatplanPhase, ("id", "name", "beschreibung", "ausloeser", "farbe", "sort")),
    ):
        kopf[name] = [{f: getattr(obj, f) for f in felder} for obj in db.query(modell).filter_by(
            org_id=plan.org_id, plan_id=plan.id,
        ).order_by(modell.sort, modell.id).all()]
    kopf["links"] = [link_snapshot(db, link) for link in db.query(KatplanLink).filter_by(
        org_id=plan.org_id, plan_id=plan.id, element_id=None,
    ).order_by(KatplanLink.sort, KatplanLink.id)]
    anhaenge = db.query(KatplanAnhang).filter_by(
        org_id=plan.org_id, plan_id=plan.id, geloescht_am=None,
    ).order_by(KatplanAnhang.sort, KatplanAnhang.uuid).all()
    kopf["anhaenge"] = [{**anhang_snapshot(a), "abschnitt_id": a.abschnitt_id}
                        for a in anhaenge if a.element_id is None]
    kopf["quellen"] = [{**{f: getattr(d, f) for f in (
        "id", "uuid", "titel", "versionsnummer", "seitenzahl", "herausgeber")},
        "stand": d.stand.isoformat() if d.stand else None}
        for d in db.query(KatplanDokument).filter_by(
            org_id=plan.org_id, plan_id=plan.id, ist_aktuelle_version=True, archiviert=False,
        ).order_by(KatplanDokument.id)]
    phasen_ids = [p["id"] for p in kopf["phasen"]]
    elemente = []
    for element in db.query(KatplanElement).filter_by(org_id=plan.org_id, plan_id=plan.id).order_by(
        KatplanElement.sort, KatplanElement.uuid,
    ).all():
        daten = {c.name: getattr(element, c.name) for c in KatplanVersionElement.__table__.columns
                 if c.name not in {"id", "org_id", "version_id", "element_uuid", "phasen_json",
                                   "links_json", "relationen_json", "anhaenge_json", "gsl_json"}}
        daten.update(element_uuid=element.uuid, links_json=_json(_link_snapshot(db, element)),
                     anhaenge_json=_json([anhang_snapshot(a) for a in anhaenge if a.element_id == element.id]),
                     gsl_json=_json({c.name: getattr(element, c.name)
                                                        for c in KatplanElement.__table__.columns
                                                        if c.name.startswith("gsl_")}))
        daten["relationen_json"] = _json([{f: getattr(r, f) for f in ("zu_element_uuid", "art", "hinweis")}
                                          for r in db.query(KatplanRelation).filter_by(
                                              org_id=plan.org_id, plan_id=plan.id, von_element_id=element.id,
                                          ).order_by(KatplanRelation.zu_element_uuid, KatplanRelation.art)])
        daten["phasen_json"] = _json([z.phase_id for z in db.query(KatplanElementPhase).filter(
            KatplanElementPhase.org_id == plan.org_id, KatplanElementPhase.element_id == element.id,
            KatplanElementPhase.phase_id.in_(phasen_ids),
        ).order_by(KatplanElementPhase.phase_id).all()])
        elemente.append(daten)
    from app.services.katplan_akteur_service import katalog_daten, schluessel_lesen

    verwendet = {s for e in elemente for s in schluessel_lesen(e.get("akteure_json"))}
    kopf["akteure"] = katalog_daten(db, plan.org_id, verwendet)
    return kopf, elemente


def _wechsel(db: Session, plan: KatplanPlan, user: User, status: str, aktion: str,
             kommentar: str | None, version_id: int | None = None) -> None:
    alt = plan.status
    plan.status = status
    plan.version += 1
    plan.aktualisiert_am = _utcnow()
    plan.aktualisiert_von_id = user.id
    db.add(KatplanPruefung(org_id=plan.org_id, plan_id=plan.id, aktion=aktion,
                         kommentar=kommentar, user_id=user.id, version_id=version_id))
    protokolliere(db, plan, "plan", aktion, user, feld="status", alt=alt, neu=status)


def einreichen(db: Session, plan: KatplanPlan, user: User, kommentar: str | None = None) -> None:
    if plan.status not in ("entwurf", "in_ueberarbeitung"):
        raise ValueError("Dieser Plan kann in seinem aktuellen Status nicht eingereicht werden.")
    _wechsel(db, plan, user, "in_pruefung", "eingereicht", kommentar)


def zurueckweisen(db: Session, plan: KatplanPlan, user: User, kommentar: str) -> None:
    if plan.status != "in_pruefung":
        raise ValueError("Nur Pläne in Prüfung können zurückgewiesen werden.")
    if not kommentar.strip():
        raise ValueError("Bitte einen Kommentar zur Zurückweisung eingeben.")
    _wechsel(db, plan, user, "in_ueberarbeitung" if plan.aktuelle_version_id else "entwurf",
             "zurueckgewiesen", kommentar.strip())


def freigeben(db: Session, plan: KatplanPlan, user: User,
              aenderungsnotiz: str | None = None) -> KatplanVersion:
    if aenderungsnotiz and len(aenderungsnotiz) > 2000:
        raise ValueError("Die Änderungsnotiz darf höchstens 2000 Zeichen enthalten.")
    if plan.status == "archiviert":
        raise ValueError("Archivierte Pläne können nicht freigegeben werden.")
    kopf, elemente = erstelle_snapshot(db, plan)
    if not elemente:
        raise ValueError("Der Plan muss mindestens ein Element enthalten.")
    # Datumswerte werden fuer den Hash identisch zu ihrer JSON-Darstellung serialisiert.
    hash_daten = json.loads(json.dumps([kopf, elemente], default=lambda d: d.isoformat()))
    # Nur die Verknuepfungsstruktur ist Planinhalt. Live-Stammdaten im historischen
    # Snapshot duerfen allein keine erneute Freigabe ermoeglichen.
    hash_daten[0].pop("wiedervorlage_am", None)
    # Katalogdaten dienen nur der Anzeige; die Zuordnung steckt in akteure_json je Element.
    hash_daten[0].pop("akteure", None)
    hash_daten[0]["links"] = _link_struktur(hash_daten[0].get("links", []))
    hash_daten[0]["anhaenge"] = [{f: a.get(f) for f in HASH_FELDER}
                                 for a in hash_daten[0]["anhaenge"]]
    hash_daten[0]["quellen"] = [{f: q[f] for f in ("id", "versionsnummer", "titel")}
                                for q in hash_daten[0]["quellen"]]
    for daten in hash_daten[1]:
        daten["anhaenge_json"] = _json([{f: a.get(f) for f in HASH_FELDER}
                                       for a in json.loads(daten["anhaenge_json"])])
        daten["links_json"] = _json(_link_struktur(json.loads(daten["links_json"])))
        daten.pop("suchtext", None)  # Abgeleiteter Index enthaelt Live-Anzeigenamen.
    inhalt_hash = hashlib.sha256(_json(hash_daten).encode("utf-8")).hexdigest()
    if plan.aktuelle_version_id:
        aktuell, _, _ = lade_version(db, plan)
        if aktuell.inhalt_hash == inhalt_hash:
            raise ValueError(f"Keine Änderungen seit Version {aktuell.versionsnummer}")
    if plan.status not in ("entwurf", "in_pruefung", "in_ueberarbeitung"):
        raise ValueError("Dieser Plan kann in seinem aktuellen Status nicht freigegeben werden.")
    nummer = db.query(func.max(KatplanVersion.versionsnummer)).filter_by(
        org_id=plan.org_id, plan_id=plan.id,
    ).scalar() or 0
    version = KatplanVersion(org_id=plan.org_id, plan_id=plan.id, versionsnummer=nummer + 1,
                             freigegeben_von_id=user.id, aenderungsnotiz=aenderungsnotiz,
                             plan_json=_json(kopf), inhalt_hash=inhalt_hash, anzahl_elemente=len(elemente))
    db.add(version)
    db.flush()
    for daten in elemente:
        db.add(KatplanVersionElement(org_id=plan.org_id, version_id=version.id, **daten))
    plan.aktuelle_version_id = version.id
    _setze_geprueft(plan, user, now_local(db.get(FireDept, plan.org_id) if plan.org_id else None).date())
    _wechsel(db, plan, user, "freigegeben", "freigegeben", aenderungsnotiz, version.id)
    return version


def lade_version(db: Session, plan: KatplanPlan, version_id: int | None = None
                 ) -> tuple[KatplanVersion, dict, list[KatplanVersionElement]]:
    version = db.query(KatplanVersion).filter_by(
        id=version_id if version_id is not None else plan.aktuelle_version_id,
        org_id=plan.org_id, plan_id=plan.id,
    ).one_or_none()
    if version is None:
        raise ValueError("Version nicht gefunden.")
    elemente = db.query(KatplanVersionElement).filter_by(org_id=plan.org_id, version_id=version.id).order_by(
        KatplanVersionElement.sort, KatplanVersionElement.element_uuid,
    ).all()
    kopf = json.loads(version.plan_json)
    kopf.setdefault("planart", "katastrophenplan")
    kopf.setdefault("akteure", [])
    return version, kopf, elemente


def version_baum(plan_daten: dict, elemente: list[KatplanVersionElement], *,
                 mit_vertraulichen: bool) -> list[dict]:
    abschnitte = sorted(plan_daten.get("abschnitte", []), key=lambda a: (a["sort"], a["id"]))
    knoten = {a["id"]: {"abschnitt": a, "kinder": [], "elemente": []} for a in abschnitte}
    wurzeln = []
    ohne: dict[str, Any] = {"abschnitt": None, "kinder": [], "elemente": []}
    for a in abschnitte:
        parent = a["parent_id"]
        gesehen = {a["id"]}
        while parent in knoten and parent not in gesehen:
            gesehen.add(parent)
            parent = knoten[parent]["abschnitt"]["parent_id"]
        if a["parent_id"] in knoten and parent not in gesehen:
            knoten[a["parent_id"]]["kinder"].append(knoten[a["id"]])
        else:
            wurzeln.append(knoten[a["id"]])
    for element in sorted(elemente, key=lambda e: (e.sort, e.element_uuid)):
        if mit_vertraulichen or not element.vertraulich:
            knoten.get(element.abschnitt_id, ohne)["elemente"].append(element)
    return [*wurzeln, ohne]


def _text(html: str | None) -> str:
    parser = _HTMLText()
    parser.feed(html or "")
    parser.close()
    return " ".join("".join(parser.teile).split())


def _textzeilen(html: str | None) -> list[str]:
    html = re.sub(r"(?i)(</(?:p|li|h3|h4)\s*>|<br\s*/?>)", r"\1\n", html or "")
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    return [zeile for roh in "".join(parser.teile).splitlines() if (zeile := " ".join(roh.split()))]


def _link_struktur(links: list[dict]) -> list[dict]:
    return [{f: link.get(f) for f in ("ziel_typ", "ziel_id", "rolle", "hinweis")} for link in links]


def _aenderungen(alt: dict, neu: dict, verborgene_anhaenge: set[str] | None = None) -> list[dict]:
    ergebnis = []
    for feld in sorted(alt.keys() | neu.keys()):
        a, n = alt.get(feld), neu.get(feld)
        if feld.endswith("_html"):
            a, n = _text(a), _text(n)
        elif feld.endswith("_json"):
            a, n = json.loads(a) if a else None, json.loads(n) if n else None
        if feld in ("links", "links_json"):
            a, n = _link_struktur(a or []), _link_struktur(n or [])
        if feld in ("anhaenge", "anhaenge_json"):
            a, n = ([{f: item.get(f) for f in (*HASH_FELDER, "abschnitt_id")}
                     for item in daten or [] if item["uuid"] not in (verborgene_anhaenge or set())]
                    for daten in (a, n))
        if a != n:
            wert = {"feld": feld, "alt": a, "neu": n}
            if feld in ("links", "links_json", "relationen_json"):
                wert["label"] = "Beziehungen" if feld == "relationen_json" else "Verknüpfungen"
            if feld in ("anhaenge", "anhaenge_json"):
                wert["label"] = "Anhänge"
            if feld == "beschreibung_html":
                # Nur Inhaltszeilen; die technischen Kopfzeilen (---/+++/@@) sind fuer Anwender wertlos.
                wert["diff"] = [z for z in difflib.unified_diff(_textzeilen(alt.get(feld)), _textzeilen(neu.get(feld)),
                                                               fromfile="alt", tofile="neu", lineterm="")
                                if not z.startswith(("---", "+++", "@@"))]
            ergebnis.append(wert)
    return ergebnis


def vergleiche_versionen(alt_elemente: list[KatplanVersionElement], neu_elemente: list[KatplanVersionElement],
                        alt_plan: dict, neu_plan: dict, *, mit_vertraulichen: bool = True) -> dict:
    anhaenge = [*alt_plan.get("anhaenge", []), *neu_plan.get("anhaenge", [])]
    for element in [*alt_elemente, *neu_elemente]:
        anhaenge.extend(json.loads(element.anhaenge_json or "[]"))
    verborgen = {a["uuid"] for a in anhaenge if a.get("vertraulich")} if not mit_vertraulichen else set()
    alt = {e.element_uuid: e for e in alt_elemente}
    neu = {e.element_uuid: e for e in neu_elemente}
    felder = [c.name for c in KatplanVersionElement.__table__.columns
              if c.name not in ("id", "org_id", "version_id", "element_uuid", "suchtext")]
    geaendert = []
    for uuid in sorted(alt.keys() & neu.keys()):
        diff = _aenderungen({f: getattr(alt[uuid], f) for f in felder},
                           {f: getattr(neu[uuid], f) for f in felder}, verborgen)
        if diff:
            geaendert.append({"element_uuid": uuid, "felder": diff})
    kopf = _aenderungen({k: v for k, v in alt_plan.items() if k not in ("abschnitte", "phasen")},
                        {k: v for k, v in neu_plan.items() if k not in ("abschnitte", "phasen")}, verborgen)
    struktur = {}
    for name in ("abschnitte", "phasen"):
        a = {str(v["id"]): v for v in alt_plan.get(name, [])}
        n = {str(v["id"]): v for v in neu_plan.get(name, [])}
        struktur[name] = {"hinzugefuegt": [n[k] for k in sorted(n.keys() - a.keys())],
                          "entfernt": [a[k] for k in sorted(a.keys() - n.keys())],
                          "geaendert": [{"id": k, "felder": diff} for k in sorted(a.keys() & n.keys())
                                        if (diff := _aenderungen(a[k], n[k]))]}
    return {"hinzugefuegt": [neu[k] for k in sorted(neu.keys() - alt.keys())],
            "entfernt": [alt[k] for k in sorted(alt.keys() - neu.keys())],
            "geaendert": geaendert, "kopf": kopf, **struktur}


def pruefe_wiedervorlagen(db: Session) -> list[dict]:
    db.flush()
    treffer = []
    heute_org: dict[int | None, date] = {}
    for plan in db.query(KatplanPlan).filter(
        KatplanPlan.wiedervorlage_am.is_not(None), KatplanPlan.status != "archiviert",
        KatplanPlan.wiedervorlage_eskaliert_am.is_(None),
    ).all():
        if plan.org_id not in heute_org:
            heute_org[plan.org_id] = now_local(db.get(FireDept, plan.org_id) if plan.org_id else None).date()
        heute = heute_org[plan.org_id]
        termin = plan.wiedervorlage_am
        if termin is None:
            continue
        if heute >= termin + timedelta(days=30) and plan.wiedervorlage_eskaliert_am is None:
            art = "eskalation"
            plan.wiedervorlage_eskaliert_am = heute
            plan.wiedervorlage_erinnert_am = plan.wiedervorlage_erinnert_am or heute
        elif heute >= termin and plan.wiedervorlage_erinnert_am is None:
            art = "faellig"
            plan.wiedervorlage_erinnert_am = heute
        elif (heute >= termin - timedelta(days=14) and plan.wiedervorlage_vorab_erinnert_am is None
              and plan.wiedervorlage_erinnert_am is None):
            art = "vorab"
            plan.wiedervorlage_vorab_erinnert_am = heute
        else:
            continue
        treffer.append({"org_id": plan.org_id, "plan_id": plan.id, "titel": plan.titel,
                        "wiedervorlage_am": termin.isoformat(), "art": art,
                        "verantwortlich_user_id": plan.pruef_verantwortlich_user_id})
    return treffer
