"""Pflege des lagebezogenen Personals einer Ressource.

Die Funktionen committen bewusst nicht: Router und aufrufende Dienste bilden die
Transaktionsgrenze.  Ausstattung wird in einer späteren Ausbaustufe ergänzt.
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.models.atemschutz_pruefung import AtemschutzGeraet
from app.models.major_incident import (
    AUSSTATTUNG_STATUS,
    GSL_AUSSTATTUNG_KATALOG,
    PERSON_FUNKTIONEN,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageEinheitAusstattung,
    LageEinheitPerson,
    MajorIncident,
)
from app.models.master import Member
from app.models.verleih import VerleihArtikel
from app.services.resource_service import _journal, dispatch_aktiv_filter


def _now() -> datetime:
    return datetime.now(UTC)


def _check_einheit(lage: MajorIncident, einheit: LageEinheit) -> None:
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit gehört nicht zur Lage")


def _audit(db: Session, lage: MajorIncident, einheit: LageEinheit, aktion: str, user_id: int | None) -> None:
    write_audit(
        db,
        "gsl.ressource.personal",
        org_id=lage.org_id,
        user_id=user_id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_id": einheit.id, "aktion": aktion},
    )


def _zahl(wert: Any, feld: str) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int) or wert < 0:
        raise ValueError(f"{feld} muss eine ganze Zahl größer oder gleich 0 sein")
    return wert


def _qualifikationen(wert: str | Iterable[str] | None) -> str | None:
    if wert is None:
        return None
    if isinstance(wert, str):
        text = ",".join(x.strip() for x in wert.split(",") if x.strip())
    else:
        text = ",".join(str(x).strip() for x in wert if str(x).strip())
    if len(text) > 200:
        raise ValueError("Qualifikationen dürfen höchstens 200 Zeichen haben")
    return text or None


def _aktive_personen(db: Session, einheit: LageEinheit) -> list[LageEinheitPerson]:
    # Test- und Router-Sessions dürfen autoflush deaktivieren; die Berechnung
    # muss dennoch die eben angelegte bzw. beendete Zeile sehen.
    db.flush()
    return (
        db.query(LageEinheitPerson)
        .filter(LageEinheitPerson.lage_id == einheit.lage_id, LageEinheitPerson.einheit_id == einheit.id)
        .filter(LageEinheitPerson.bis_at.is_(None))
        .all()
    )


def _neu_berechnen(db: Session, einheit: LageEinheit) -> None:
    """Berechnet die denormalisierten Personalzahlen aus aktiven Listenzeilen."""
    personen = _aktive_personen(db, einheit)
    einheit.staerke_gesamt = len(personen)
    einheit.staerke_fuehrung = sum(p.funktion == "fuehrung" for p in personen)
    einheit.staerke_agt = sum(
        p.funktion == "agt" or "AGT" in {x.strip().upper() for x in (p.qualifikationen or "").split(",")}
        for p in personen
    )
    einheit.staerke_sanitaeter = sum(
        p.funktion == "sanitaeter" or "SAN" in {x.strip().upper() for x in (p.qualifikationen or "").split(",")}
        for p in personen
    )


def _zahlen(einheit: LageEinheit) -> dict[str, int]:
    return {
        "gesamt": einheit.staerke_gesamt or 0,
        "fuehrung": einheit.staerke_fuehrung or 0,
        "agt": einheit.staerke_agt or 0,
        "sanitaeter": einheit.staerke_sanitaeter or 0,
    }


def _journal_personal(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    text: str,
    user_id: int | None,
    author_name: str | None,
    quelle: str,
) -> None:
    _journal(
        db,
        lage.id,
        text,
        author_name=author_name,
        user_id=user_id,
        einheit_id=einheit.id,
        ereignis_typ="personal",
        quelle=quelle,
    )


def personal_setzen(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    gesamt: int,
    fuehrung: int | None = None,
    agt: int | None = None,
    sanitaeter: int | None = None,
    bemerkung: str | None = None,
    user_id: int | None,
    author_name: str | None,
    quelle: str = "manuell",
) -> dict[str, dict[str, int] | str | None]:
    _check_einheit(lage, einheit)
    if einheit.personal_modus != "summe":
        raise ValueError("Mannschaft wird aus der Personenliste berechnet")
    neu = {
        "gesamt": _zahl(gesamt, "Gesamtstärke"),
        "fuehrung": _zahl(fuehrung or 0, "Führung"),
        "agt": _zahl(agt or 0, "AGT"),
        "sanitaeter": _zahl(sanitaeter or 0, "Sanitäter"),
    }
    if any(neu[k] > neu["gesamt"] for k in ("fuehrung", "agt", "sanitaeter")):
        raise ValueError("Teilsummen dürfen die Gesamtstärke nicht überschreiten")
    if bemerkung is not None and len(bemerkung) > 500:
        raise ValueError("Bemerkung darf höchstens 500 Zeichen haben")
    alt = _zahlen(einheit)
    alt_bemerkung = einheit.personal_bemerkung
    if alt == neu and alt_bemerkung == bemerkung:
        return {"alt": alt, "neu": neu, "bemerkung": bemerkung}
    einheit.staerke_gesamt, einheit.staerke_fuehrung = neu["gesamt"], neu["fuehrung"]
    einheit.staerke_agt, einheit.staerke_sanitaeter = neu["agt"], neu["sanitaeter"]
    einheit.personal_bemerkung = bemerkung
    _journal_personal(
        db,
        lage,
        einheit,
        f"Mannschaft {alt['gesamt']} -> {neu['gesamt']} (Führung {neu['fuehrung']}, AGT {neu['agt']}, San {neu['sanitaeter']})",
        user_id,
        author_name,
        quelle,
    )
    _audit(db, lage, einheit, "setzen", user_id)
    return {"alt": alt, "neu": neu, "bemerkung": bemerkung}


def modus_wechseln(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    modus: str,
    *,
    user_id: int | None,
    author_name: str | None,
    quelle: str = "manuell",
) -> None:
    _check_einheit(lage, einheit)
    if modus not in {"summe", "liste"}:
        raise ValueError("Ungültiger Personalmodus")
    if einheit.personal_modus == modus:
        return
    alt = einheit.personal_modus
    einheit.personal_modus = modus
    if modus == "liste":
        _neu_berechnen(db, einheit)
    _journal_personal(db, lage, einheit, f"Personalmodus: {alt} -> {modus}", user_id, author_name, quelle)
    _audit(db, lage, einheit, "modus_wechseln", user_id)


def _mitglied(db: Session, lage: MajorIncident, member_id: int) -> Member:
    member = db.get(Member, member_id)
    if not member or member.org_id != lage.org_id or not member.active:
        raise ValueError("Mitglied gehört nicht zur Organisation der Lage oder ist nicht aktiv")
    return member


def _person_anlegen(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    member_id: int | None,
    name: str | None,
    funktion: str,
    qualifikationen: str | Iterable[str] | None,
    herkunft: str,
    user_id: int | None,
    abloesung_von_id: int | None = None,
    umbuchung_id: str | None = None,
) -> LageEinheitPerson:
    if funktion not in PERSON_FUNKTIONEN:
        raise ValueError("Ungültige Funktion")
    if herkunft not in {"frei", "stamm", "verstaerkung", "umbuchung"}:
        raise ValueError("Ungültige Herkunft")
    member: Member | None = None
    if member_id is not None:
        member = _mitglied(db, lage, member_id)
        name = member.full_name
        herkunft = "stamm" if herkunft == "frei" else herkunft
        if qualifikationen is None:
            qualifikationen = [mq.qualification.code for mq in member.qualifications if mq.qualification]
    if not name or not name.strip() or len(name.strip()) > 120:
        raise ValueError("Name ist erforderlich und darf höchstens 120 Zeichen haben")
    if member_id is not None:
        # Bei Ablösung/Umbuchung wurde die alte aktive Zeile eben beendet.
        db.flush()
        vorhanden = (
            db.query(LageEinheitPerson, LageEinheit.label)
            .join(LageEinheit, LageEinheit.id == LageEinheitPerson.einheit_id)
            .filter(
                LageEinheitPerson.lage_id == lage.id,
                LageEinheitPerson.member_id == member_id,
                LageEinheitPerson.bis_at.is_(None),
            )
            .first()
        )
        if vorhanden:
            raise ValueError(f"Mitglied ist bereits bei Einheit {vorhanden[1]} aktiv")
    person = LageEinheitPerson(
        lage_id=lage.id,
        einheit_id=einheit.id,
        member_id=member_id,
        name=name.strip(),
        funktion=funktion,
        qualifikationen=_qualifikationen(qualifikationen),
        von_at=_now(),
        herkunft=herkunft,
        abloesung_von_id=abloesung_von_id,
        umbuchung_id=umbuchung_id,
        aktiv_key=member_id,
        created_by=user_id,
    )
    if member_id is None:
        db.add(person)
        return person
    try:
        with db.begin_nested():
            db.add(person)
            db.flush()
    except IntegrityError as exc:
        vorhanden = (
            db.query(LageEinheit.label)
            .join(LageEinheitPerson, LageEinheit.id == LageEinheitPerson.einheit_id)
            .filter(
                LageEinheitPerson.lage_id == lage.id,
                LageEinheitPerson.member_id == member_id,
                LageEinheitPerson.bis_at.is_(None),
            )
            .scalar()
        )
        raise ValueError(f"Mitglied ist bereits bei Einheit {vorhanden or 'einer anderen Einheit'} aktiv") from exc
    return person


def person_hinzufuegen(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    member_id: int | None = None,
    name: str | None = None,
    funktion: str,
    qualifikationen: str | Iterable[str] | None = None,
    herkunft: str = "frei",
    bemerkung: str | None = None,
    user_id: int | None,
    author_name: str | None,
) -> LageEinheitPerson:
    _check_einheit(lage, einheit)
    if einheit.personal_modus != "liste":
        raise ValueError("Personen können nur im Listenmodus hinzugefügt werden")
    if herkunft not in {"frei", "stamm", "verstaerkung"}:
        raise ValueError("Ungültige Herkunft")
    if bemerkung is not None and len(bemerkung) > 300:
        raise ValueError("Bemerkung darf höchstens 300 Zeichen haben")
    person = _person_anlegen(
        db,
        lage,
        einheit,
        member_id=member_id,
        name=name,
        funktion=funktion,
        qualifikationen=qualifikationen,
        herkunft=herkunft,
        user_id=user_id,
    )
    person.bemerkung = bemerkung
    _neu_berechnen(db, einheit)
    _journal_personal(
        db,
        lage,
        einheit,
        f"Personal hinzugefügt: {person.name} ({PERSON_FUNKTIONEN[person.funktion]})",
        user_id,
        author_name,
        "manuell",
    )
    _audit(db, lage, einheit, "hinzufuegen", user_id)
    return person


def person_entfernen(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    person_id: int,
    *,
    grund: str | None = None,
    user_id: int | None,
    author_name: str | None,
) -> None:
    _check_einheit(lage, einheit)
    person = db.get(LageEinheitPerson, person_id)
    if not person or person.lage_id != lage.id or person.einheit_id != einheit.id or person.bis_at is not None:
        raise ValueError("Person gehört nicht zur Einheit und Lage")
    person.bis_at, person.aktiv_key = _now(), None
    _neu_berechnen(db, einheit)
    suffix = f" ({grund})" if grund else ""
    _journal_personal(db, lage, einheit, f"Personal entfernt: {person.name}{suffix}", user_id, author_name, "manuell")
    _audit(db, lage, einheit, "entfernen", user_id)


def verstaerken(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    anzahl: int | None = None,
    personen: list[dict[str, Any]] | None = None,
    user_id: int | None,
    author_name: str | None,
) -> None:
    _check_einheit(lage, einheit)
    if einheit.personal_modus == "summe":
        delta = _zahl(anzahl, "Anzahl") if anzahl is not None else 0
        if delta <= 0:
            raise ValueError("Anzahl muss größer als 0 sein")
        alt = einheit.staerke_gesamt or 0
        einheit.staerke_gesamt = alt + delta
        _journal_personal(
            db,
            lage,
            einheit,
            f"Verstärkung +{delta} ({alt} -> {einheit.staerke_gesamt})",
            user_id,
            author_name,
            "manuell",
        )
    elif einheit.personal_modus == "liste":
        if not personen:
            raise ValueError("Personen sind erforderlich")
        for daten in personen:
            _person_anlegen(
                db,
                lage,
                einheit,
                member_id=daten.get("member_id"),
                name=daten.get("name"),
                funktion=daten.get("funktion", ""),
                qualifikationen=daten.get("qualifikationen"),
                herkunft="verstaerkung",
                user_id=user_id,
            )
        _neu_berechnen(db, einheit)
        _journal_personal(
            db, lage, einheit, f"Verstärkung: {len(personen)} Person(en)", user_id, author_name, "manuell"
        )
    else:
        raise ValueError("Ungültiger Personalmodus")
    _audit(db, lage, einheit, "verstaerken", user_id)


def abloesen(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    person_alt_id: int,
    *,
    member_id: int | None = None,
    name: str | None = None,
    funktion: str | None = None,
    user_id: int | None,
    author_name: str | None,
) -> LageEinheitPerson:
    _check_einheit(lage, einheit)
    if einheit.personal_modus != "liste":
        raise ValueError("Ablösung ist nur im Listenmodus möglich")
    alt = db.get(LageEinheitPerson, person_alt_id)
    if not alt or alt.lage_id != lage.id or alt.einheit_id != einheit.id or alt.bis_at is not None:
        raise ValueError("Person gehört nicht zur Einheit und Lage")
    alt.bis_at, alt.aktiv_key = _now(), None
    neu = _person_anlegen(
        db,
        lage,
        einheit,
        member_id=member_id,
        name=name,
        funktion=funktion or alt.funktion,
        qualifikationen=alt.qualifikationen,
        herkunft=alt.herkunft,
        user_id=user_id,
        abloesung_von_id=alt.id,
    )
    _neu_berechnen(db, einheit)
    _journal_personal(db, lage, einheit, f"Ablösung: {alt.name} -> {neu.name}", user_id, author_name, "manuell")
    _audit(db, lage, einheit, "abloesen", user_id)
    return neu


def umbuchen(
    db: Session,
    lage: MajorIncident,
    von_einheit: LageEinheit,
    nach_einheit: LageEinheit,
    *,
    anzahl: int | None = None,
    person_ids: list[int] | None = None,
    user_id: int | None,
    author_name: str | None,
) -> None:
    """Bucht Personal beider Einheiten atomar innerhalb der Aufrufertransaktion."""
    with db.begin_nested():
        _umbuchen(
            db,
            lage,
            von_einheit,
            nach_einheit,
            anzahl=anzahl,
            person_ids=person_ids,
            user_id=user_id,
            author_name=author_name,
        )


def _umbuchen(
    db: Session,
    lage: MajorIncident,
    von_einheit: LageEinheit,
    nach_einheit: LageEinheit,
    *,
    anzahl: int | None = None,
    person_ids: list[int] | None = None,
    user_id: int | None,
    author_name: str | None,
) -> None:
    _check_einheit(lage, von_einheit)
    _check_einheit(lage, nach_einheit)
    if von_einheit.id == nach_einheit.id:
        raise ValueError("Quelle und Ziel müssen verschieden sein")
    gesperrt = (
        db.query(LageEinheit)
        .filter(LageEinheit.id.in_(sorted((von_einheit.id, nach_einheit.id))))
        .order_by(LageEinheit.id)
        .with_for_update()
        .all()
    )
    by_id = {e.id: e for e in gesperrt}
    von, nach = by_id[von_einheit.id], by_id[nach_einheit.id]
    if von.lage_id != lage.id or nach.lage_id != lage.id:
        raise ValueError("Einheiten gehören nicht zur selben Lage")
    if von.personal_modus != nach.personal_modus:
        raise ValueError("Umbuchung zwischen unterschiedlichen Personalmodi ist nicht möglich")
    if von.personal_modus == "summe":
        delta = _zahl(anzahl, "Anzahl") if anzahl is not None else 0
        if delta <= 0:
            raise ValueError("Anzahl muss größer als 0 sein")
        vorher = von.staerke_gesamt or 0
        if vorher < delta:
            raise ValueError("Nicht genügend Personal für Umbuchung")
        von.staerke_gesamt, nach.staerke_gesamt = vorher - delta, (nach.staerke_gesamt or 0) + delta
        text_von, text_nach = f"Umbuchung -{delta} zu {nach.label}", f"Umbuchung +{delta} von {von.label}"
    elif von.personal_modus == "liste":
        if not person_ids:
            raise ValueError("Personen sind erforderlich")
        personen = (
            db.query(LageEinheitPerson)
            .filter(
                LageEinheitPerson.id.in_(person_ids),
                LageEinheitPerson.lage_id == lage.id,
                LageEinheitPerson.einheit_id == von.id,
                LageEinheitPerson.bis_at.is_(None),
            )
            .all()
        )
        if len(personen) != len(set(person_ids)):
            raise ValueError("Person gehört nicht zur Quelleinheit")
        umbuchung_id = str(uuid4())
        for person in personen:
            person.bis_at, person.aktiv_key = _now(), None
            _person_anlegen(
                db,
                lage,
                nach,
                member_id=person.member_id,
                name=person.name,
                funktion=person.funktion,
                qualifikationen=person.qualifikationen,
                herkunft="umbuchung",
                user_id=user_id,
                umbuchung_id=umbuchung_id,
            )
        _neu_berechnen(db, von)
        _neu_berechnen(db, nach)
        text_von, text_nach = (
            f"Umbuchung: {len(personen)} Person(en) zu {nach.label}",
            f"Umbuchung: {len(personen)} Person(en) von {von.label}",
        )
    else:
        raise ValueError("Ungültiger Personalmodus")
    _journal_personal(db, lage, von, text_von, user_id, author_name, "manuell")
    _journal_personal(db, lage, nach, text_nach, user_id, author_name, "manuell")
    _audit(db, lage, von, "umbuchen", user_id)
    _audit(db, lage, nach, "umbuchen", user_id)


def kraefte_summen(db: Session, lage: MajorIncident) -> dict[str, int]:
    """Summiert nur eigenständige Einheiten; Verbände beziehen ihre Stärke aus Kindern."""
    db.flush()
    einheiten = db.query(LageEinheit).filter(LageEinheit.lage_id == lage.id).all()
    # Ein Verband zählt an Stelle seiner Kinder; freie Einheiten zählen selbst.
    zaehlende = [e for e in einheiten if e.resource_type == "verband" or e.verband_id is None]
    return {
        feld: sum(
            verband_summen(db, e)[feld] if e.resource_type == "verband" else getattr(e, f"staerke_{feld}") or 0
            for e in zaehlende
        )
        for feld in ("gesamt", "fuehrung", "agt", "sanitaeter")
    }


# ── Ausstattung ──────────────────────────────────────────────────────────────

_AUSSTATTUNG_FAEHIGKEITEN = {
    "hochwasser",
    "wasserrettung",
    "hoehenrettung",
    "gefahrstoff",
    "faehigkeit_sonstige",
}
_AUSSTATTUNG_FREITEXT = {"sonstiges", "faehigkeit_sonstige"}
_AUSSTATTUNG_STAMM_REF_TYPEN = {"atemschutz_geraet", "verleih_artikel", "vorlage_lage"}


def _audit_ausstattung(
    db: Session, lage: MajorIncident, einheit: LageEinheit, aktion: str, user_id: int | None
) -> None:
    write_audit(
        db,
        "gsl.ressource.ausstattung",
        org_id=lage.org_id,
        user_id=user_id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_id": einheit.id, "aktion": aktion},
    )


def _journal_ausstattung(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    text: str,
    user_id: int | None,
    author_name: str | None,
) -> None:
    _journal(
        db,
        lage.id,
        text,
        author_name=author_name,
        user_id=user_id,
        einheit_id=einheit.id,
        ereignis_typ="ausstattung",
        quelle="manuell",
    )


def _ausstattung_menge(wert: Any) -> int:
    menge = _zahl(wert, "Menge")
    if menge > 9999:
        raise ValueError("Menge darf höchstens 9999 sein")
    return menge


def _ausstattung_text(wert: str | None, feld: str, laenge: int, *, erforderlich: bool = False) -> str | None:
    if wert is None:
        if erforderlich:
            raise ValueError(f"{feld} ist erforderlich")
        return None
    if not isinstance(wert, str):
        raise ValueError(f"{feld} muss Text sein")
    text = wert.strip()
    if erforderlich and not text:
        raise ValueError(f"{feld} ist erforderlich")
    if len(text) > laenge:
        raise ValueError(f"{feld} darf höchstens {laenge} Zeichen haben")
    return text or None


def _ausstattung_kategorie(
    kategorie: str, bezeichnung: str | None, ist_faehigkeit: bool | None
) -> tuple[str, bool]:
    if kategorie not in GSL_AUSSTATTUNG_KATALOG:
        raise ValueError("Ungültige Ausstattungskategorie")
    if kategorie in _AUSSTATTUNG_FREITEXT and not bezeichnung:
        raise ValueError("Bezeichnung ist für diese Kategorie erforderlich")
    if kategorie not in _AUSSTATTUNG_FREITEXT and bezeichnung not in {None, GSL_AUSSTATTUNG_KATALOG[kategorie]}:
        raise ValueError("Freitext ist nur für Sonstiges oder Fähigkeit: Sonstige zulässig")
    abgeleitet = kategorie in _AUSSTATTUNG_FAEHIGKEITEN
    # Das Feld ist Teil der Service-Schnittstelle, wird aber nie aus Aufruferdaten
    # übernommen: Fähigkeit oder Ausstattung ergibt sich ausschließlich aus dem Katalog.
    del ist_faehigkeit
    return GSL_AUSSTATTUNG_KATALOG[kategorie] if bezeichnung is None else bezeichnung, abgeleitet


def _stammreferenz_pruefen(
    db: Session,
    lage: MajorIncident,
    stamm_ref_typ: str | None,
    stamm_ref_id: int | None,
) -> None:
    if stamm_ref_typ is None and stamm_ref_id is None:
        return
    if stamm_ref_typ not in _AUSSTATTUNG_STAMM_REF_TYPEN or stamm_ref_id is None:
        raise ValueError("Ungültige Stammdatenreferenz")
    if isinstance(stamm_ref_id, bool) or not isinstance(stamm_ref_id, int) or stamm_ref_id <= 0:
        raise ValueError("Ungültige Stammdatenreferenz")
    stamm: AtemschutzGeraet | VerleihArtikel | None
    if stamm_ref_typ == "atemschutz_geraet":
        stamm = db.get(AtemschutzGeraet, stamm_ref_id)
    elif stamm_ref_typ == "verleih_artikel":
        stamm = db.get(VerleihArtikel, stamm_ref_id)
    else:
        return
    if not stamm or stamm.org_id != lage.org_id:
        raise ValueError("Stammdatenreferenz gehört nicht zur Organisation der Lage")


def _ausstattung_zeile(db: Session, lage: MajorIncident, einheit: LageEinheit, zeile_id: int) -> LageEinheitAusstattung:
    zeile = db.get(LageEinheitAusstattung, zeile_id)
    if not zeile or zeile.lage_id != lage.id or zeile.einheit_id != einheit.id:
        raise ValueError("Ausstattung gehört nicht zur Einheit und Lage")
    return zeile


def ausstattung_hinzufuegen(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    kategorie: str,
    bezeichnung: str | None = None,
    menge: int = 1,
    status: str = "einsatzbereit",
    bemerkung: str | None = None,
    ist_faehigkeit: bool | None = None,
    stamm_ref_typ: str | None = None,
    stamm_ref_id: int | None = None,
    user_id: int | None,
    author_name: str | None,
) -> LageEinheitAusstattung:
    _check_einheit(lage, einheit)
    bezeichnung = _ausstattung_text(bezeichnung, "Bezeichnung", 120)
    bezeichnung, ist_faehigkeit = _ausstattung_kategorie(kategorie, bezeichnung, ist_faehigkeit)
    menge = _ausstattung_menge(menge)
    if status not in AUSSTATTUNG_STATUS:
        raise ValueError("Ungültiger Ausstattungsstatus")
    bemerkung = _ausstattung_text(bemerkung, "Bemerkung", 300)
    _stammreferenz_pruefen(db, lage, stamm_ref_typ, stamm_ref_id)
    zeile = LageEinheitAusstattung(
        org_id=lage.org_id,
        lage_id=lage.id,
        einheit_id=einheit.id,
        kategorie=kategorie,
        bezeichnung=bezeichnung,
        ist_faehigkeit=ist_faehigkeit,
        menge=menge,
        status=status,
        bemerkung=bemerkung,
        stamm_ref_typ=stamm_ref_typ,
        stamm_ref_id=stamm_ref_id,
        created_by=user_id,
    )
    db.add(zeile)
    db.flush()
    _journal_ausstattung(db, lage, einheit, f"Ausstattung hinzugefügt: {menge}x {bezeichnung}", user_id, author_name)
    _audit_ausstattung(db, lage, einheit, "hinzufuegen", user_id)
    return zeile


def ausstattung_aendern(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    zeile_id: int,
    *,
    menge: int | None = None,
    status: str | None = None,
    bemerkung: str | None = None,
    user_id: int | None,
    author_name: str | None,
) -> LageEinheitAusstattung:
    _check_einheit(lage, einheit)
    zeile = _ausstattung_zeile(db, lage, einheit, zeile_id)
    if menge is not None:
        menge = _ausstattung_menge(menge)
    if status is not None and status not in AUSSTATTUNG_STATUS:
        raise ValueError("Ungültiger Ausstattungsstatus")
    if bemerkung is not None:
        bemerkung = _ausstattung_text(bemerkung, "Bemerkung", 300)
    aenderungen: list[str] = []
    if menge is not None and menge != zeile.menge:
        aenderungen.append(f"Menge {zeile.menge} -> {menge}")
        zeile.menge = menge
    if status is not None and status != zeile.status:
        aenderungen.append(f"Status {zeile.status} -> {status}")
        zeile.status = status
    if bemerkung is not None and bemerkung != zeile.bemerkung:
        aenderungen.append("Bemerkung geändert")
        zeile.bemerkung = bemerkung
    if not aenderungen:
        return zeile
    _journal_ausstattung(db, lage, einheit, f"{zeile.bezeichnung}: {', '.join(aenderungen)}", user_id, author_name)
    _audit_ausstattung(db, lage, einheit, "aendern", user_id)
    return zeile


def ausstattung_entfernen(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    zeile_id: int,
    *,
    user_id: int | None,
    author_name: str | None,
) -> None:
    _check_einheit(lage, einheit)
    zeile = _ausstattung_zeile(db, lage, einheit, zeile_id)
    text = f"Ausstattung entfernt: {zeile.menge}x {zeile.bezeichnung}"
    db.delete(zeile)
    _journal_ausstattung(db, lage, einheit, text, user_id, author_name)
    _audit_ausstattung(db, lage, einheit, "entfernen", user_id)


def vorlage_uebernehmen(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    user_id: int | None,
    author_name: str | None,
) -> list[LageEinheitAusstattung]:
    _check_einheit(lage, einheit)
    if einheit.vehicle_id is None:
        raise ValueError("Einheit hat kein Fahrzeug für eine Vorlagenübernahme")
    # Router- und Test-Sessions können Autoflush deaktivieren; vorhandene,
    # eben angelegte Positionen müssen die Übernahme dennoch sperren.
    db.flush()
    if db.query(LageEinheitAusstattung.id).filter_by(lage_id=lage.id, einheit_id=einheit.id).first():
        raise ValueError("Einheit hat bereits Ausstattung")
    vorlage = (
        db.query(LageEinheit)
        .join(MajorIncident, MajorIncident.id == LageEinheit.lage_id)
        .filter(
            LageEinheit.vehicle_id == einheit.vehicle_id,
            LageEinheit.id != einheit.id,
            MajorIncident.org_id == lage.org_id,
            MajorIncident.id != lage.id,
            MajorIncident.started_at < lage.started_at,
        )
        .order_by(MajorIncident.started_at.desc(), MajorIncident.id.desc(), LageEinheit.id.desc())
        .first()
    )
    if not vorlage:
        raise ValueError("Keine Ausstattungsvorlage aus einer früheren Lage gefunden")
    vorlagen_zeilen = (
        db.query(LageEinheitAusstattung)
        .filter_by(lage_id=vorlage.lage_id, einheit_id=vorlage.id)
        .order_by(LageEinheitAusstattung.id)
        .all()
    )
    if not vorlagen_zeilen:
        raise ValueError("Keine Ausstattungsvorlage aus einer früheren Lage gefunden")
    kopien = [
        LageEinheitAusstattung(
            org_id=lage.org_id,
            lage_id=lage.id,
            einheit_id=einheit.id,
            kategorie=zeile.kategorie,
            bezeichnung=zeile.bezeichnung,
            ist_faehigkeit=zeile.ist_faehigkeit,
            menge=zeile.menge,
            status="einsatzbereit",
            bemerkung=zeile.bemerkung,
            stamm_ref_typ="vorlage_lage",
            stamm_ref_id=zeile.id,
            created_by=user_id,
        )
        for zeile in vorlagen_zeilen
    ]
    db.add_all(kopien)
    _journal_ausstattung(
        db, lage, einheit, f"Ausstattung aus Vorlage übernommen: {len(kopien)} Position(en)", user_id, author_name
    )
    _audit_ausstattung(db, lage, einheit, "vorlage_uebernehmen", user_id)
    return kopien


def ausstattung_umbuchen(
    db: Session,
    lage: MajorIncident,
    von_einheit: LageEinheit,
    nach_einheit: LageEinheit,
    zeile_id: int,
    menge: int,
    *,
    user_id: int | None,
    author_name: str | None,
) -> None:
    """Bucht Ausstattung beider Einheiten atomar innerhalb der Aufrufertransaktion."""
    with db.begin_nested():
        _ausstattung_umbuchen(
            db, lage, von_einheit, nach_einheit, zeile_id, menge, user_id=user_id, author_name=author_name
        )


def _ausstattung_umbuchen(
    db: Session,
    lage: MajorIncident,
    von_einheit: LageEinheit,
    nach_einheit: LageEinheit,
    zeile_id: int,
    menge: int,
    *,
    user_id: int | None,
    author_name: str | None,
) -> None:
    _check_einheit(lage, von_einheit)
    _check_einheit(lage, nach_einheit)
    if von_einheit.id == nach_einheit.id:
        raise ValueError("Quelle und Ziel müssen verschieden sein")
    menge = _ausstattung_menge(menge)
    if menge <= 0:
        raise ValueError("Menge muss größer als 0 sein")
    gesperrt = (
        db.query(LageEinheit)
        .filter(LageEinheit.id.in_(sorted((von_einheit.id, nach_einheit.id))))
        .order_by(LageEinheit.id)
        .with_for_update()
        .all()
    )
    if len(gesperrt) != 2 or any(e.lage_id != lage.id for e in gesperrt):
        raise ValueError("Einheiten gehören nicht zur selben Lage")
    einheiten = {e.id: e for e in gesperrt}
    von, nach = einheiten[von_einheit.id], einheiten[nach_einheit.id]
    zeile = (
        db.query(LageEinheitAusstattung)
        .filter_by(id=zeile_id, lage_id=lage.id, einheit_id=von.id)
        .with_for_update()
        .first()
    )
    if not zeile:
        raise ValueError("Ausstattung gehört nicht zur Einheit und Lage")
    if menge > zeile.menge:
        raise ValueError("Nicht genügend Ausstattung für Umbuchung")
    ziel = (
        db.query(LageEinheitAusstattung)
        .filter_by(lage_id=lage.id, einheit_id=nach.id, kategorie=zeile.kategorie, bezeichnung=zeile.bezeichnung, status=zeile.status)
        .with_for_update()
        .first()
    )
    umbuchung_id = str(uuid4())
    if ziel:
        ziel.menge += menge
        ziel.umbuchung_id = umbuchung_id
    else:
        ziel = LageEinheitAusstattung(
            org_id=lage.org_id,
            lage_id=lage.id,
            einheit_id=nach.id,
            kategorie=zeile.kategorie,
            bezeichnung=zeile.bezeichnung,
            ist_faehigkeit=zeile.ist_faehigkeit,
            menge=menge,
            status=zeile.status,
            bemerkung=zeile.bemerkung,
            stamm_ref_typ=zeile.stamm_ref_typ,
            stamm_ref_id=zeile.stamm_ref_id,
            umbuchung_id=umbuchung_id,
            created_by=user_id,
        )
        db.add(ziel)
    if zeile.menge == menge:
        db.delete(zeile)
    else:
        zeile.menge -= menge
        zeile.umbuchung_id = umbuchung_id
    _journal_ausstattung(db, lage, von, f"Umbuchung: -{menge} {zeile.bezeichnung} zu {nach.label}", user_id, author_name)
    _journal_ausstattung(db, lage, nach, f"Umbuchung: +{menge} {zeile.bezeichnung} von {von.label}", user_id, author_name)
    _audit_ausstattung(db, lage, von, "umbuchen", user_id)
    _audit_ausstattung(db, lage, nach, "umbuchen", user_id)


def ausstattung_liste(db: Session, einheit: LageEinheit) -> list[dict[str, Any]]:
    """Liefert Ausstattung für die Darstellung, Fähigkeiten immer am Ende."""
    zeilen = (
        db.query(LageEinheitAusstattung)
        .filter_by(lage_id=einheit.lage_id, einheit_id=einheit.id)
        .order_by(LageEinheitAusstattung.ist_faehigkeit, LageEinheitAusstattung.bezeichnung, LageEinheitAusstattung.id)
        .all()
    )
    return [
        {
            "id": zeile.id,
            "kategorie": zeile.kategorie,
            "bezeichnung": zeile.bezeichnung,
            "ist_faehigkeit": zeile.ist_faehigkeit,
            "menge": zeile.menge,
            "status": zeile.status,
            "bemerkung": zeile.bemerkung,
            "stamm_ref_typ": zeile.stamm_ref_typ,
            "stamm_ref_id": zeile.stamm_ref_id,
        }
        for zeile in zeilen
    ]


def ausstattung_warnungen(db: Session, einheit: LageEinheit) -> int:
    return (
        db.query(LageEinheitAusstattung)
        .filter(
            LageEinheitAusstattung.lage_id == einheit.lage_id,
            LageEinheitAusstattung.einheit_id == einheit.id,
            LageEinheitAusstattung.status.in_(("defekt", "eingeschraenkt")),
        )
        .count()
    )


# ── Verbände und Aufteilungen ────────────────────────────────────────────────


def verband_kinder(db: Session, verband: LageEinheit) -> list[LageEinheit]:
    """Liefert die unmittelbaren, stabil sortierten Mitglieder eines Verbands."""
    return (
        db.query(LageEinheit)
        .filter(LageEinheit.verband_id == verband.id)
        .order_by(LageEinheit.id)
        .all()
    )


def ist_kind(einheit: LageEinheit) -> bool:
    return einheit.verband_id is not None


def _pflege_audit(db: Session, lage: MajorIncident, einheit: LageEinheit, aktion: str, user_id: int | None) -> None:
    write_audit(
        db,
        f"gsl.ressource.{aktion}",
        org_id=lage.org_id,
        user_id=user_id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_id": einheit.id},
    )


def _pflege_journal(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    text: str,
    *,
    ereignis_typ: str,
    user_id: int | None,
    author_name: str | None,
) -> None:
    _journal(
        db,
        lage.id,
        text,
        author_name=author_name,
        user_id=user_id,
        einheit_id=einheit.id,
        ereignis_typ=ereignis_typ,
        quelle="manuell",
    )


def _verband_pruefen(einheit: LageEinheit) -> None:
    if einheit.resource_type == "verband":
        raise ValueError("Verbände können nicht aufgeteilt werden")
    if ist_kind(einheit):
        raise ValueError("Einheit ist Teil eines Verbands")


def verband_bilden(
    db: Session,
    lage: MajorIncident,
    *,
    label: str,
    einheit_ids: list[int],
    user_id: int | None,
    author_name: str | None,
) -> LageEinheit:
    """Fasst selbständige Einheiten zusammen, ohne ihre Aufträge umzuhängen."""
    if not label or not label.strip() or len(label.strip()) > 120:
        raise ValueError("Bezeichnung ist erforderlich und darf höchstens 120 Zeichen haben")
    if len(set(einheit_ids)) < 2:
        raise ValueError("Ein Verband benötigt mindestens zwei Einheiten")
    with db.begin_nested():
        kinder = (
            db.query(LageEinheit)
            .filter(LageEinheit.id.in_(set(einheit_ids)))
            .order_by(LageEinheit.id)
            .with_for_update()
            .all()
        )
        if len(kinder) != len(set(einheit_ids)):
            raise ValueError("Einheit nicht gefunden")
        for kind in kinder:
            _check_einheit(lage, kind)
            if kind.status == "abgerueckt":
                raise ValueError(f"Einheit {kind.label} ist abgerückt")
            if kind.resource_type == "verband":
                raise ValueError(f"Einheit {kind.label} ist selbst ein Verband")
            if kind.verband_id is not None:
                raise ValueError(f"Einheit {kind.label} ist bereits Teil eines Verbands")
        aktive = (
            db.query(EinheitSiteDispatch, LageEinheit.label, IncidentSite.bezeichnung)
            .join(LageEinheit, LageEinheit.id == EinheitSiteDispatch.einheit_id)
            .join(IncidentSite, IncidentSite.id == EinheitSiteDispatch.site_id)
            .filter(EinheitSiteDispatch.einheit_id.in_([k.id for k in kinder]), dispatch_aktiv_filter())
            .all()
        )
        stellen = {dispatch.site_id for dispatch, _, _ in aktive}
        if len(stellen) > 1:
            details = ", ".join(f"{name} -> {stelle}" for dispatch, name, stelle in aktive)
            raise ValueError(f"Aktive Dispositionen müssen dieselbe Stelle haben: {details}")
        sektor_ids = {kind.sector_id for kind in kinder}
        verband = LageEinheit(
            lage_id=lage.id,
            label=label.strip(),
            resource_type="verband",
            status="im_einsatz" if any(k.status == "im_einsatz" for k in kinder) else "bereitgestellt",
            sector_id=kinder[0].sector_id if len(sektor_ids) == 1 else None,
            vehicle_id=None,
            is_from_org=False,
            personal_modus="summe",
        )
        db.add(verband)
        db.flush()
        for kind in kinder:
            kind.verband_id = verband.id
            _pflege_journal(
                db, lage, kind, f"Teil von Verband {verband.label}", ereignis_typ="status", user_id=user_id, author_name=author_name
            )
        _pflege_journal(
            db,
            lage,
            verband,
            f"Verband {verband.label} gebildet aus {', '.join(k.label for k in kinder)}",
            ereignis_typ="status",
            user_id=user_id,
            author_name=author_name,
        )
        _pflege_audit(db, lage, verband, "zusammenfassen", user_id)
    return verband


def verband_aufloesen(
    db: Session, lage: MajorIncident, verband: LageEinheit, *, user_id: int | None, author_name: str | None
) -> None:
    _check_einheit(lage, verband)
    if verband.resource_type != "verband":
        raise ValueError("Einheit ist kein Verband")
    with db.begin_nested():
        db.query(LageEinheit).filter(LageEinheit.id == verband.id).with_for_update().one()
        kinder = verband_kinder(db, verband)
        for kind in kinder:
            kind.verband_id = None
            _pflege_journal(
                db, lage, kind, f"Verband {verband.label} aufgelöst", ereignis_typ="status", user_id=user_id, author_name=author_name
            )
        verband.status, verband.released_at = "abgerueckt", _now()
        aktiv = db.query(EinheitSiteDispatch.id).filter(EinheitSiteDispatch.einheit_id == verband.id, dispatch_aktiv_filter()).first()
        hinweis = "; aktive Dispositionen bleiben unverändert" if aktiv else ""
        _pflege_journal(
            db, lage, verband, f"Verband {verband.label} aufgelöst{hinweis}", ereignis_typ="status", user_id=user_id, author_name=author_name
        )
        from app.services.gk_zugang_service import widerrufe

        widerrufe(db, verband.id, grund="abgerueckt", user_id=user_id)
        _pflege_audit(db, lage, verband, "verband_aufloesen", user_id)


def verband_summen(db: Session, verband: LageEinheit) -> dict[str, Any]:
    if verband.resource_type != "verband":
        raise ValueError("Einheit ist kein Verband")
    kinder = verband_kinder(db, verband)
    summen: dict[str, Any] = {feld: 0 for feld in ("gesamt", "fuehrung", "agt", "sanitaeter")}
    for kind in kinder:
        if kind.personal_modus == "summe":
            zahlen = _zahlen(kind)
        else:
            personen = _aktive_personen(db, kind)
            zahlen = {
                "gesamt": len(personen),
                "fuehrung": sum(person.funktion == "fuehrung" for person in personen),
                "agt": sum(
                    person.funktion == "agt"
                    or "AGT" in {x.strip().upper() for x in (person.qualifikationen or "").split(",")}
                    for person in personen
                ),
                "sanitaeter": sum(
                    person.funktion == "sanitaeter"
                    or "SAN" in {x.strip().upper() for x in (person.qualifikationen or "").split(",")}
                    for person in personen
                ),
            }
        for feld in summen:
            summen[feld] += zahlen[feld]
    ausstattung: dict[tuple[str, str, str], int] = {}
    if kinder:
        for zeile in db.query(LageEinheitAusstattung).filter(LageEinheitAusstattung.einheit_id.in_([k.id for k in kinder])):
            schluessel = (zeile.kategorie, zeile.bezeichnung, zeile.status)
            ausstattung[schluessel] = ausstattung.get(schluessel, 0) + zeile.menge
    summen["ausstattung"] = [
        {"kategorie": kategorie, "bezeichnung": bezeichnung, "status": status, "menge": menge}
        for (kategorie, bezeichnung, status), menge in sorted(ausstattung.items())
    ]
    return summen


def einheit_aufteilen(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    teile: list[dict[str, Any]],
    *,
    user_id: int | None,
    author_name: str | None,
) -> list[LageEinheit]:
    """Spaltet Personal und Ausstattung transaktional in neue, selbständige Einheiten auf."""
    _check_einheit(lage, einheit)
    _verband_pruefen(einheit)
    if not teile:
        raise ValueError("Mindestens ein Teil ist erforderlich")
    with db.begin_nested():
        mutter = db.query(LageEinheit).filter(LageEinheit.id == einheit.id).with_for_update().one()
        _verband_pruefen(mutter)
        neu: list[LageEinheit] = []
        for teil in teile:
            label = teil.get("label")
            typ = teil.get("resource_type", mutter.resource_type)
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
                raise ValueError("Bezeichnung jedes Teils ist erforderlich")
            if typ not in {"fahrzeug", "extern", "material"}:
                raise ValueError("Ungültiger Ressourcentyp für Teil")
            ziel = LageEinheit(
                lage_id=lage.id, label=label.strip(), resource_type=typ, vehicle_id=None,
                aufgeteilt_von_id=mutter.id, org_name=mutter.org_name, bos=mutter.bos,
                sector_id=mutter.sector_id, status="bereitgestellt", is_from_org=False,
                personal_modus=mutter.personal_modus,
            )
            db.add(ziel)
            neu.append(ziel)
        db.flush()
        if mutter.personal_modus == "summe":
            mengen = [_zahl(teil.get("anzahl"), "Anzahl") for teil in teile]
            if sum(mengen) > (mutter.staerke_gesamt or 0):
                raise ValueError("Nicht genügend Personal für Aufteilung")
            mutter.staerke_gesamt = (mutter.staerke_gesamt or 0) - sum(mengen)
            for ziel, menge in zip(neu, mengen, strict=True):
                ziel.staerke_gesamt = menge
        else:
            ids = [person_id for teil in teile for person_id in teil.get("person_ids", [])]
            if len(ids) != len(set(ids)):
                raise ValueError("Person darf nur einem Teil zugeordnet werden")
            personen = (
                db.query(LageEinheitPerson).filter(
                    LageEinheitPerson.id.in_(ids), LageEinheitPerson.einheit_id == mutter.id,
                    LageEinheitPerson.lage_id == lage.id, LageEinheitPerson.bis_at.is_(None)
                ).with_for_update().all()
            )
            if len(personen) != len(ids):
                raise ValueError("Person gehört nicht zur Quelleinheit")
            by_id = {person.id: person for person in personen}
            umbuchung_id = str(uuid4())
            for ziel, teil in zip(neu, teile, strict=True):
                for person_id in teil.get("person_ids", []):
                    person = by_id[person_id]
                    person.bis_at, person.aktiv_key = _now(), None
                    _person_anlegen(db, lage, ziel, member_id=person.member_id, name=person.name, funktion=person.funktion,
                                    qualifikationen=person.qualifikationen, herkunft="umbuchung", user_id=user_id, umbuchung_id=umbuchung_id)
            _neu_berechnen(db, mutter)
            for ziel in neu:
                _neu_berechnen(db, ziel)
        umbuchung_id = str(uuid4())
        ausstattung_verschoben = False
        for ziel, teil in zip(neu, teile, strict=True):
            for pos in teil.get("ausstattung", []):
                zeile_id, menge = pos.get("zeile_id"), _ausstattung_menge(pos.get("menge"))
                quelle = db.query(LageEinheitAusstattung).filter_by(id=zeile_id, lage_id=lage.id, einheit_id=mutter.id).with_for_update().first()
                if not quelle or menge <= 0 or menge > quelle.menge:
                    raise ValueError("Nicht genügend Ausstattung für Aufteilung")
                zielzeile = LageEinheitAusstattung(org_id=lage.org_id, lage_id=lage.id, einheit_id=ziel.id,
                    kategorie=quelle.kategorie, bezeichnung=quelle.bezeichnung, ist_faehigkeit=quelle.ist_faehigkeit,
                    menge=menge, status=quelle.status, bemerkung=quelle.bemerkung, stamm_ref_typ=quelle.stamm_ref_typ,
                    stamm_ref_id=quelle.stamm_ref_id, umbuchung_id=umbuchung_id, created_by=user_id)
                db.add(zielzeile)
                ausstattung_verschoben = True
                if menge == quelle.menge:
                    db.delete(quelle)
                else:
                    quelle.menge -= menge
                    quelle.umbuchung_id = umbuchung_id
            for dispatch_id in teil.get("dispatches_kopieren", []):
                dispatch = db.get(EinheitSiteDispatch, dispatch_id)
                if not dispatch or dispatch.einheit_id != mutter.id or dispatch.withdrawn_at or dispatch.beendet_at:
                    raise ValueError("Disposition gehört nicht zur Muttereinheit oder ist nicht aktiv")
                from app.services.resource_service import dispatch_to_site

                dispatch_to_site(db, ziel.id, lage.id, dispatch.site_id, auftrag=dispatch.auftrag,
                                 reihenfolge=dispatch.reihenfolge, author_name=author_name, user_id=user_id)
        _pflege_journal(db, lage, mutter, f"Aufteilung aus {mutter.label}: {', '.join(z.label for z in neu)}",
                        ereignis_typ="personal", user_id=user_id, author_name=author_name)
        for ziel in neu:
            _pflege_journal(db, lage, ziel, f"Teil {ziel.label} abgespalten aus {mutter.label}",
                            ereignis_typ="personal", user_id=user_id, author_name=author_name)
        if ausstattung_verschoben:
            _pflege_journal(db, lage, mutter, f"Ausstattung für Aufteilung aus {mutter.label} verschoben",
                            ereignis_typ="ausstattung", user_id=user_id, author_name=author_name)
            for ziel in neu:
                _pflege_journal(db, lage, ziel, f"Ausstattung aus {mutter.label} übernommen",
                                ereignis_typ="ausstattung", user_id=user_id, author_name=author_name)
        _pflege_audit(db, lage, mutter, "aufteilen", user_id)
    return neu
