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
from app.models.major_incident import PERSON_FUNKTIONEN, LageEinheit, LageEinheitPerson, MajorIncident
from app.models.master import Member
from app.services.resource_service import _journal


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
    einheiten = db.query(LageEinheit).filter(LageEinheit.lage_id == lage.id).all()
    # Verbände speichern keine eigene Mannschaft: ihre Kinder sind die Quelle.
    # Damit zählt ein Kind global genau einmal, unabhängig davon, ob es einem
    # Verband zugeordnet ist.
    zaehlende = [e for e in einheiten if e.resource_type != "verband"]
    return {
        feld: sum(getattr(e, f"staerke_{feld}") or 0 for e in zaehlende)
        for feld in ("gesamt", "fuehrung", "agt", "sanitaeter")
    }
