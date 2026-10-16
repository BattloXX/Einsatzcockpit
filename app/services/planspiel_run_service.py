"""Entwuerfe und Starts von Einzel-Planspielen.

Die Ereignisausfuehrung gehoert bewusst nicht in diesen Service; er legt nur den
unveraenderlichen Snapshot und die geplanten Ereignisse an.
"""
from __future__ import annotations

import logging
import random
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.data.planspiel_katalog import BAUSTEINE, MELDENDE
from app.models.incident import IncidentLog
from app.models.master import FireDept, OrgSettings
from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, Objekt, ObjektEinsatz
from app.models.planspiel import (
    PlanspielAdresse,
    PlanspielMitglied,
    PlanspielRun,
    PlanspielRunEreignis,
    PlanspielSzenario,
    PlanspielSzenarioVersion,
    PlanspielZiel,
)
from app.services.broadcast import broadcast_org
from app.services.incident_service import create_incident
from app.services.lage_journal_service import journal_eintrag_anlegen
from app.services.major_incident_service import create_lage, lage_einrichten
from app.services.planspiel_katalog_service import (
    VersionSchema,
    aktive_version,
    org_stichwort_code,
    rendern,
    version_als_dict,
)
from app.services.planspiel_ort_service import (
    OrtWahl,
    als_verwendet_markieren,
    gebiet,
    im_gebiet,
    ort_waehlen,
    szenario_zufall,
)

logger = logging.getLogger(__name__)


class ZustandsFehler(ValueError):
    """Der angeforderte Uebergang ist fuer den Run nicht erlaubt."""


@dataclass
class EntwurfDaten:
    titel: str
    art: Literal["einzel", "gsl"]
    erzeugung: Literal["manuell", "teilauto", "zufall"]
    szenario_id: int | None = None
    seed: int | None = None
    ort_modus: Literal["zufall", "pool", "objekt", "manuell", "karte"] = "zufall"
    adresse_id: int | None = None
    objekt_id: int | None = None
    strasse: str | None = None
    hausnr: str | None = None
    plz: str | None = None
    ort: str | None = None
    lat: float | None = None
    lng: float | None = None
    bezeichnung: str | None = None
    stichwort_code: str | None = None
    alarmtext: str | None = None
    titel_szenario: str | None = None
    ereignisse: list[dict[str, Any]] | None = None
    folgemeldungen_aktiv: bool = True
    tempo: float = 1.0
    lernziele: str | None = None
    ortschaften: list[str] | None = None
    leiter_user_id: int | None = None
    gsl_modus: str | None = None
    mittel_s: int = 45
    max_stellen: int = 60
    max_laufzeit_min: int = 120


@dataclass(frozen=True)
class GeplantesEreignis:
    index: int
    baustein_code: str | None
    ereignis_typ: str
    trigger_type: str
    trigger_param: dict[str, Any] | None
    faellig_sim_s: int | None
    status: Literal["geplant", "uebersprungen"]
    sichtbarkeit: str
    ziel: str
    text: str
    max_fires: int


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _ort_dict(ort: OrtWahl, modus: str) -> dict[str, Any]:
    result = asdict(ort)
    result["modus"] = modus
    return result


def _lade_szenario(
    db: Session, org_id: int, daten: EntwurfDaten, seed: int
) -> tuple[PlanspielSzenario, dict[str, Any], int]:
    if daten.szenario_id is None:
        szenario, inhalt = szenario_zufall(
            db, org_id, random.Random(f"{seed}:szenario"), art=daten.art,
            ortschaften=daten.ortschaften, gsl_modus=daten.gsl_modus if daten.art == "gsl" else None,
        )
        version_id = szenario.aktive_version_id
        if version_id is None:
            raise ValueError("Szenario hat keine aktive Version")
        return szenario, inhalt, version_id
    szenario_gewaehlt = db.query(PlanspielSzenario).filter(
        PlanspielSzenario.id == daten.szenario_id,
        PlanspielSzenario.org_id == org_id,
        PlanspielSzenario.aktiv.is_(True),
        PlanspielSzenario.archiviert_at.is_(None),
    ).one_or_none()
    if szenario_gewaehlt is None:
        raise ValueError("Szenario ist nicht aktiv oder nicht in der Organisation vorhanden")
    version = aktive_version(db, szenario_gewaehlt)
    if version is None:
        raise ValueError("Szenario hat keine aktive Version")
    return szenario_gewaehlt, version_als_dict(version, db), version.id


