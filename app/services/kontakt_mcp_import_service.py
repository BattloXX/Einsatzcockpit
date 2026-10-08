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
from sqlalchemy.sql.elements import ColumnElement

from app.core.telefon import telefon_normalisiert
from app.models.kontakt import (
    Kontakt,
    KontaktAdresse,
    KontaktEmail,
    KontaktExterneReferenz,
    KontaktOrganisation,
    KontaktOrganisationFunktion,
    KontaktTelefon,
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
        "id": kontakt.id,
        "version": kontakt.version,
        "typ": kontakt.typ,
        "anzeigename": kontakt.anzeigename,
        "anrede": kontakt.anrede,
        "titel_vor": kontakt.titel_vor,
        "vorname": kontakt.vorname,
        "nachname": kontakt.nachname,
        "titel_nach": kontakt.titel_nach,
        "aktiv": kontakt.aktiv,
        "gueltig_ab": str(kontakt.gueltig_ab or "") or None,
        "gueltig_bis": str(kontakt.gueltig_bis or "") or None,
        "datenquelle": kontakt.datenquelle,
        "externe_quelle_id": kontakt.externe_quelle_id,
        "quellendokument": kontakt.quellendokument,
        "quellendatum": str(kontakt.quellendatum or "") or None,
        "aktualisiert_am": kontakt.aktualisiert_am.isoformat() if kontakt.aktualisiert_am else None,
        "telefone": [
            {
                "id": phone.id, "nummer": phone.nummer, "nummer_normalisiert": phone.nummer_normalisiert,
                "typ": phone.typ, "verwendung": phone.verwendung, "label": phone.label,
                "bevorzugt": phone.bevorzugt, "sms_eignung": phone.sms_eignung,
                "whatsapp_eignung": phone.whatsapp_eignung, "aktiv": phone.aktiv, "sortierung": phone.sort,
            }
            for phone in kontakt.telefone
        ],
        "email_adressen": [
            {
                "id": email_address.id, "email": email_address.email, "typ": email_address.typ,
                "label": email_address.label, "bevorzugt": email_address.bevorzugt,
                "aktiv": email_address.aktiv, "sortierung": email_address.sortierung,
            }
            for email_address in kontakt.email_adressen
        ],
        "adressen": [
            {
                key: getattr(address, key)
                for key in (
                    "id", "typ", "strasse", "hausnummer", "adresszusatz", "plz", "ort", "bundesland",
                    "land", "latitude", "longitude", "organisation_id", "bevorzugt", "aktiv",
                )
            }
            for address in kontakt.adressen
        ],
        "organisationen": [
            {
                "zuordnung_id": function.id, "organisation_id": function.organisation_id,
                "name": function.organisation.name if function.organisation else None,
                "kurzname": function.organisation.kurzname if function.organisation else None,
                "funktion": function.funktion, "funktionskategorie": function.funktionskategorie,
                "ist_hauptfunktion": function.ist_hauptfunktion, "prioritaet": function.prioritaet,
                "aktiv": function.aktiv, "erreichbarkeit": function.erreichbarkeit,
                "vertretung_kontakt_id": function.vertretung_kontakt_id, "bemerkung": function.bemerkung,
            }
            for function in kontakt.organisations_funktionen
        ],
        "quellen": [
            {"quelle": reference.quelle, "namespace": reference.quelle_kontext, "externe_id": reference.extern_id}
            for reference in kontakt.externe_referenzen
        ],
    }


