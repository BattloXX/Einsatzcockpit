"""Request-freie QR-Zugänge für Einsätze und Großschadenslagen."""
from __future__ import annotations

import hashlib
from urllib.parse import urlencode

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import sign_lage_qr_token, sign_qr_token
from app.models.incident import Incident, IncidentToken
from app.models.major_incident import IncidentSite, LageToken, MajorIncident, MajorIncidentStatus
from app.models.user import Role, User, UserRole


def get_or_create_qr_principal(db: Session, org_id: int) -> User:
    """Liefert den langlebigen, nicht interaktiv anmeldbaren QR-Benutzer einer Org."""
    benutzername = f"qr-druck@org{org_id}"
    benutzer = db.query(User).filter(User.username == benutzername).first()
    if benutzer is None:
        benutzer = User(
            username=benutzername,
            display_name="QR-Zugang (Druck)",
            org_id=org_id,
            is_device=True,
            active=True,
            password_hash=None,
            auth_provider="local",
        )
        db.add(benutzer)
        db.flush()
    rolle = db.query(Role).filter(Role.code == "recorder").first()
    if rolle is None:
        raise RuntimeError("Rolle recorder fehlt")
    if not any(zuordnung.role_id == rolle.id for zuordnung in benutzer.user_roles):
        db.add(UserRole(user_id=benutzer.id, role_id=rolle.id))
        db.flush()
    return benutzer


def _aussteller(db: Session, org_id: int, issuing_user_id: int | None) -> User:
    benutzer = (
        db.get(User, issuing_user_id)
        if issuing_user_id is not None
        else get_or_create_qr_principal(db, org_id)
    )
    if benutzer is None or not benutzer.active or benutzer.org_id != org_id:
        raise ValueError("QR-Aussteller gehört nicht zur Organisation")
    return benutzer


def einsatz_qr_login_url(
    db: Session, incident: Incident, *, issuing_user_id: int | None = None
) -> str | None:
    if incident.status != "active":
        return None
    org_id = incident.primary_org_id
    if org_id is None:
        raise ValueError("Einsatz hat keine primäre Organisation")
    aussteller = _aussteller(db, org_id, issuing_user_id)
    if incident.primary_org_id != aussteller.org_id:
        raise ValueError("Einsatz und QR-Aussteller gehören nicht zur selben Organisation")
    token = sign_qr_token(incident.id, aussteller.id)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    vorhanden = db.query(IncidentToken).filter(
        IncidentToken.incident_id == incident.id,
        IncidentToken.token_hash == token_hash,
        IncidentToken.revoked_at.is_(None),
    ).first()
    if vorhanden is None:
        try:
            with db.begin_nested():
                db.add(IncidentToken(
                    incident_id=incident.id,
                    token_hash=token_hash,
                    issued_by_user_id=aussteller.id,
                ))
                db.flush()
        except IntegrityError:
            vorhanden = db.query(IncidentToken).filter(
                IncidentToken.token_hash == token_hash,
                IncidentToken.incident_id == incident.id,
            ).first()
            if vorhanden is None:
                raise
    db.commit()
    basis = settings.effective_public_base_url.rstrip("/")
    return f"{basis}/qr-login?{urlencode({'incident_id': incident.id, 'token': token})}"


def lage_qr_login_url(
    db: Session,
    lage: MajorIncident,
    *,
    issuing_user_id: int | None = None,
    site_id: int | None = None,
) -> str | None:
    if lage.status != MajorIncidentStatus.active:
        return None
    aussteller = _aussteller(db, lage.org_id, issuing_user_id)
    if lage.org_id != aussteller.org_id:
        raise ValueError("Lage und QR-Aussteller gehören nicht zur selben Organisation")
    normalisierte_site_id: int | None = None
    if site_id is not None:
        site = db.get(IncidentSite, site_id)
        if site is None or site.major_incident_id != lage.id:
            raise ValueError("Einsatzstelle gehört nicht zur Lage")
        normalisierte_site_id = site.id
    token = sign_lage_qr_token(lage.id, aussteller.id)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    vorhanden = db.query(LageToken).filter(
        LageToken.lage_id == lage.id,
        LageToken.token_hash == token_hash,
        LageToken.revoked_at.is_(None),
    ).first()
    if vorhanden is None:
        try:
            with db.begin_nested():
                db.add(LageToken(
                    lage_id=lage.id,
                    token_hash=token_hash,
                    issued_by_user_id=aussteller.id,
                ))
                db.flush()
        except IntegrityError:
            vorhanden = db.query(LageToken).filter(
                LageToken.token_hash == token_hash,
                LageToken.lage_id == lage.id,
            ).first()
            if vorhanden is None:
                raise
    db.commit()
    parameter: dict[str, str | int] = {"token": token}
    if normalisierte_site_id is not None:
        parameter["open_site"] = normalisierte_site_id
    basis = settings.effective_public_base_url.rstrip("/")
    return f"{basis}/lage/{lage.id}/qr-login?{urlencode(parameter)}"