def _inhalt_mit_overrides(inhalt: dict[str, Any], daten: EntwurfDaten) -> dict[str, Any]:
    result = dict(inhalt)
    if daten.titel_szenario is not None:
        result["titel"] = daten.titel_szenario
    if daten.stichwort_code is not None:
        result["stichwort_code"] = daten.stichwort_code
    if daten.alarmtext is not None:
        result["alarmtext_template"] = daten.alarmtext
    if daten.ereignisse is not None:
        result["ereignisse"] = daten.ereignisse
    return VersionSchema.model_validate(result).model_dump()


def _pool_ort(db: Session, org_id: int, adresse_id: int) -> OrtWahl:
    row = db.query(PlanspielAdresse).filter(
        PlanspielAdresse.id == adresse_id,
        PlanspielAdresse.org_id == org_id,
        PlanspielAdresse.aktiv.is_(True),
    ).one_or_none()
    if row is None:
        raise ValueError("Adresse ist nicht aktiv oder nicht in der Organisation vorhanden")
    strassenadresse = " ".join(filter(None, [row.strasse, row.hausnr]))
    ortsadresse = " ".join(filter(None, [row.plz, row.ort]))
    adresse = ", ".join(filter(None, [strassenadresse, ortsadresse]))
    return OrtWahl(row.id, row.objekt_id, row.bezeichnung, row.strasse, row.hausnr, row.plz, row.ort,
                   row.lat, row.lng, row.qualitaet, row.quelle, adresse)


def _ort_waehlen(db: Session, org_id: int, inhalt: dict[str, Any], daten: EntwurfDaten, seed: int) -> OrtWahl:
    if daten.ort_modus == "zufall":
        return ort_waehlen(db, org_id, inhalt, random.Random(f"{seed}:ort"), ortschaften=daten.ortschaften)
    if daten.ort_modus == "pool":
        if daten.adresse_id is None:
            raise ValueError("Pool-Ort braucht eine Adresse")
        return _pool_ort(db, org_id, daten.adresse_id)
    if daten.ort_modus == "objekt":
        if daten.objekt_id is None:
            raise ValueError("Objekt-Ort braucht ein Objekt")
        objekt = db.query(Objekt).filter(Objekt.id == daten.objekt_id, Objekt.org_id == org_id).one_or_none()
        if objekt is None:
            raise ValueError("Objekt ist nicht in der Organisation vorhanden")
        if objekt.lat is None or objekt.lng is None:
            raise ValueError("Objekt hat keine Koordinaten")
        adresse = ", ".join(filter(None, [" ".join(filter(None, [objekt.strasse, objekt.hausnummer])),
                                             " ".join(filter(None, [objekt.plz, objekt.ort]))]))
        return OrtWahl(0, objekt.id, objekt.name, objekt.strasse or "", objekt.hausnummer, objekt.plz,
                       objekt.ort, objekt.lat, objekt.lng, "A", "objekt", adresse)
    if daten.lat is None or daten.lng is None:
        raise ValueError("Manueller Ort braucht Koordinaten")
    if not im_gebiet(gebiet(db, org_id), daten.lat, daten.lng):
        raise ValueError("Ort liegt außerhalb des Planspiel-Gebiets")
    if daten.ort_modus == "manuell" and not daten.strasse:
        raise ValueError("Manueller Ort braucht eine Straße")
    if daten.ort_modus == "karte" and not daten.bezeichnung:
        raise ValueError("Karten-Ort braucht eine Bezeichnung")
    strasse = daten.strasse or ""
    adresse = ", ".join(filter(None, [" ".join(filter(None, [strasse, daten.hausnr])),
                                        " ".join(filter(None, [daten.plz, daten.ort]))]))
    return OrtWahl(0, None, daten.bezeichnung, strasse, daten.hausnr, daten.plz, daten.ort,
                   daten.lat, daten.lng, "C", daten.ort_modus, adresse)