def find_match(
    db: Session, org_id: int, row: dict[str, Any], quelle: str | None
) -> tuple[Kontakt | None, list[int]]:
    """Return a safe exact match and separate ambiguous candidate ids."""
    source_id = _text(row.get("externe_id") or row.get("externe_quelle_id"))
    requested_type = _text(row.get("typ"))
    if quelle and source_id:
        ref = db.query(KontaktExterneReferenz).filter_by(
            org_id=org_id, quelle=quelle, extern_id=source_id
        ).first()
        if ref:
            match = _details(db.query(Kontakt)).filter(Kontakt.id == ref.kontakt_id).first()
            if match and (not requested_type or match.typ == requested_type):
                return match, []
            return None, [ref.kontakt_id]
    if str(row.get("id") or "").isdigit():
        existing = _details(db.query(Kontakt)).filter_by(org_id=org_id, id=int(row["id"])).first()
        if existing and (not requested_type or existing.typ == requested_type):
            return existing, []
    terms: list[ColumnElement[bool]] = []
    for email_value in row.get("email_adressen") or []:
        email = _text(email_value.get("email") if isinstance(email_value, dict) else email_value)
        if email:
            terms.append(func.lower(KontaktEmail.email) == email.lower())
    legacy_email = _text(row.get("email"))
    if legacy_email:
        terms.append(func.lower(Kontakt.email) == legacy_email.lower())
    for phone_value in row.get("telefone") or []:
        phone = _text(phone_value.get("nummer") if isinstance(phone_value, dict) else phone_value)
        if phone:
            terms.append(KontaktTelefon.nummer_normalisiert == telefon_normalisiert(phone))
    candidates = []
    if terms:
        candidates = (
            _details(db.query(Kontakt))
            .outerjoin(KontaktEmail)
            .outerjoin(KontaktTelefon)
            .filter(Kontakt.org_id == org_id, Kontakt.archiviert.is_(False), or_(*terms))
            .distinct()
            .all()
        )
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
    for field in (
        "name", "kurzname", "organisationstyp", "externe_id", "quellenreferenz", "website", "notizen", "aktiv"
    ):
        if field in data and data[field] is not None:
            setattr(organisation, field, data[field])
    db.flush()
    return organisation


def _sync_relations(db: Session, kontakt: Kontakt, row: dict[str, Any], org_id: int, replace: bool) -> None:
    if "email_adressen" in row:
        existing_emails = {email.email.lower(): email for email in kontakt.email_adressen}
        for pos, email_value in enumerate(row["email_adressen"] or []):
            if not isinstance(email_value, dict):
                continue
            email = _text(email_value.get("email"))
            if not email:
                continue
            email_item = existing_emails.pop(email.lower(), None)
            if email_item is None:
                email_item = KontaktEmail(org_id=org_id, kontakt_id=kontakt.id, email=email)
                db.add(email_item)
            for field in ("typ", "label", "bevorzugt", "aktiv"):
                if field in email_value and email_value[field] is not None:
                    setattr(email_item, field, email_value[field])
            sortierung = email_value.get("sortierung", pos)
            email_item.sortierung = int(sortierung) if sortierung is not None else pos
        if replace:
            for email_item in existing_emails.values():
                db.delete(email_item)
    if "telefone" in row:
        existing_phones = {phone.nummer_normalisiert: phone for phone in kontakt.telefone}
        for pos, phone_value in enumerate(row["telefone"] or []):
            if not isinstance(phone_value, dict):
                continue
            number = _text(phone_value.get("nummer"))
            if not number:
                continue
            normalized = telefon_normalisiert(number)
            phone_item = existing_phones.pop(normalized, None)
            if phone_item is None:
                phone_item = KontaktTelefon(org_id=org_id, kontakt_id=kontakt.id, nummer=number)
                db.add(phone_item)
            for field in (
                "nummer", "typ", "verwendung", "label", "bevorzugt", "sms_eignung", "whatsapp_eignung", "aktiv"
            ):
                if field in phone_value and phone_value[field] is not None:
                    setattr(phone_item, field, phone_value[field])
            sortierung = phone_value.get("sortierung", phone_value.get("sort", pos))
            phone_item.sort = int(sortierung) if sortierung is not None else pos
        if replace:
            for phone_item in existing_phones.values():
                db.delete(phone_item)
    if "adressen" in row:
        if replace:
            for address_item in list(kontakt.adressen):
                db.delete(address_item)
        existing_addresses = {
            (address.typ, address.strasse, address.hausnummer, address.plz, address.ort): address
            for address in kontakt.adressen
        }
        for address_value in row["adressen"] or []:
            if not isinstance(address_value, dict):
                continue
            key = (
                address_value.get("typ", "sonstige"), address_value.get("strasse"),
                address_value.get("hausnummer"), address_value.get("plz"), address_value.get("ort"),
            )
            if key in existing_addresses:
                address_item = existing_addresses.pop(key)
            else:
                address_item = KontaktAdresse(org_id=org_id, kontakt_id=kontakt.id)
            for field in (
                "typ", "strasse", "hausnummer", "adresszusatz", "plz", "ort", "bundesland", "land",
                "latitude", "longitude", "organisation_id", "bevorzugt", "aktiv",
            ):
                if field in address_value:
                    setattr(address_item, field, address_value[field])
            db.add(address_item)
    if "organisationen" in row:
        seen_functions: set[tuple[int, str]] = set()
        for organisation_value in row["organisationen"] or []:
            if not isinstance(organisation_value, dict):
                continue
            if organisation_value.get("organisation_id"):
                organisation = db.get(KontaktOrganisation, organisation_value["organisation_id"])
            else:
                organisation = upsert_organisation(db, org_id, organisation_value)
            funktion = _text(organisation_value.get("funktion"))
            if organisation is None or not funktion:
                continue
            seen_functions.add((organisation.id, funktion))
            present = next(
                (
                    function for function in kontakt.organisations_funktionen
                    if function.organisation_id == organisation.id and function.funktion == funktion
                ),
                None,
            )
            if present is None:
                present = KontaktOrganisationFunktion(
                    org_id=org_id, kontakt_id=kontakt.id, organisation_id=organisation.id, funktion=funktion
                )
                db.add(present)
            for field in (
                "funktionskategorie", "ist_hauptfunktion", "prioritaet", "aktiv", "erreichbarkeit", "bemerkung"
            ):
                if field in organisation_value and organisation_value[field] is not None:
                    setattr(present, field, organisation_value[field])
        if replace:
            for item in list(kontakt.organisations_funktionen):
                if (item.organisation_id, item.funktion) not in seen_functions:
                    db.delete(item)


