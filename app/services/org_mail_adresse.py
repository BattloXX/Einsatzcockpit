"""Sichere, zentrale Adressbildung fuer Organisations-E-Mails."""
from __future__ import annotations

import re
import unicodedata

from app.config import settings
from app.models.master import FireDept, OrgSettings, SystemSettings

_DOMAIN_RE = re.compile(
    r"(?=.{4,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)


def ist_gueltige_domain(value: str) -> bool:
    """Prueft eine Domain, die sicher in eine Mailadresse eingesetzt werden darf."""
    return bool(_DOMAIN_RE.fullmatch(value.strip().lower()))


def typ_lokalteil(bos: str) -> str:
    """Normalisiert eine BOS-Bezeichnung fuer den lokalen Teil einer Adresse."""
    value = (bos or "").strip().lower()
    value = (value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "org"


def _slug_lokalteil(slug: str) -> str:
    return typ_lokalteil(slug)


def lokalteil(org: FireDept) -> str:
    return f"{typ_lokalteil(org.bos)}.{_slug_lokalteil(org.slug)}"


def _adresse(lokal: str, domain: str) -> str:
    domain = domain.strip().lower()
    if not ist_gueltige_domain(domain):
        raise ValueError("Ungueltige Maildomain")
    return f"{lokal}@{domain}"


def absender_adresse(org: FireDept, from_domain: str) -> str:
    return _adresse(lokalteil(org), from_domain)


def empfangs_adresse(org: FireDept, inbound_domain: str) -> str:
    return _adresse(lokalteil(org), inbound_domain)


def alias_lokalteile(org: FireDept) -> list[str]:
    return [_slug_lokalteil(org.slug)]


def inbound_domain(db) -> str:
    """Liest die zentrale Empfangsdomain, mit sicherem Env-Fallback."""
    value = ""
    if db is not None:
        try:
            row = db.query(SystemSettings).filter_by(key="resend_inbound_domain").first()
            value = (row.value if row and row.value else "").strip().lower()
        except Exception:
            value = ""
    if ist_gueltige_domain(value):
        return value
    value = settings.MAIL_INBOUND_DOMAIN.strip().lower()
    return value if ist_gueltige_domain(value) else "get.einsatzcockpit.com"


def org_aus_adresse(db, adresse: str, inbound_domain: str) -> FireDept | None:
    """Ordnet eine Empfangsadresse genau einer aktiven Organisation zu."""
    if not ist_gueltige_domain(inbound_domain) or not isinstance(adresse, str):
        return None
    candidate = adresse.strip().lower()
    if candidate.count("@") != 1 or any(char in candidate for char in "\r\n<>,;\""):
        return None
    local, domain = candidate.rsplit("@", 1)
    if domain != inbound_domain.strip().lower() or not local:
        return None
    orgs = db.query(FireDept).filter(FireDept.is_active.is_(True), FireDept.deleted_at.is_(None)).all()
    typed = [org for org in orgs if lokalteil(org).lower() == local]
    if len(typed) == 1:
        return typed[0]
    if typed:
        return None
    aliases = [org for org in orgs if local in {item.lower() for item in alias_lokalteile(org)}]
    return aliases[0] if len(aliases) == 1 else None


def mail_eingang_aktiv(db, org_id: int) -> bool:
    """Ob fuer die Organisation Eingang und Reply-To aktiviert sind."""
    row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return bool(row and row.mail_eingang_modus != "aus")