def _variablen(inhalt: dict[str, Any], ort: OrtWahl, seed: int) -> dict[str, str]:
    rng = random.Random(f"{seed}:variablen")
    werte = {
        "adresse": ort.adresse, "strasse": ort.strasse, "hausnr": ort.hausnr or "", "ort": ort.ort or "",
        "objekt": ort.bezeichnung or "Gebäude", "meldender": rng.choice(MELDENDE), "meldezeit": "",
    }
    texte = [inhalt["alarmtext_template"]] + [e["payload_template"] for e in inhalt["ereignisse"]]
    for code in sorted(BAUSTEINE):
        baustein = BAUSTEINE[code]
        if not any("{" + name + "}" in text for text in texte for name in baustein.variablen):
            continue
        for name, choices in baustein.variablen.items():
            if name not in werte and any("{" + name + "}" in text for text in texte):
                werte[name] = rng.choice(choices)
    return werte


def _versionsnummer(db: Session, version_id: int) -> int | None:
    version = db.get(PlanspielSzenarioVersion, version_id)
    return version.version if version else None


def _snapshot(db: Session, org_id: int, daten: EntwurfDaten, seed: int) -> dict[str, Any]:
    szenario, roh, version_id = _lade_szenario(db, org_id, daten, seed)
    inhalt = _inhalt_mit_overrides(roh, daten)
    if daten.art == "gsl" and inhalt["kategorie"] != "gsl":
        raise ValueError("Für eine Großschadenslage ist ein GSL-Szenario (G01–G05 oder eigenes) nötig")
    if daten.art == "gsl":
        area = gebiet(db, org_id)
        if area.polygon is not None:
            point = area.polygon.centroid
            lat, lng = point.y, point.x
        elif area.zentrum is not None:
            lat, lng = area.zentrum
        else:
            raise ValueError("Planspiel-Gebiet ist nicht festgelegt")
        org = db.get(FireDept, org_id)
        name = org.name if org is not None else "Organisation"
        ort = OrtWahl(0, None, None, "", None, None, None, lat, lng, "C", "gebiet", f"{name} Ortsgebiet")
    else:
        ort = _ort_waehlen(db, org_id, inhalt, daten, seed)
    variablen = _variablen(inhalt, ort, seed)
    code = inhalt["stichwort_code"]
    stichwort = org_stichwort_code(db, org_id, code)
    if stichwort is None:
        fallback = "T1" if inhalt["kategorie"] == "technik" else "F1"
        stichwort = org_stichwort_code(db, org_id, fallback)
    if stichwort is None:
        raise ValueError(f"Stichwort {code} ist in der Organisation nicht angelegt")
    result = {
        "code": szenario.code, "szenario_id": szenario.id, "version_id": version_id,
        "version": _versionsnummer(db, version_id), "inhalt": inhalt,
        "ort": _ort_dict(ort, "gebiet" if daten.art == "gsl" else daten.ort_modus),
        "variablen": variablen, "alarmtext": rendern(inhalt["alarmtext_template"], variablen),
        "stichwort_org": stichwort,
    }
    if daten.art == "gsl":
        result["gsl_modus"] = daten.gsl_modus or inhalt.get("gsl_modus") or "hochwasser"
    return result


def _konfig(daten: EntwurfDaten, seed: int) -> dict[str, Any]:
    result = asdict(daten)
    result["seed"] = seed
    return result


def _leiter(db: Session, run: PlanspielRun, user_id: int) -> None:
    member = db.query(PlanspielMitglied).filter(
        PlanspielMitglied.run_id == run.id,
        PlanspielMitglied.org_id == run.org_id,
        PlanspielMitglied.user_id == user_id,
    ).one_or_none()
    if member is None:
        db.add(PlanspielMitglied(org_id=run.org_id, run_id=run.id, user_id=user_id, rolle="leiter"))
    else:
        member.rolle = "leiter"


def entwurf_anlegen(db: Session, org_id: int, user: Any, daten: EntwurfDaten) -> PlanspielRun:
    if not 0.25 <= daten.tempo <= 20:
        raise ValueError("Tempo muss zwischen 0.25 und 20 liegen")
    if daten.art == "gsl" and not 15 <= daten.mittel_s <= 600:
        raise ValueError("Mittelwert muss zwischen 15 und 600 Sekunden liegen")
    if daten.art == "gsl" and not 1 <= daten.max_stellen <= 300:
        raise ValueError("Maximale Stellenanzahl muss zwischen 1 und 300 liegen")
    if daten.art == "gsl" and not 10 <= daten.max_laufzeit_min <= 720:
        raise ValueError("Maximale Laufzeit muss zwischen 10 und 720 Minuten liegen")
    seed = daten.seed if daten.seed is not None else secrets.randbelow(2 ** 31)
    snapshot = _snapshot(db, org_id, daten, seed)
    leiter_id = daten.leiter_user_id or user.id
    run = PlanspielRun(org_id=org_id, titel=daten.titel, art=daten.art, erzeugung=daten.erzeugung,
                       seed=seed, tempo=daten.tempo, folge_aktiv=daten.folgemeldungen_aktiv,
                       konfig=_konfig(daten, seed), szenario_snapshot=snapshot, leiter_user_id=leiter_id,
                       created_by_user_id=user.id, status="entwurf", kanal_freigaben={})
    db.add(run)
    db.flush()
    _leiter(db, run, leiter_id)
    write_audit(db, "planspiel.run.entwurf", org_id=org_id, user_id=user.id,
                entity_type="planspiel_run", entity_id=run.id)
    return run