def upsert_contact(
    db: Session,
    org_id: int,
    user_id: int | None,
    row: dict[str, Any],
    *,
    quelle: str | None,
    modus: str = "merge",
    quellendokument: str | None = None,
    quellendatum: str | None = None,
) -> tuple[str, Kontakt | None, list[int]]:
    if modus not in VALID_MODES:
        raise ValueError("Unbekannter Importmodus")
    existing, candidates = find_match(db, org_id, row, quelle)
    if candidates:
        return "DUPLICATE_CANDIDATE", None, candidates
    if existing is None and modus == "update_only":
        return "UNCHANGED", None, []
    if existing is not None and modus == "create_only":
        return "UNCHANGED", existing, []
    created = existing is None
    kontakt = existing or Kontakt(
        org_id=org_id, erstellt_von_id=user_id, aktualisiert_von_id=user_id, anzeigename=""
    )
    if created:
        db.add(kontakt)
    scalar = (
        "typ", "anzeigename", "anrede", "titel_vor", "vorname", "nachname", "titel_nach", "funktion",
        "organisation", "email", "erreichbarkeit", "notizen", "aktiv", "datenquelle", "externe_quelle_id",
    )
    changed = created
    for field in scalar:
        if field not in row or row[field] is None:
            continue
        if getattr(kontakt, field) != row[field]:
            setattr(kontakt, field, row[field])
            changed = True
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
        ref = db.query(KontaktExterneReferenz).filter_by(
            org_id=org_id, quelle=quelle, extern_id=external_id
        ).first()
        if ref is None:
            db.add(
                KontaktExterneReferenz(
                    org_id=org_id, kontakt_id=kontakt.id, quelle=quelle, extern_id=external_id
                )
            )
    if not created and changed:
        kontakt.version += 1
        kontakt.aktualisiert_von_id = user_id
    kontakt.aktualisiert_ueber = "MCP"
    kontakt.aktualisiert_am = datetime.now(UTC)
    db.flush()
    return ("NEW" if created else "UPDATE" if changed else "UNCHANGED"), kontakt, []
