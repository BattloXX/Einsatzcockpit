"""Structured, additive contact upserts shared by MCP bulk and import tools.

This module deliberately does not replace legacy contact fields.  It mirrors a
preferred structured value into the old fields only where this is necessary for
older clients, and it never clears data for an omitted field.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.core.telefon import telefon_normalisiert
from app.models.kontakt import (
    Kontakt, KontaktAdresse, KontaktEmail, KontaktExterneReferenz,
    KontaktOrganisation, KontaktOrganisationFunktion, KontaktTelefon,
)

MAX_BATCH = 250
VALID_MODES = {"create_only", "update_only", "upsert", "merge", "replace_selected"}


def _text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _details(query):
    return query.options(
        selectinload(Kontakt.telefone), selectinload(Kontakt.email_adressen),
        selectinload(Kontakt.adressen), selectinload(Kontakt.organisations_funktionen),
        selectinload(Kontakt.organisations_funktionen).selectinload(KontaktOrganisationFunktion.organisation),
        selectinload(Kontakt.externe_referenzen),
    )


def kontakt_payload(kontakt: Kontakt) -> dict[str, Any]:
    return {
        "id": kontakt.id, "version": kontakt.version, "typ": kontakt.typ,
        "anzeigename": kontakt.anzeigename, "anrede": kontakt.anrede,
        "titel_vor": kontakt.titel_vor, "vorname": kontakt.vorname,
        "nachname": kontakt.nachname, "titel_nach": kontakt.titel_nach,
        "aktiv": kontakt.aktiv, "gueltig_ab": str(kontakt.gueltig_ab or "") or None,
        "gueltig_bis": str(kontakt.gueltig_bis or "") or None,
        "datenquelle": kontakt.datenquelle, "externe_quelle_id": kontakt.externe_quelle_id,
        "quellendokument": kontakt.quellendokument, "quellendatum": str(kontakt.quellendatum or "") or None,
        "aktualisiert_am": kontakt.aktualisiert_am.isoformat() if kontakt.aktualisiert_am else None,
        "telefone": [{"id": p.id, "nummer": p.nummer, "nummer_normalisiert": p.nummer_normalisiert, "typ": p.typ, "verwendung": p.verwendung, "label": p.label, "bevorzugt": p.bevorzugt, "sms_eignung": p.sms_eignung, "whatsapp_eignung": p.whatsapp_eignung, "aktiv": p.aktiv, "sortierung": p.sort} for p in kontakt.telefone],
        "email_adressen": [{"id": e.id, "email": e.email, "typ": e.typ, "label": e.label, "bevorzugt": e.bevorzugt, "aktiv": e.aktiv, "sortierung": e.sortierung} for e in kontakt.email_adressen],
        "adressen": [{key: getattr(a, key) for key in ("id", "typ", "strasse", "hausnummer", "adresszusatz", "plz", "ort", "bundesland", "land", "latitude", "longitude", "organisation_id", "bevorzugt", "aktiv")} for a in kontakt.adressen],
        "organisationen": [{"zuordnung_id": f.id, "organisation_id": f.organisation_id, "name": f.organisation.name if f.organisation else None, "kurzname": f.organisation.kurzname if f.organisation else None, "funktion": f.funktion, "funktionskategorie": f.funktionskategorie, "ist_hauptfunktion": f.ist_hauptfunktion, "prioritaet": f.prioritaet, "aktiv": f.aktiv, "erreichbarkeit": f.erreichbarkeit, "vertretung_kontakt_id": f.vertretung_kontakt_id, "bemerkung": f.bemerkung} for f in kontakt.organisations_funktionen],
        "quellen": [{"quelle": r.quelle, "namespace": r.quelle_kontext, "externe_id": r.extern_id} for r in kontakt.externe_referenzen],
    }


def find_match(db: Session, org_id: int, row: dict[str, Any], quelle: str | None) -> tuple[Kontakt | None, list[int]]:
    """Return a safe exact match and separate ambiguous candidate ids."""
    source_id = _text(row.get("externe_id") or row.get("externe_quelle_id"))
    requested_type = _text(row.get("typ"))
    if quelle and source_id:
        ref = db.query(KontaktExterneReferenz).filter_by(org_id=org_id, quelle=quelle, extern_id=source_id).first()
        if ref:
            match = _details(db.query(Kontakt)).filter(Kontakt.id == ref.kontakt_id).first()
            if match and (not requested_type or match.typ == requested_type):
                return match, []
            return None, [ref.kontakt_id]
    if str(row.get("id") or "").isdigit():
        existing = _details(db.query(Kontakt)).filter_by(org_id=org_id, id=int(row["id"])).first()
        if existing and (not requested_type or existing.typ == requested_type):
            return existing, []
    terms = []
    for email in row.get("email_adressen") or []:
        value = _text(email.get("email") if isinstance(email, dict) else email)
        if value:
            terms.append(func.lower(KontaktEmail.email) == value.lower())
    if _text(row.get("email")):
        terms.append(func.lower(Kontakt.email) == _text(row["email"]).lower())
    for phone in row.get("telefone") or []:
        value = _text(phone.get("nummer") if isinstance(phone, dict) else phone)
        if value:
            terms.append(KontaktTelefon.nummer_normalisiert == telefon_normalisiert(value))
    candidates = []
    if terms:
        candidates = _details(db.query(Kontakt)).outerjoin(KontaktEmail).outerjoin(KontaktTelefon).filter(Kontakt.org_id == org_id, Kontakt.archiviert.is_(False), or_(*terms)).distinct().all()
    candidates = [candidate for candidate in candidates if not requested_type or candidate.typ == requested_type]
    # Shared switchboard numbers and functional mailboxes are not identities.
    # Without an external ID, only an exact name corroborates a communication
    # match; otherwise callers must explicitly resolve the candidate.
    name = _text(row.get("anzeigename"))
    if len(candidates) == 1 and name and candidates[0].anzeigename.casefold() == name.casefold():
        return candidates[0], []
    return None, [candidate.id for candidate in candidates]


def upsert_organisation(db: Session, org_id: int, data: dict[str, Any]) -> KontaktOrganisation:
    name = _text(data.get("name"))
    if not name:
        raise ValueError("Organisation braucht einen Namen")
    external_id = _text(data.get("externe_id"))
    query = db.query(KontaktOrganisation).filter_by(org_id=org_id)
    organisation = query.filter_by(externe_id=external_id).first() if external_id else None
    organisation = organisation or query.filter(func.lower(KontaktOrganisation.name) == name.lower()).first()
    if organisation is None:
        organisation = KontaktOrganisation(org_id=org_id, name=name)
        db.add(organisation)
    for field in ("name", "kurzname", "organisationstyp", "externe_id", "quellenreferenz", "website", "notizen", "aktiv"):
        if field in data and data[field] is not None:
            setattr(organisation, field, data[field])
    db.flush()
    return organisation


def _sync_relations(db: Session, kontakt: Kontakt, row: dict[str, Any], org_id: int, replace: bool) -> None:
    if "email_adressen" in row:
        existing = {item.email.lower(): item for item in kontakt.email_adressen}
        for pos, value in enumerate(row["email_adressen"] or []):
            if not isinstance(value, dict) or not _text(value.get("email")):
                continue
            email = _text(value["email"])
            item = existing.pop(email.lower(), None)
            if item is None:
                item = KontaktEmail(org_id=org_id, kontakt_id=kontakt.id, email=email)
                db.add(item)
            for field in ("typ", "label", "bevorzugt", "aktiv"):
                if field in value and value[field] is not None:
                    setattr(item, field, value[field])
            item.sortierung = int(value.get("sortierung", pos))
        if replace:
            for item in existing.values(): db.delete(item)
    if "telefone" in row:
        existing = {item.nummer_normalisiert: item for item in kontakt.telefone}
        for pos, value in enumerate(row["telefone"] or []):
            if not isinstance(value, dict) or not _text(value.get("nummer")):
                continue
            normalized = telefon_normalisiert(_text(value["nummer"]))
            item = existing.pop(normalized, None)
            if item is None:
                item = KontaktTelefon(org_id=org_id, kontakt_id=kontakt.id, nummer=_text(value["nummer"]))
                db.add(item)
            for field in ("nummer", "typ", "verwendung", "label", "bevorzugt", "sms_eignung", "whatsapp_eignung", "aktiv"):
                if field in value and value[field] is not None: setattr(item, field, value[field])
            item.sort = int(value.get("sortierung", value.get("sort", pos)))
        if replace:
            for item in existing.values(): db.delete(item)
    if "adressen" in row:
        if replace:
            for item in list(kontakt.adressen): db.delete(item)
        existing_addresses = {
            (item.typ, item.strasse, item.hausnummer, item.plz, item.ort): item for item in kontakt.adressen
        }
        for value in row["adressen"] or []:
            if not isinstance(value, dict): continue
            key = (value.get("typ", "sonstige"), value.get("strasse"), value.get("hausnummer"), value.get("plz"), value.get("ort"))
            item = existing_addresses.pop(key, None) or KontaktAdresse(org_id=org_id, kontakt_id=kontakt.id)
            for field in ("typ", "strasse", "hausnummer", "adresszusatz", "plz", "ort", "bundesland", "land", "latitude", "longitude", "organisation_id", "bevorzugt", "aktiv"):
                if field in value: setattr(item, field, value[field])
            db.add(item)
    if "organisationen" in row:
        for value in row["organisationen"] or []:
            if not isinstance(value, dict): continue
            organisation = upsert_organisation(db, org_id, value) if not value.get("organisation_id") else db.get(KontaktOrganisation, value["organisation_id"])
            if organisation is None or not _text(value.get("funktion")): continue
            present = next((f for f in kontakt.organisations_funktionen if f.organisation_id == organisation.id and f.funktion == value["funktion"]), None)
            if present is None:
                present = KontaktOrganisationFunktion(org_id=org_id, kontakt_id=kontakt.id, organisation_id=organisation.id, funktion=value["funktion"])
                db.add(present)
            for field in ("funktionskategorie", "ist_hauptfunktion", "prioritaet", "aktiv", "erreichbarkeit", "bemerkung"):
                if field in value and value[field] is not None: setattr(present, field, value[field])


def upsert_contact(db: Session, org_id: int, user_id: int | None, row: dict[str, Any], *, quelle: str | None, modus: str = "merge", quellendokument: str | None = None, quellendatum: str | None = None) -> tuple[str, Kontakt | None, list[int]]:
    if modus not in VALID_MODES: raise ValueError("Unbekannter Importmodus")
    existing, candidates = find_match(db, org_id, row, quelle)
    if candidates: return "DUPLICATE_CANDIDATE", None, candidates
    if existing is None and modus == "update_only": return "UNCHANGED", None, []
    if existing is not None and modus == "create_only": return "UNCHANGED", existing, []
    created = existing is None
    kontakt = existing or Kontakt(org_id=org_id, erstellt_von_id=user_id, aktualisiert_von_id=user_id, anzeigename="")
    if created: db.add(kontakt)
    scalar = ("typ", "anzeigename", "anrede", "titel_vor", "vorname", "nachname", "titel_nach", "funktion", "organisation", "email", "erreichbarkeit", "notizen", "aktiv", "datenquelle", "externe_quelle_id")
    changed = created
    for field in scalar:
        if field not in row or row[field] is None: continue
        if getattr(kontakt, field) != row[field]:
            setattr(kontakt, field, row[field]); changed = True
    if quelle and not kontakt.datenquelle:
        kontakt.datenquelle = quelle
        changed = True
    if quellendokument and not kontakt.quellendokument:
        kontakt.quellendokument = quellendokument
        changed = True
    if quellendatum and not kontakt.quellendatum:
        try:
            kontakt.quellendatum = date.fromisoformat(quellendatum)
            changed = True
        except ValueError:
            raise ValueError("quellendatum muss ISO-8601 (YYYY-MM-DD) sein") from None
    if not kontakt.anzeigename:
        kontakt.anzeigename = " ".join(x for x in (kontakt.nachname, kontakt.vorname) if x) or "Unbenannter Kontakt"
    db.flush()
    _sync_relations(db, kontakt, row, org_id, modus == "replace_selected")
    if quelle and _text(row.get("externe_id") or row.get("externe_quelle_id")):
        external_id = _text(row.get("externe_id") or row.get("externe_quelle_id"))
        ref = db.query(KontaktExterneReferenz).filter_by(org_id=org_id, quelle=quelle, extern_id=external_id).first()
        if ref is None: db.add(KontaktExterneReferenz(org_id=org_id, kontakt_id=kontakt.id, quelle=quelle, extern_id=external_id))
    if not created and changed:
        kontakt.version += 1; kontakt.aktualisiert_von_id = user_id
    kontakt.aktualisiert_ueber = "MCP"
    kontakt.aktualisiert_am = datetime.now(UTC)
    db.flush()
    return ("NEW" if created else "UPDATE" if changed else "UNCHANGED"), kontakt, []