def entwurf_aktualisieren(db: Session, run: PlanspielRun, user: Any, daten: EntwurfDaten) -> PlanspielRun:
    if run.status not in {"entwurf", "bereit"}:
        raise ZustandsFehler("Entwurf kann in diesem Status nicht geändert werden")
    if not 0.25 <= daten.tempo <= 20:
        raise ValueError("Tempo muss zwischen 0.25 und 20 liegen")
    if daten.art == "gsl" and not (15 <= daten.mittel_s <= 600 and 1 <= daten.max_stellen <= 300
                                     and 10 <= daten.max_laufzeit_min <= 720):
        raise ValueError("Ungültige GSL-Generatorgrenzen")
    if run.org_id is None:
        raise ValueError("Planspiel hat keine Organisation")
    seed = daten.seed if daten.seed is not None else run.seed
    snapshot = _snapshot(db, run.org_id, daten, seed)
    run.titel, run.art, run.erzeugung, run.seed = daten.titel, daten.art, daten.erzeugung, seed
    run.tempo, run.folge_aktiv, run.konfig = daten.tempo, daten.folgemeldungen_aktiv, _konfig(daten, seed)
    run.szenario_snapshot = snapshot
    run.leiter_user_id = daten.leiter_user_id or user.id
    _leiter(db, run, run.leiter_user_id)
    write_audit(db, "planspiel.run.entwurf_geaendert", org_id=run.org_id, user_id=user.id,
                entity_type="planspiel_run", entity_id=run.id)
    return run


def zeitplan(run: PlanspielRun) -> list[GeplantesEreignis]:
    if not run.folge_aktiv:
        return []
    snapshot = run.szenario_snapshot or {}
    return zeitplan_fuer(snapshot.get("inhalt", {}), snapshot.get("variablen", {}), str(run.seed))


def zeitplan_fuer(inhalt: dict[str, Any], werte: dict[str, str], seed_text: str) -> list[GeplantesEreignis]:
    """Zeitplan fuer einen Szenario-Inhalt; seed_text trennt z. B. die einzelnen GSL-Stellen."""
    result = []
    for index, event in enumerate(inhalt.get("ereignisse", [])):
        rng = random.Random(f"{seed_text}:ereignis:{index}")
        status: Literal["geplant", "uebersprungen"] = "geplant"
        if rng.random() >= event.get("wahrscheinlichkeit", 1.0):
            status = "uebersprungen"
        due = None
        if event.get("trigger_type") == "elapsed_time":
            minimum = event.get("offset_min_s") or 0
            maximum = event.get("offset_max_s") if event.get("offset_max_s") is not None else minimum
            due = rng.randint(minimum, maximum)
        result.append(GeplantesEreignis(index, event.get("baustein_code"), event["ereignis_typ"],
            event["trigger_type"], event.get("trigger_param"), due, status, event["sichtbarkeit"], event["ziel"],
            rendern(event["payload_template"], werte), event.get("max_fires", 1)))
    return result


