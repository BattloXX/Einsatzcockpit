"""Auswertung Mitglied x Fahrzeug fuer Maschinistenfahrten."""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.timezones import local_date_to_utc
from app.models.fahrtenbuch import Fahrt, FahrtKategorie, FahrtStatus
from app.models.master import FireDept, Member, VehicleMaster

FARBE_UEBUNG = "DDEBF7"
FARBE_EINSATZ = "FCE4D6"
FARBE_STUFE = {1: "00B050", 2: "FFE699", 3: "FF3B30", 4: "C00000"}


def maschinist_stufe(member) -> int | None:
    """Niedrigste (= höchste) gültige M-Stufe, sonst None."""
    heute = date.today()
    stufen = [
        mq.qualification.maschinist_stufe
        for mq in member.qualifications
        if mq.qualification
        and mq.qualification.maschinist_stufe is not None
        and (mq.valid_until is None or mq.valid_until >= heute)
    ]
    return min(stufen) if stufen else None


def berechne_maschinisten_matrix(db: Session, org_id: int, jahr: int) -> dict:
    org = db.get(FireDept, org_id)
    if org is None:
        raise ValueError("Organisation nicht gefunden")
    beginn = local_date_to_utc(f"{jahr}-01-01", org=org)
    ende = local_date_to_utc(f"{jahr}-12-31", end=True, org=org)

    fahrzeuge = (
        db.query(VehicleMaster)
        .filter(
            VehicleMaster.dept_id == org_id,
            VehicleMaster.active == True,  # noqa: E712
            VehicleMaster.is_adhoc == False,  # noqa: E712
            VehicleMaster.is_external == False,  # noqa: E712
            VehicleMaster.deleted == False,  # noqa: E712
        )
        .execution_options(include_all_tenants=True)
        .order_by(VehicleMaster.display_order)
        .all()
    )
    fahrzeug_ids = {fz.id for fz in fahrzeuge}
    fahrten = (
        db.query(Fahrt)
        .filter(
            Fahrt.org_id == org_id,
            Fahrt.status == FahrtStatus.aktiv,
            Fahrt.nicht_statistikrelevant == False,  # noqa: E712
            Fahrt.fahrttyp.in_((FahrtKategorie.einsatz, FahrtKategorie.uebung)),
            Fahrt.zeitpunkt >= beginn,
            Fahrt.zeitpunkt <= ende,
        )
        .execution_options(include_all_tenants=True)
        .all()
    )
    korb_ids = {
        f.fahrzeug_id for f in fahrten
        if f.fahrzeug_id in fahrzeug_ids and (f.maschinist2_member_id or f.maschinist2_name)
    }
    spalten = []
    for fz in fahrzeuge:
        spalten.append({
            "key": f"{fz.id}:ma", "fahrzeug_id": fz.id, "label": fz.code,
            "gruppe": fz.code, "rolle": "ma",
        })
        if fz.zweiter_maschinist_pflicht or fz.id in korb_ids:
            spalten.append({
                "key": f"{fz.id}:korb", "label": f"{fz.code} Korb",
                "fahrzeug_id": fz.id, "gruppe": fz.code, "rolle": "korb",
            })

    members = (
        db.query(Member).filter(Member.org_id == org_id, Member.active == True)  # noqa: E712
        .execution_options(include_all_tenants=True).all()
    )
    member_by_id = {m.id: m for m in members}
    rows: dict[tuple[str, object], dict] = {}
    for member in members:
        stufe = maschinist_stufe(member)
        if stufe is not None:
            rows[("id", member.id)] = {
                "member_id": member.id, "name": f"{member.lastname} {member.firstname}",
                "lastname": member.lastname, "firstname": member.firstname,
                "stufe": stufe, "zellen": {}, "summe": {"uebung": 0, "einsatz": 0},
            }

    for fahrt in fahrten:
        if fahrt.fahrzeug_id not in fahrzeug_ids:
            continue
        personen = [
            ("ma", fahrt.maschinist_member_id, fahrt.maschinist_name),
            ("korb", fahrt.maschinist2_member_id, fahrt.maschinist2_name),
        ]
        for rolle, member_id, name in personen:
            if not name and not member_id:
                continue
            key = ("id", member_id) if member_id else ("name", (name or "").strip().casefold())
            matched_member = member_by_id.get(member_id) if member_id else None
            if key not in rows:
                fallback_name = (name or "").strip()
                rows[key] = {
                    "member_id": member_id,
                    "name": f"{matched_member.lastname} {matched_member.firstname}"
                    if matched_member else fallback_name,
                    "lastname": matched_member.lastname if matched_member else fallback_name,
                    "firstname": matched_member.firstname if matched_member else "",
                    "stufe": maschinist_stufe(matched_member) if matched_member else None,
                    "zellen": {}, "summe": {"uebung": 0, "einsatz": 0},
                }
            column_key = f"{fahrt.fahrzeug_id}:{rolle}"
            if not any(s["key"] == column_key for s in spalten):
                continue
            typ = "uebung" if fahrt.fahrttyp == FahrtKategorie.uebung else "einsatz"
            zelle = rows[key]["zellen"].setdefault(column_key, {"uebung": 0, "einsatz": 0})
            zelle[typ] += 1
            rows[key]["summe"][typ] += 1

    zeilen = sorted(rows.values(), key=lambda r: (
        r["stufe"] is None, r["stufe"] or 99,
        r["lastname"].casefold(), r["firstname"].casefold(),
    ))
    summen = {s["key"]: {"uebung": 0, "einsatz": 0} for s in spalten}
    summen["gesamt"] = {"uebung": 0, "einsatz": 0}
    for row in zeilen:
        for key, zelle in row["zellen"].items():
            summen[key]["uebung"] += zelle["uebung"]
            summen[key]["einsatz"] += zelle["einsatz"]
        summen["gesamt"]["uebung"] += row["summe"]["uebung"]
        summen["gesamt"]["einsatz"] += row["summe"]["einsatz"]
        row.pop("lastname")
        row.pop("firstname")
    return {"jahr": jahr, "spalten": spalten, "zeilen": zeilen, "summen": summen}
