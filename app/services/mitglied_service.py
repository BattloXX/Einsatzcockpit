"""Gemeinsame Schreiblogik fuer Mitglieder innerhalb einer Organisation."""

from datetime import date
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.master import Member, MemberQualification, MemberTag, MemberTagAssignment, Qualification

MITGLIED_FELDER = {"vorname", "nachname", "telefon", "email", "sybos_id", "ist_gruppenkommandant"}
_MAX_LAENGEN = {"vorname": 100, "nachname": 100, "telefon": 30, "email": 200, "sybos_id": 50}


def _pflichttext(wert: str, feld: str) -> str:
    text = wert.strip()
    if not text:
        raise ValueError(f"{feld} darf nicht leer sein.")
    if len(text) > _MAX_LAENGEN[feld]:
        raise ValueError(f"{feld} darf maximal {_MAX_LAENGEN[feld]} Zeichen lang sein.")
    return text


def _optionaltext(wert: str, feld: str) -> str | None:
    text = wert.strip()
    if not text:
        return None
    if len(text) > _MAX_LAENGEN[feld]:
        raise ValueError(f"{feld} darf maximal {_MAX_LAENGEN[feld]} Zeichen lang sein.")
    return text


def mitglied_laden(db: Session, org_id: int, mitglied_id: int) -> Member:
    member = db.query(Member).filter(Member.id == mitglied_id, Member.org_id == org_id).first()
    if member is None:
        raise ValueError("Mitglied nicht gefunden.")
    return member


def dubletten_pruefen(
    db: Session, org_id: int, vorname: str, nachname: str, email: str | None, sybos_id: str | None
) -> list[int]:
    kriterien: list[ColumnElement[bool]] = [
        (func.lower(Member.firstname) == vorname.lower()) & (func.lower(Member.lastname) == nachname.lower())
    ]
    if email:
        kriterien.append(func.lower(Member.email) == email.lower())
    if sybos_id:
        kriterien.append(func.lower(Member.sybos_id) == sybos_id.lower())
    return [
        member_id for (member_id,) in (
            db.query(Member.id)
            .filter(Member.org_id == org_id, or_(*kriterien))
            .order_by(Member.id)
            .all()
        )
    ]


def mitglied_anlegen(
    db: Session,
    org_id: int,
    vorname: str,
    nachname: str,
    telefon: str = "",
    email: str = "",
    sybos_id: str = "",
    *,
    dublette_ignorieren: bool = False,
) -> Member:
    normalisiert_vorname = _pflichttext(vorname, "vorname")
    normalisiert_nachname = _pflichttext(nachname, "nachname")
    normalisierte_email = _optionaltext(email, "email")
    normalisierte_sybos_id = _optionaltext(sybos_id, "sybos_id")
    kandidaten = dubletten_pruefen(
        db, org_id, normalisiert_vorname, normalisiert_nachname, normalisierte_email, normalisierte_sybos_id
    )
    if kandidaten and not dublette_ignorieren:
        raise ValueError("Mögliche Mitglieder-Dublette gefunden: IDs " + ", ".join(map(str, kandidaten)) + ".")
    member = Member(
        org_id=org_id,
        firstname=normalisiert_vorname,
        lastname=normalisiert_nachname,
        phone=_optionaltext(telefon, "telefon"),
        email=normalisierte_email,
        sybos_id=normalisierte_sybos_id,
    )
    db.add(member)
    db.flush()
    return member


def mitglied_aktualisieren(
    db: Session, org_id: int, mitglied_id: int, felder: dict[str, Any]
) -> tuple[Member, dict[str, dict[str, object]]]:
    unbekannt = set(felder) - MITGLIED_FELDER
    if unbekannt:
        raise ValueError("Unbekannte Mitgliedsfelder: " + ", ".join(sorted(unbekannt)))
    if not felder:
        raise ValueError("Mindestens ein Mitgliedsfeld ist erforderlich.")
    member = mitglied_laden(db, org_id, mitglied_id)
    geaendert: dict[str, dict[str, object]] = {}
    feld_zu_attribut = {
        "vorname": "firstname",
        "nachname": "lastname",
        "telefon": "phone",
        "email": "email",
        "sybos_id": "sybos_id",
        "ist_gruppenkommandant": "ist_gruppenkommandant",
    }
    for feld, wert in felder.items():
        if feld == "ist_gruppenkommandant":
            if not isinstance(wert, bool):
                raise ValueError("ist_gruppenkommandant muss true oder false sein.")
            neu: object = wert
        elif not isinstance(wert, str):
            raise ValueError(f"{feld} muss ein Text sein.")
        elif feld in {"vorname", "nachname"}:
            neu = _pflichttext(wert, feld)
        else:
            neu = _optionaltext(wert, feld)
        attribut = feld_zu_attribut[feld]
        alt = getattr(member, attribut)
        if alt != neu:
            setattr(member, attribut, neu)
            geaendert[feld] = {"alt": alt, "neu": neu}
    return member, geaendert