def vorschau(db: Session, run: PlanspielRun) -> dict[str, Any]:
    snapshot = run.szenario_snapshot or {}
    plan = zeitplan(run)
    plan.sort(key=lambda e: (e.faellig_sim_s is None, e.trigger_type != "elapsed_time", e.faellig_sim_s or 0, e.index))
    geplant = sum(e.status == "geplant" for e in plan)
    ueber = sum(e.status == "uebersprungen" for e in plan)
    ort = snapshot.get("ort", {})
    adresse = ort.get("adresse") or ort.get("bezeichnung") or "ohne Adresse"
    kanaele = [name for name, aktiv in (run.kanal_freigaben or {}).items() if aktiv]
    result = {
        "titel": run.titel, "code": snapshot.get("code"), "stichwort_org": snapshot.get("stichwort_org"),
        "alarmtext": snapshot.get("alarmtext"), "ort": ort, "zeitplan": plan,
        "wird_angelegt": [f"1 Übungseinsatz (ÜBUNG) mit Stichwort {snapshot.get('stichwort_org')} an Adresse {adresse}",
                           f"{geplant} geplante Folgemeldungen ({ueber} übersprungen)"],
        "wird_nicht_ausgeloest": ["SMS", "Push", "Teams", "Mail", "Druck", "Nachbarfeuerwehren", "LIS",
                                    "WordPress", "Geocoding", "öffentliche Links",
                                    "Kanal-Freigaben dieses Laufs: " + (", ".join(kanaele) if kanaele else "keine")],
    }
    if run.art == "gsl":
        from app.data.planspiel_katalog import GSL_WELLEN
        modus = snapshot.get("gsl_modus")
        konfig = run.konfig or {}
        mittel = int(konfig.get("mittel_s", 45))
        maximum = int(konfig.get("max_stellen", 60))
        laufzeit = int(konfig.get("max_laufzeit_min", 120))
        result["wird_angelegt"] = [
            "1 Übungs-Großschadenslage (ÜBUNG) – keine externe Alarmierung.",
            f"ca. {round(3600 / mittel)} Einsatzstellen pro Stunde (Mittel {mittel} s), "
            f"max. {maximum} Stellen / {laufzeit} Minuten",
        ]
        if konfig.get("ortschaften"):
            result["wird_angelegt"].append("Ortschaften: " + ", ".join(konfig["ortschaften"]))
        result["wellen"] = [welle.name for welle in GSL_WELLEN.get(str(modus), ())]
    elif (run.konfig or {}).get("ortschaften"):
        result["wird_angelegt"].append("Ortschaften: " + ", ".join((run.konfig or {})["ortschaften"]))
    del db
    return result


