"""Lesender, konservativer Abgleich fuer Benutzerverwaltung und MCP."""

import re
import unicodedata
from collections import defaultdict
from difflib import get_close_matches

from sqlalchemy.orm import Session

from app.models.master import Member
from app.models.user import User


def name_normalisieren(value: str | None) -> str:
    value = (value or "").casefold().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"}))
    value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", value))


def telefon_normalisieren(value: str | None) -> str:
    # Keine Landesvorwahl raten: nationale/internationale Varianten bleiben im Zweifel manuell.
    value = re.sub(r"[^0-9+]", "", value or "")
    if value.startswith("00"):
        value = "+" + value[2:]
    return value if len(value.lstrip("+")) >= 7 else ""


def benutzer_name(user: User) -> tuple[str | None, str | None]:
    """Das bestehende Modell speichert nur full_name, keine separaten Namensfelder."""
    parts = (user.full_name or "").strip().split(maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (None, None)


def abgleichen(db: Session, org_id: int) -> dict[str, list[dict]]:
    users = db.query(User).filter(User.org_id == org_id, User.is_device.is_(False)).order_by(User.id).all()
    members = db.query(Member).filter(Member.org_id == org_id).order_by(Member.id).all()
    linked = {row[0] for row in db.query(User.member_id).filter(
        User.org_id == org_id, User.member_id.is_not(None),
    ).all()}
    indexes = {key: defaultdict(set) for key in ("name", "email", "telefon")}
    member_by_id = {m.id: m for m in members}
    for member in members:
        values = {
            "name": name_normalisieren(f"{member.firstname} {member.lastname}"),
            "email": (member.email or "").strip().casefold(),
            "telefon": telefon_normalisieren(member.phone),
        }
        for key, value in values.items():
            if value:
                indexes[key][value].add(member.id)

    candidates = {}
    competing = defaultdict(set)
    for user in users:
        if user.member_id is not None:
            continue
        values = {
            "name": name_normalisieren(user.full_name or user.display_name),
            "email": (user.email or "").strip().casefold(),
            "telefon": telefon_normalisieren(user.phone),
        }
        hits = defaultdict(list)
        for key, value in values.items():
            if value:
                for member_id in indexes[key].get(value, set()):
                    hits[member_id].append(key)
        if values["name"] and not indexes["name"].get(values["name"]):
            for similar in get_close_matches(values["name"], indexes["name"], n=3, cutoff=0.9):
                for member_id in indexes["name"][similar]:
                    hits[member_id].append("name_aehnlich")
        candidates[user.id] = hits
        for member_id in hits:
            competing[member_id].add(user.id)

    result: dict[str, list[dict]] = {"sicher": [], "pruefen": [], "kein_treffer": [], "bestehend": []}
    for user in users:
        if user.member_id is not None:
            result["bestehend"].append({"benutzer_id": user.id, "mitglied_id": user.member_id})
            continue
        hits = candidates[user.id]
        entries = []
        for member_id, features in sorted(hits.items()):
            reasons = []
            if len(hits) > 1 or len(competing[member_id]) > 1:
                reasons.append("mehrdeutig")
            if member_id in linked:
                reasons.append("mitglied_bereits_verknuepft")
            if not user.active or not member_by_id[member_id].active:
                reasons.append("inaktiv")
            if user.is_system_admin:
                reasons.append("geschuetzter_benutzer")
            if len(set(features) & {"name", "email", "telefon"}) < 2:
                reasons.append("weniger_als_zwei_merkmale")
            entries.append({"mitglied_id": member_id, "merkmale": features, "gruende": reasons})
        safe = len(entries) == 1 and not entries[0]["gruende"]
        category = "sicher" if safe else "pruefen" if entries else "kein_treffer"
        result[category].append({
            "benutzer_id": user.id, "kandidaten": entries, "automatisch_verknuepfbar": safe,
            "mitglied_id": entries[0]["mitglied_id"] if safe else None,
        })
    return result
