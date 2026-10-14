"""Fachlogik fuer den versionsierten Planspiel-Szenariokatalog."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session, object_session

from app.data.planspiel_katalog import BAUSTEINE, ORT_TYPEN, SYSTEM_KATALOG_VERSION, SZENARIEN, TEMPLATE_VARIABLEN
from app.models.master import AlarmType
from app.models.planspiel import (
    PlanspielRun,
    PlanspielSzenario,
    PlanspielSzenarioEreignis,
    PlanspielSzenarioVersion,
    PlanspielZiel,
)

PLATZHALTER_RE = re.compile(r"\{([a-z_]+)\}")
_BEDINGUNGS_SCHLUESSEL = {"phase", "auftragsart", "nur_simuliert", "ereignis_ausgefuehrt", "anzahl_offene_stellen_ge"}
_VERSION_FELDER = (
    "titel", "beschreibung", "kategorie", "stichwort_code", "stichwort_quelle", "alarmtext_template",
    "regie_geheimlage", "grundsituation", "ort_typen", "geografie_bedingung", "requires_existing_object",
    "requires_verified_bma", "min_adressqualitaet", "schwierigkeit", "schlagworte", "lernziele",
    "ressourcenhinweis", "ausschlussregeln", "gsl_modus", "gsl_verteilung",
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class KatalogImportErgebnis:
    neu: list[str] = field(default_factory=list)
    aktualisiert: list[str] = field(default_factory=list)
    update_verfuegbar: list[str] = field(default_factory=list)


def unbekannte_platzhalter(text: str) -> set[str]:
    return {name for name in PLATZHALTER_RE.findall(text) if name not in TEMPLATE_VARIABLEN}


def rendern(text: str, werte: dict[str, str]) -> str:
    def ersetzen(match: re.Match[str]) -> str:
        name = match.group(1)
        return werte[name] if name in TEMPLATE_VARIABLEN and name in werte else match.group(0)

    return PLATZHALTER_RE.sub(ersetzen, text)


class EreignisSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baustein_code: str | None = None
    ereignis_typ: Literal["aufklaerung", "eskalation", "komplikation", "entspannung", "lageweit", "funk", "duplikat"]
    trigger_type: Literal[
        "elapsed_time", "phase_reached", "assignment_created", "assignment_completed", "manual", "condition"
    ]
    offset_min_s: int | None = Field(default=None, ge=0)
    offset_max_s: int | None = Field(default=None, ge=0)
    trigger_param: dict[str, Any] | None = None
    wahrscheinlichkeit: float = Field(ge=0, le=1)
    max_fires: int = Field(default=1, ge=1)
    prioritaet: int = 0
    sichtbarkeit: Literal["teilnehmer", "regie"]
    ziel: Literal["stelle", "lage", "einheit", "regie"]
    payload_template: str = Field(max_length=1000)
    voraussetzungen: dict[str, Any] | None = None
    abbruch: dict[str, Any] | None = None
    reihenfolge: int = Field(default=0, ge=0)

    @field_validator("payload_template")
    @classmethod
    def keine_unbekannten_platzhalter(cls, value: str) -> str:
        unbekannt = unbekannte_platzhalter(value)
        if unbekannt:
            raise ValueError(f"unbekannte Platzhalter: {', '.join(sorted(unbekannt))}")
        return value

    @field_validator("trigger_param", "voraussetzungen", "abbruch")
    @classmethod
    def bedingungen_whitelist(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and not set(value).issubset(_BEDINGUNGS_SCHLUESSEL):
            raise ValueError("enthaelt nicht erlaubte Bedingungs-Schluessel")
        return value

    @model_validator(mode="after")
    def offsets_pruefen(self) -> EreignisSchema:
        if self.offset_min_s is not None and self.offset_max_s is not None and self.offset_min_s > self.offset_max_s:
            raise ValueError("offset_min_s darf nicht groesser als offset_max_s sein")
        return self


class VersionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titel: str = Field(min_length=1, max_length=200)
    beschreibung: str | None = None
    kategorie: Literal["brand", "technik", "gsl", "sonstiges"]
    stichwort_code: str = Field(min_length=1, max_length=10)
    stichwort_quelle: str | None = None
    alarmtext_template: str = Field(max_length=2000)
    regie_geheimlage: str | None = None
    grundsituation: str | None = None
    ort_typen: list[str] = Field(min_length=1)
    geografie_bedingung: dict[str, Any] | None = None
    requires_existing_object: bool = False
    requires_verified_bma: bool = False
    min_adressqualitaet: Literal["A", "B", "C"] | None = None
    schwierigkeit: Literal["leicht", "mittel", "schwer"]
    schlagworte: str | None = None
    lernziele: str | None = None
    ressourcenhinweis: str | None = None
    ausschlussregeln: dict[str, Any] | None = None
    gsl_modus: Literal["hochwasser", "sturm", "kombination"] | None = None
    gsl_verteilung: dict[str, Any] | None = None
    ereignisse: list[EreignisSchema] = Field(max_length=20)

    @field_validator("alarmtext_template")
    @classmethod
    def alarmtext_platzhalter(cls, value: str) -> str:
        unbekannt = unbekannte_platzhalter(value)
        if unbekannt:
            raise ValueError(f"unbekannte Platzhalter: {', '.join(sorted(unbekannt))}")
        return value

    @field_validator("ort_typen")
    @classmethod
    def ort_typen_pruefen(cls, value: list[str]) -> list[str]:
        unbekannt = set(value).difference(ORT_TYPEN)
        if unbekannt:
            raise ValueError(f"unbekannte Ortstypen: {', '.join(sorted(unbekannt))}")
        return value


class SzenarioExportSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    aktiv: bool
    zufall_erlaubt: bool
    gewicht: int = Field(ge=0, le=1000)
    version: VersionSchema


class KatalogExportSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["ecp-planspiel-katalog"]
    schema_version: Literal[1]
    szenarien: list[SzenarioExportSchema] = Field(max_length=500)


def system_inhalt(code: str) -> dict[str, Any]:
    szenario = next((item for item in SZENARIEN if item.code == code), None)
    if szenario is None:
        raise ValueError(f"Unbekannter Systemcode: {code}")
    ereignisse: list[dict[str, Any]] = []
    for reihenfolge, baustein_code in enumerate(szenario.bausteine):
        baustein = BAUSTEINE[baustein_code]
        ereignisse.append({
            "baustein_code": baustein.code, "ereignis_typ": baustein.typ,
            "trigger_type": baustein.trigger_type, "offset_min_s": baustein.offset_min_s,
            "offset_max_s": baustein.offset_max_s, "trigger_param": baustein.trigger_param,
            "wahrscheinlichkeit": baustein.wahrscheinlichkeit, "max_fires": 1, "prioritaet": 0,
            "sichtbarkeit": baustein.sichtbarkeit, "ziel": baustein.ziel,
            "payload_template": (szenario.text_overrides or {}).get(baustein.code, baustein.text),
            "voraussetzungen": None, "abbruch": None, "reihenfolge": reihenfolge,
        })
    return {
        "titel": szenario.titel, "beschreibung": None, "kategorie": szenario.kategorie,
        "stichwort_code": szenario.stichwort, "stichwort_quelle": "LFV V03 02/2021",
        "alarmtext_template": szenario.alarmtext_template, "regie_geheimlage": None, "grundsituation": None,
        "ort_typen": list(szenario.ort_typen), "geografie_bedingung": szenario.geografie_bedingung,
        "requires_existing_object": szenario.requires_existing_object,
        "requires_verified_bma": szenario.requires_verified_bma, "min_adressqualitaet": None,
        "schwierigkeit": szenario.schwierigkeit, "schlagworte": None, "lernziele": szenario.lernziele,
        "ressourcenhinweis": None, "ausschlussregeln": None, "gsl_modus": szenario.gsl_modus,
        "gsl_verteilung": None, "ereignisse": ereignisse,
    }


def version_als_dict(version: PlanspielSzenarioVersion, db: Session | None = None) -> dict[str, Any]:
    inhalt = {feld: getattr(version, feld) for feld in _VERSION_FELDER}
    if db is None:
        db = object_session(version)
    if db is None:
        raise ValueError("Die Version ist keiner Datenbank-Session zugeordnet.")
    ereignisse = db.query(PlanspielSzenarioEreignis).filter(
        PlanspielSzenarioEreignis.version_id == version.id,
        PlanspielSzenarioEreignis.org_id == version.org_id,
    ).order_by(PlanspielSzenarioEreignis.reihenfolge, PlanspielSzenarioEreignis.id).all()
    inhalt["ereignisse"] = [{
        "baustein_code": e.baustein_code, "ereignis_typ": e.ereignis_typ, "trigger_type": e.trigger_type,
        "offset_min_s": e.offset_min_s, "offset_max_s": e.offset_max_s, "trigger_param": e.trigger_param,
        "wahrscheinlichkeit": e.wahrscheinlichkeit, "max_fires": e.max_fires, "prioritaet": e.prioritaet,
        "sichtbarkeit": e.sichtbarkeit, "ziel": e.ziel, "payload_template": e.payload_template,
        "voraussetzungen": e.voraussetzungen, "abbruch": e.abbruch, "reihenfolge": e.reihenfolge,
    } for e in ereignisse]
    return inhalt


def _version_anlegen(
    db: Session, szenario: PlanspielSzenario, inhalt: dict[str, Any], user_id: int | None
) -> PlanspielSzenarioVersion:
    nummer = (db.query(func.max(PlanspielSzenarioVersion.version)).filter(
        PlanspielSzenarioVersion.szenario_id == szenario.id,
        PlanspielSzenarioVersion.org_id == szenario.org_id,
    ).scalar() or 0) + 1
    werte = {feld: inhalt.get(feld) for feld in _VERSION_FELDER}
    version = PlanspielSzenarioVersion(org_id=szenario.org_id, szenario_id=szenario.id, version=nummer,
        veroeffentlicht_at=_utcnow(), created_by_user_id=user_id, **werte)
    db.add(version)
    db.flush()
    for ereignis in inhalt["ereignisse"]:
        db.add(PlanspielSzenarioEreignis(org_id=szenario.org_id, version_id=version.id, **ereignis))
    szenario.aktive_version_id = version.id
    return version


def aktive_version(db: Session, szenario: PlanspielSzenario) -> PlanspielSzenarioVersion | None:
    if szenario.aktive_version_id is None:
        return None
    return db.query(PlanspielSzenarioVersion).filter(
        PlanspielSzenarioVersion.id == szenario.aktive_version_id,
        PlanspielSzenarioVersion.szenario_id == szenario.id,
        PlanspielSzenarioVersion.org_id == szenario.org_id,
    ).first()


def katalog_importieren(db: Session, org_id: int, user_id: int | None = None) -> KatalogImportErgebnis:
    ergebnis = KatalogImportErgebnis()
    for daten in SZENARIEN:
        szenario = db.query(PlanspielSzenario).filter(
            PlanspielSzenario.org_id == org_id, PlanspielSzenario.code == daten.code,
        ).first()
        if szenario is None:
            szenario = PlanspielSzenario(org_id=org_id, code=daten.code, herkunft="system", system_code=daten.code,
                system_version=SYSTEM_KATALOG_VERSION, zufall_erlaubt=daten.zufall_erlaubt, aktiv=True,
                created_by_user_id=user_id, updated_by_user_id=user_id)
            db.add(szenario)
            db.flush()
            _version_anlegen(db, szenario, system_inhalt(daten.code), user_id)
            ergebnis.neu.append(daten.code)
        elif szenario.herkunft == "system" and (szenario.system_version or 0) < SYSTEM_KATALOG_VERSION:
            if szenario.org_geaendert:
                ergebnis.update_verfuegbar.append(szenario.code)
            else:
                _version_anlegen(db, szenario, system_inhalt(daten.code), user_id)
                szenario.system_version = SYSTEM_KATALOG_VERSION
                szenario.updated_by_user_id = user_id
                ergebnis.aktualisiert.append(szenario.code)
    return ergebnis


def neue_version(
    db: Session, szenario: PlanspielSzenario, inhalt: dict[str, Any], user_id: int | None
) -> PlanspielSzenarioVersion:
    geprueft = VersionSchema.model_validate(inhalt).model_dump()
    version = _version_anlegen(db, szenario, geprueft, user_id)
    if szenario.herkunft == "system":
        szenario.org_geaendert = True
    szenario.updated_by_user_id = user_id
    return version


def duplizieren(db: Session, szenario: PlanspielSzenario, neuer_code: str, user_id: int | None) -> PlanspielSzenario:
    if db.query(PlanspielSzenario).filter(
        PlanspielSzenario.org_id == szenario.org_id, PlanspielSzenario.code == neuer_code,
    ).first():
        raise ValueError("Dieser Szenario-Code existiert bereits.")
    quelle = aktive_version(db, szenario)
    if quelle is None:
        raise ValueError("Das Szenario hat keine aktive Version.")
    kopie = PlanspielSzenario(org_id=szenario.org_id, code=neuer_code, herkunft="org", aktiv=szenario.aktiv,
        zufall_erlaubt=szenario.zufall_erlaubt, gewicht=szenario.gewicht, created_by_user_id=user_id,
        updated_by_user_id=user_id)
    db.add(kopie)
    db.flush()
    _version_anlegen(db, kopie, version_als_dict(quelle, db), user_id)
    return kopie


def aktiv_setzen(db: Session, szenario: PlanspielSzenario, aktiv: bool) -> None:
    szenario.aktiv = aktiv


def auf_standard_zuruecksetzen(
    db: Session, szenario: PlanspielSzenario, user_id: int | None
) -> PlanspielSzenarioVersion:
    if szenario.herkunft != "system" or not szenario.system_code:
        raise ValueError("Nur System-Szenarien koennen zurueckgesetzt werden.")
    version = _version_anlegen(db, szenario, system_inhalt(szenario.system_code), user_id)
    szenario.org_geaendert = False
    szenario.system_version = SYSTEM_KATALOG_VERSION
    szenario.updated_by_user_id = user_id
    return version


def entfernen(db: Session, szenario: PlanspielSzenario) -> Literal["archiviert", "geloescht"]:
    db.flush()
    benutzt = db.query(PlanspielZiel).filter(
        PlanspielZiel.org_id == szenario.org_id, PlanspielZiel.szenario_code == szenario.code,
    ).first() is not None
    if not benutzt:
        runs = db.query(PlanspielRun).filter(PlanspielRun.org_id == szenario.org_id).all()
        benutzt = any((run.szenario_snapshot or {}).get("code") == szenario.code for run in runs)
    if benutzt:
        szenario.archiviert_at = _utcnow()
        szenario.aktiv = False
        return "archiviert"
    szenario.aktive_version_id = None
    db.flush()
    versionen = db.query(PlanspielSzenarioVersion).filter(
        PlanspielSzenarioVersion.szenario_id == szenario.id, PlanspielSzenarioVersion.org_id == szenario.org_id,
    ).all()
    for version in versionen:
        ereignisse = db.query(PlanspielSzenarioEreignis).filter(
            PlanspielSzenarioEreignis.version_id == version.id, PlanspielSzenarioEreignis.org_id == szenario.org_id,
        ).all()
        for ereignis in ereignisse:
            db.delete(ereignis)
        db.delete(version)
    db.delete(szenario)
    return "geloescht"


def systemdiff(szenario: PlanspielSzenario, db: Session | None = None) -> list[tuple[str, Any, Any]]:
    if db is None:
        db = object_session(szenario)
    if szenario.herkunft != "system" or not szenario.system_code or db is None:
        return []
    version = aktive_version(db, szenario)
    if version is None:
        return []
    alt, neu = version_als_dict(version, db), system_inhalt(szenario.system_code)
    return [
        (feld, alt.get(feld), neu.get(feld))
        for feld in (*_VERSION_FELDER, "ereignisse")
        if alt.get(feld) != neu.get(feld)
    ]


def export_json(db: Session, org_id: int, codes: list[str] | None = None) -> dict[str, Any]:
    query = db.query(PlanspielSzenario).filter(
        PlanspielSzenario.org_id == org_id, PlanspielSzenario.archiviert_at.is_(None),
    )
    if codes is not None:
        query = query.filter(PlanspielSzenario.code.in_(codes))
    szenarien = query.order_by(PlanspielSzenario.code).all()
    export = []
    for szenario in szenarien:
        version = aktive_version(db, szenario)
        if version:
            export.append({"code": szenario.code, "aktiv": szenario.aktiv,
                "zufall_erlaubt": szenario.zufall_erlaubt, "gewicht": szenario.gewicht,
                "version": version_als_dict(version, db)})
    return KatalogExportSchema.model_validate({
        "format": "ecp-planspiel-katalog", "schema_version": 1, "szenarien": export,
    }).model_dump()


def _validierungsfehler(exc: ValidationError) -> ValueError:
    fehler = exc.errors()[0]
    pfad = ".".join(str(part) for part in fehler["loc"])
    return ValueError(f"Ungültiger Katalog bei {pfad}: {fehler['msg']}")


def import_json(db: Session, org_id: int, payload: dict[str, Any], user_id: int | None) -> KatalogImportErgebnis:
    try:
        katalog = KatalogExportSchema.model_validate(payload)
    except ValidationError as exc:
        raise _validierungsfehler(exc) from exc
    ergebnis = KatalogImportErgebnis()
    for eintrag in katalog.szenarien:
        szenario = db.query(PlanspielSzenario).filter(
            PlanspielSzenario.org_id == org_id, PlanspielSzenario.code == eintrag.code,
        ).first()
        inhalt = eintrag.version.model_dump()
        if szenario:
            neue_version(db, szenario, inhalt, user_id)
            szenario.aktiv = eintrag.aktiv
            szenario.zufall_erlaubt = eintrag.zufall_erlaubt
            szenario.gewicht = eintrag.gewicht
            ergebnis.aktualisiert.append(eintrag.code)
        else:
            szenario = PlanspielSzenario(org_id=org_id, code=eintrag.code, herkunft="org", aktiv=eintrag.aktiv,
                zufall_erlaubt=eintrag.zufall_erlaubt, gewicht=eintrag.gewicht, created_by_user_id=user_id,
                updated_by_user_id=user_id)
            db.add(szenario)
            db.flush()
            _version_anlegen(db, szenario, inhalt, user_id)
            ergebnis.neu.append(eintrag.code)
    return ergebnis


def org_stichwort_code(db: Session, org_id: int, code: str) -> str | None:
    alarm = db.query(AlarmType).filter(
        AlarmType.org_id == org_id, func.lower(AlarmType.code) == code.lower(),
    ).first()
    return alarm.code if alarm else None


def szenarien_fuer_stichwort(db: Session, org_id: int, code: str) -> list[PlanspielSzenario]:
    szenarien = db.query(PlanspielSzenario).filter(
        PlanspielSzenario.org_id == org_id,
        PlanspielSzenario.aktiv.is_(True),
        PlanspielSzenario.archiviert_at.is_(None),
    ).all()
    ergebnis = []
    for szenario in szenarien:
        version = aktive_version(db, szenario)
        if version and version.stichwort_code.casefold() == code.casefold():
            ergebnis.append(szenario)
    return sorted(ergebnis, key=lambda item: item.code)


def _tokens(text: str) -> set[str]:
    normal = unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()
    return {wort for wort in re.findall(r"[a-z0-9]+", normal) if len(wort) >= 4}


def stichwort_vorschlag(db: Session, org_id: int, text: str) -> list[tuple[str, int]]:
    gesucht = _tokens(text)
    punkte: dict[str, int] = {}
    szenarien = db.query(PlanspielSzenario).filter(
        PlanspielSzenario.org_id == org_id,
        PlanspielSzenario.aktiv.is_(True),
        PlanspielSzenario.archiviert_at.is_(None),
    ).all()
    for szenario in szenarien:
        version = aktive_version(db, szenario)
        if version is None:
            continue
        suchtext = " ".join(filter(None, [version.titel, version.schlagworte, version.alarmtext_template]))
        treffer = len(gesucht.intersection(_tokens(suchtext)))
        if treffer:
            punkte[version.stichwort_code] = punkte.get(version.stichwort_code, 0) + treffer
    return sorted(punkte.items(), key=lambda item: (-item[1], item[0]))[:5]


def katalog_sicherstellen(db: Session, org_id: int, user_id: int | None = None) -> KatalogImportErgebnis:
    systeme = db.query(PlanspielSzenario).filter(
        PlanspielSzenario.org_id == org_id, PlanspielSzenario.herkunft == "system",
    ).all()
    if not systeme or any((s.system_version or 0) < SYSTEM_KATALOG_VERSION for s in systeme):
        return katalog_importieren(db, org_id, user_id)
    return KatalogImportErgebnis()