def starten(db: Session, run: PlanspielRun, user: Any) -> Any:
    if run.status not in {"entwurf", "bereit"}:
        raise ZustandsFehler("Planspiel kann in diesem Status nicht gestartet werden")
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == run.org_id).one_or_none()
    limit = settings.planspiel_max_laufende_runs if settings else 2
    laufend = db.query(PlanspielRun).filter(PlanspielRun.org_id == run.org_id,
        PlanspielRun.status.in_(("laeuft", "pausiert", "generierung_gestoppt"))).count()
    if laufend >= limit:
        raise ZustandsFehler("Maximale Anzahl laufender Planspiele erreicht")
    snapshot = run.szenario_snapshot or {}
    if run.org_id is None:
        raise ValueError("Planspiel hat keine Organisation")
    if run.art == "gsl":
        lage = create_lage(db, run.org_id, f"ÜBUNG – {run.titel}", trigger="manual", is_exercise=True,
                           started_by_user_id=user.id, description=snapshot["alarmtext"])
        lage.planspiel_run_id = run.id
        lage_einrichten(db, lage, org_id=run.org_id, user_id=user.id)
        journal_eintrag_anlegen(
            db, lage, text=f"[Planspiel] Übungs-Großschadenslage aus Planspiel '{run.titel}' gestartet – "
            "keine externe Alarmierung.", category="info", body_html="", partner_from="", partner_to="",
            measure="", user_id=user.id, author_name="Planspiel",
        )
        for event in zeitplan(run):
            trigger_param = dict(event.trigger_param or {})
            trigger_param.setdefault("ereignis_typ", event.ereignis_typ)
            db.add(PlanspielRunEreignis(
                org_id=run.org_id, run_id=run.id, seq=event.index + 1, quelle="szenario",
                baustein_code=event.baustein_code, ziel_typ="lage", ziel_id=lage.id,
                trigger_type=event.trigger_type, trigger_param=trigger_param, faellig_sim_s=event.faellig_sim_s,
                status=event.status, idempotenz_key=f"run{run.id}:szenario:{event.index}",
                sichtbarkeit=event.sichtbarkeit, text=event.text,
            ))
        run.lage_id, run.gen_aktiv, run.naechste_gen_sim_s, run.gen_seq = lage.id, True, 0.0, 0
        run.max_stellen = int((run.konfig or {}).get("max_stellen", 60))
        run.max_laufzeit_s = int((run.konfig or {}).get("max_laufzeit_min", 120)) * 60
        run.status, run.gestartet_at = "laeuft", _now()
        run.steuer_version += 1
        write_audit(db, "planspiel.run.gestartet", org_id=run.org_id, user_id=user.id,
                    entity_type="planspiel_run", entity_id=run.id, payload={"lage_id": lage.id})
        db.flush()
        return lage
    ort = snapshot["ort"]
    incident, _ = create_incident(db, snapshot["stichwort_org"], is_exercise=True,
        address_street=ort.get("strasse"), address_no=ort.get("hausnr"), address_city=ort.get("ort"),
        lat=ort.get("lat"), lng=ort.get("lng"), report_text=snapshot["alarmtext"],
        reason=snapshot["inhalt"]["titel"], incident_leader_user_id=user.id, primary_org_id=run.org_id,
        reject_near_duplicates=False)
    incident.planspiel_run_id = run.id
    if settings and settings.default_access_pin_hash:
        incident.access_pin_hash = settings.default_access_pin_hash
    log_text = f"[Planspiel] Übungseinsatz aus Planspiel '{run.titel}' erzeugt – "
    log_text += "keine externe Alarmierung."
    db.add(IncidentLog(incident_id=incident.id, author_name="Planspiel", level="info", text=log_text))
    if ort.get("objekt_id"):
        db.add(ObjektEinsatz(org_id=run.org_id, objekt_id=ort["objekt_id"], incident_id=incident.id,
                             quelle="manuell", status=OBJEKT_EINSATZ_BESTAETIGT))
    db.add(PlanspielZiel(org_id=run.org_id, run_id=run.id, seq=1, typ="incident", incident_id=incident.id,
                         szenario_code=snapshot.get("code"), stichwort_code=snapshot["stichwort_org"],
                         adress_snapshot=ort))
    for event in zeitplan(run):
        trigger_param = dict(event.trigger_param or {})
        trigger_param.setdefault("ereignis_typ", event.ereignis_typ)
        db.add(PlanspielRunEreignis(org_id=run.org_id, run_id=run.id, seq=event.index + 1, quelle="szenario",
            baustein_code=event.baustein_code, ziel_typ="incident", ziel_id=incident.id,
            trigger_type=event.trigger_type, trigger_param=trigger_param, faellig_sim_s=event.faellig_sim_s,
            status=event.status, idempotenz_key=f"run{run.id}:szenario:{event.index}",
            sichtbarkeit=event.sichtbarkeit, text=event.text))
    if ort.get("adresse_id"):
        als_verwendet_markieren(db, ort["adresse_id"])
    run.status, run.gestartet_at, run.incident_id = "laeuft", _now(), incident.id
    run.steuer_version += 1
    write_audit(db, "planspiel.run.gestartet", org_id=run.org_id, user_id=user.id,
                entity_type="planspiel_run", entity_id=run.id, payload={"incident_id": incident.id})
    db.flush()
    return incident


async def nach_start_benachrichtigen(run: PlanspielRun, incident: Any) -> None:
    try:
        if run.org_id is None:
            raise ValueError("Planspiel hat keine Organisation")
        if run.art == "gsl":
            await broadcast_org(run.org_id, {"type": "gsl_changed"})
            await broadcast_org(run.org_id, {"type": "planspiel_changed", "run_id": run.id})
            return
        code = incident.alarm_type_code
        await broadcast_org(run.org_id, {"type": "incident_created", "incident_id": incident.id, "alarm": code,
            "alarm_erlaubt": False, "alarm_type_code": code, "is_exercise": True,
            "url": f"/einsatz/{incident.id}/info", "title": f"[ÜBUNG] Neuer Einsatz: {code}"})
        await broadcast_org(run.org_id, {"type": "planspiel_changed", "run_id": run.id})
    except Exception:
        logger.exception("Planspiel-Start-Broadcast fehlgeschlagen")


def entwurf_verwerfen(db: Session, run: PlanspielRun, user: Any) -> None:
    if run.status not in {"entwurf", "bereit"}:
        raise ZustandsFehler("Entwurf kann in diesem Status nicht verworfen werden")
    run.status, run.beendet_at = "abgebrochen", _now()
    write_audit(db, "planspiel.run.entwurf_verworfen", org_id=run.org_id, user_id=user.id,
                entity_type="planspiel_run", entity_id=run.id)