def aktiv_setzen(db: Session, org_id: int, mitglied_id: int, aktiv: bool) -> tuple[Member, dict[str, object]]:
    member = mitglied_laden(db, org_id, mitglied_id)
    alt = member.active
    member.active = aktiv
    return member, {"alt": alt, "neu": aktiv}


def _qualifikationen_laden(db: Session, codes: list[str]) -> dict[str, Qualification]:
    qualifikationen = db.query(Qualification).filter(Qualification.code.in_(codes)).all() if codes else []
    nach_code = {qualifikation.code: qualifikation for qualifikation in qualifikationen}
    unbekannt = sorted(set(codes) - set(nach_code))
    if unbekannt:
        raise ValueError("Unbekannte Qualifikationscodes: " + ", ".join(unbekannt))
    return nach_code


def qualifikationen_setzen(
    db: Session, org_id: int, mitglied_id: int, qualifikationen: list[dict[str, Any]]
) -> tuple[Member, list[dict[str, object]]]:
    member = mitglied_laden(db, org_id, mitglied_id)
    normalisiert: list[dict[str, object]] = []
    codes: list[str] = []
    for eintrag in qualifikationen:
        if not isinstance(eintrag, dict) or set(eintrag) - {"code", "gueltig_bis"} or "code" not in eintrag:
            raise ValueError("Jede Qualifikation benötigt code und optional gueltig_bis.")
        code = eintrag["code"]
        if not isinstance(code, str) or not code.strip():
            raise ValueError("Qualifikationscode darf nicht leer sein.")
        code = code.strip()
        if code in codes:
            raise ValueError(f"Qualifikationscode {code} wurde mehrfach angegeben.")
        gueltig_bis = eintrag.get("gueltig_bis")
        if gueltig_bis is not None:
            if (
                not isinstance(gueltig_bis, str)
                or len(gueltig_bis) != 10
                or gueltig_bis[4] != "-"
                or gueltig_bis[7] != "-"
            ):
                raise ValueError("gueltig_bis muss YYYY-MM-DD oder null sein.")
            try:
                gueltig_bis = date.fromisoformat(gueltig_bis)
            except ValueError as exc:
                raise ValueError("gueltig_bis muss YYYY-MM-DD oder null sein.") from exc
        codes.append(code)
        normalisiert.append({"code": code, "gueltig_bis": gueltig_bis})
    nach_code = _qualifikationen_laden(db, codes)
    bestehend = db.query(MemberQualification).filter(MemberQualification.member_id == member.id).all()
    for zuordnung in bestehend:
        db.delete(zuordnung)
    for eintrag in normalisiert:
        db.add(MemberQualification(
            member_id=member.id,
            qualification_id=nach_code[str(eintrag["code"])].id,
            valid_until=eintrag["gueltig_bis"],  # type: ignore[arg-type]
        ))
    return member, normalisiert


def tags_setzen(db: Session, org_id: int, mitglied_id: int, tags: list[str]) -> tuple[Member, list[str]]:
    member = mitglied_laden(db, org_id, mitglied_id)
    namen: list[str] = []
    for tag in tags:
        if not isinstance(tag, str) or not tag.strip():
            raise ValueError("Tag-Namen dürfen nicht leer sein.")
        name = tag.strip()
        if name in namen:
            raise ValueError(f"Tag {name} wurde mehrfach angegeben.")
        namen.append(name)
    vorhandene_tags = (
        db.query(MemberTag)
        .filter(MemberTag.org_id == org_id, MemberTag.name.in_(namen))
        .all()
        if namen else []
    )
    nach_name = {tag.name: tag for tag in vorhandene_tags}
    unbekannt = sorted(set(namen) - set(nach_name))
    if unbekannt:
        raise ValueError("Unbekannte Tags: " + ", ".join(unbekannt))
    bestehend = db.query(MemberTagAssignment).filter(MemberTagAssignment.member_id == member.id).all()
    for zuordnung in bestehend:
        db.delete(zuordnung)
    for name in namen:
        db.add(MemberTagAssignment(member_id=member.id, tag_id=nach_name[name].id))
    return member, namen
