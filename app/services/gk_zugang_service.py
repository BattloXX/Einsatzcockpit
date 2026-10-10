"""Sicherer, hash-basierter Zugang für Gruppenkommandanten."""

from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.models.major_incident import (
    LageEinheit,
    LageEinheitLeader,
    LageEinheitZugang,
    LageEinheitZugangSession,
    MajorIncident,
)
from app.models.master import OrgSettings

TOKEN_PREFIX = "gkz_"
COOKIE = "ec_gk"
STANDARDNACHRICHT = """GSL {lage}
Einheit: {einheit}
Du bist als Gruppenkommandant zugewiesen. Einsätze, Status, Lagemeldungen und Fotos:
{link}
Gilt nur für deine Einheit in dieser Lage. Bitte nicht weitergeben."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def link_fuer(token: str) -> str:
    return settings.effective_public_base_url.rstrip("/") + "/gk#" + token


@dataclass(frozen=True)
class OrgEinstellungen:
    gk_zugang_aktiv: bool = False
    gk_zugang_auto_sms: bool = False
    gk_zugang_nachricht: str | None = None
    gk_zugang_gueltigkeit_stunden: int = 48
    gk_sitzung_stunden: int = 12
    gk_zugang_max_sitzungen: int = 2
    gk_zugang_sms_pin: bool = False
    gk_zugang_ressource_pflegen: bool = False


def org_einstellungen(db: Session, org_id: int) -> OrgEinstellungen:
    row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if row is None:
        return OrgEinstellungen()
    return OrgEinstellungen(**{name: getattr(row, name) for name in OrgEinstellungen.__dataclass_fields__})


@dataclass
class NeuerZugang:
    link: str
    generation: int
    laeuft_ab_at: datetime
    zugang_id: int

    def __repr__(self) -> str:
        return (
            "NeuerZugang("
            f"link={schwaerze_link(self.link)!r}, generation={self.generation}, "
            f"laeuft_ab_at={self.laeuft_ab_at!r}, zugang_id={self.zugang_id})"
        )


class ZugangFehlergrund(StrEnum):
    WIDERRUFEN = "widerrufen"
    ABGELAUFEN = "abgelaufen"
    UNGUELTIG = "ungueltig"


@dataclass
class TokenPruefung:
    zustand: str
    zugang: LageEinheitZugang | None


@dataclass
class ZugangPrincipal:
    zugang: LageEinheitZugang
    session: LageEinheitZugangSession
    einheit: LageEinheit
    lage: MajorIncident
    leader: LageEinheitLeader
    org_id: int
    fehlergrund: ZugangFehlergrund | None = None


def _revoke_sessions(db: Session, zugang_id: int, grund: str, now: datetime) -> None:
    for session in (
        db.query(LageEinheitZugangSession)
        .filter(LageEinheitZugangSession.zugang_id == zugang_id, LageEinheitZugangSession.revoked_at.is_(None))
        .all()
    ):
        session.revoked_at = now
        session.revoke_grund = grund


def _journal(db: Session, lage: MajorIncident, einheit: LageEinheit, text: str, user_id: int | None = None) -> None:
    from app.services.resource_service import _journal as resource_journal

    resource_journal(
        db,
        lage.id,
        text,
        category="ressource",
        user_id=user_id,
        einheit_id=einheit.id,
        ereignis_typ="zugang",
        quelle="system",
    )


def stelle_zugang_aus(
    db: Session, lage: MajorIncident, einheit: LageEinheit, *, user_id: int | None, grund: str
) -> NeuerZugang:
    cfg = org_einstellungen(db, lage.org_id)
    leader = db.get(LageEinheitLeader, einheit.leader_assignment_id) if einheit.leader_assignment_id else None
    if not cfg.gk_zugang_aktiv:
        raise ValueError("Gruppenkommandanten-Zugang ist für diese Organisation deaktiviert")
    if not leader or leader.end_at is not None or not leader.phone_e164:
        raise ValueError("Aktueller Gruppenkommandant benötigt eine gültige Telefonnummer")
    if lage.status != "active":
        raise ValueError("Zugang kann nur für eine aktive Lage ausgestellt werden")
    if einheit.status == "abgerueckt":
        raise ValueError("Für abgerückte Einheiten kann kein Zugang ausgestellt werden")
    now = _now()
    zugang = db.query(LageEinheitZugang).filter(LageEinheitZugang.einheit_id == einheit.id).with_for_update().first()
    if zugang is None:
        try:
            with db.begin_nested():
                zugang = LageEinheitZugang(
                    org_id=lage.org_id,
                    lage_id=lage.id,
                    einheit_id=einheit.id,
                    phone_version=leader.phone_version,
                    status="kein_token",
                    created_at=now,
                    updated_at=now,
                )
                db.add(zugang)
                db.flush()
        except IntegrityError:
            zugang = (
                db.query(LageEinheitZugang).filter(LageEinheitZugang.einheit_id == einheit.id).with_for_update().one()
            )
    assert zugang is not None
    raw = TOKEN_PREFIX + secrets.token_urlsafe(24)
    zugang.vorheriger_token_hash = zugang.token_hash or zugang.vorheriger_token_hash
    zugang.token_hash = hash_api_key(raw)
    zugang.generation += 1
    zugang.status = "aktiv"
    zugang.laeuft_ab_at = now + timedelta(hours=max(1, min(168, cfg.gk_zugang_gueltigkeit_stunden)))
    zugang.leader_id, zugang.phone_e164, zugang.phone_version = leader.id, leader.phone_e164, leader.phone_version
    zugang.pin_pflicht = cfg.gk_zugang_sms_pin
    zugang.pin_hash = None
    zugang.pin_gueltig_bis = None
    zugang.pin_versuche = 0
    zugang.pin_gesperrt_bis = None
    zugang.widerruf_grund = None
    zugang.widerrufen_at = None
    zugang.widerrufen_von = None
    zugang.ausgestellt_at, zugang.ausgestellt_von, zugang.updated_at = now, user_id, now
    _revoke_sessions(db, zugang.id, "rotation", now)
    write_audit(
        db,
        "gsl.zugang.ausgestellt",
        org_id=lage.org_id,
        user_id=user_id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_id": einheit.id, "generation": zugang.generation, "grund": grund},
    )
    _journal(db, lage, einheit, f"{einheit.label}: Gruppenkommandanten-Zugang ausgestellt", user_id)
    return NeuerZugang(link_fuer(raw), zugang.generation, zugang.laeuft_ab_at, zugang.id)


def widerrufe(db: Session, einheit_id: int, *, grund: str, user_id: int | None = None) -> None:
    zugang = db.query(LageEinheitZugang).filter(LageEinheitZugang.einheit_id == einheit_id).with_for_update().first()
    if not zugang:
        return
    now = _now()
    zugang.vorheriger_token_hash = zugang.token_hash or zugang.vorheriger_token_hash
    zugang.token_hash = None
    zugang.status = "widerrufen"
    zugang.widerruf_grund = grund
    zugang.widerrufen_at = now
    zugang.widerrufen_von = user_id
    zugang.generation += 1
    zugang.updated_at = now
    _revoke_sessions(db, zugang.id, grund, now)
    lage = db.get(MajorIncident, zugang.lage_id)
    einheit = db.get(LageEinheit, einheit_id)
    write_audit(
        db,
        "gsl.zugang.widerrufen",
        org_id=zugang.org_id,
        user_id=user_id,
        entity_type="lage_einheit",
        entity_id=einheit_id,
        payload={
            "lage_id": zugang.lage_id,
            "einheit_id": einheit_id,
            "zugang_id": zugang.id,
            "generation": zugang.generation,
            "grund": grund,
        },
    )
    if lage and einheit:
        _journal(db, lage, einheit, f"{einheit.label}: Gruppenkommandanten-Zugang widerrufen ({grund})", user_id)


def widerrufe_alle_fuer_lage(db: Session, lage_id: int, grund: str) -> None:
    for row in db.query(LageEinheitZugang).filter(LageEinheitZugang.lage_id == lage_id).all():
        widerrufe(db, row.einheit_id, grund=grund)


def widerrufe_alle_fuer_org(db: Session, org_id: int) -> None:
    for row in db.query(LageEinheitZugang).filter(LageEinheitZugang.org_id == org_id).all():
        widerrufe(db, row.einheit_id, grund="manuell")


def verlaengere(db: Session, einheit_id: int, user_id: int | None) -> LageEinheitZugang:
    zugang = db.query(LageEinheitZugang).filter(LageEinheitZugang.einheit_id == einheit_id).with_for_update().first()
    if not zugang:
        raise ValueError("Kein Zugang vorhanden")
    if zugang.org_id is None:
        raise ValueError("Zugang ohne Organisation")
    zugang.laeuft_ab_at = _now() + timedelta(hours=org_einstellungen(db, zugang.org_id).gk_zugang_gueltigkeit_stunden)
    write_audit(
        db,
        "gsl.zugang.verlaengert",
        org_id=zugang.org_id,
        user_id=user_id,
        entity_type="lage_einheit",
        entity_id=einheit_id,
        payload={"zugang_id": zugang.id, "generation": zugang.generation},
    )
    return zugang


def token_pruefen(db: Session, token: str) -> TokenPruefung:
    set_tenant_context(db, None)
    zugang = (
        db.query(LageEinheitZugang)
        .execution_options(include_all_tenants=True)
        .filter(LageEinheitZugang.token_hash == hash_api_key(token))
        .first()
    )
    if not zugang:
        ersetzt = (
            db.query(LageEinheitZugang)
            .execution_options(include_all_tenants=True)
            .filter(LageEinheitZugang.vorheriger_token_hash == hash_api_key(token))
            .first()
        )
        return TokenPruefung("beendet", ersetzt) if ersetzt else TokenPruefung("unbekannt", None)
    if zugang.status != "aktiv" or not zugang.token_hash:
        return TokenPruefung("beendet", zugang)
    if not zugang.laeuft_ab_at or zugang.laeuft_ab_at <= _now():
        return TokenPruefung("abgelaufen", zugang)
    return TokenPruefung("ok", zugang)


def _ip_gruppe(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        value = ipaddress.ip_address(ip)
        return str(ipaddress.ip_network(f"{value}/{'24' if value.version == 4 else '56'}", strict=False))
    except ValueError:
        return None


def sitzung_anlegen(
    db: Session, zugang: LageEinheitZugang, *, user_agent: str | None, ip: str | None, verifiziert: bool
) -> tuple[str, LageEinheitZugangSession]:
    if zugang.org_id is None:
        raise ValueError("Zugang ohne Organisation")
    cfg = org_einstellungen(db, zugang.org_id)
    now = _now()
    active = (
        db.query(LageEinheitZugangSession)
        .filter(
            LageEinheitZugangSession.zugang_id == zugang.id,
            LageEinheitZugangSession.revoked_at.is_(None),
            LageEinheitZugangSession.laeuft_ab_at > now,
        )
        .order_by(LageEinheitZugangSession.created_at)
        .all()
    )
    # Reserve one slot for the new session; revoke the oldest excess sessions.
    for old in active[: max(0, len(active) - cfg.gk_zugang_max_sitzungen + 1)]:
        old.revoked_at, old.revoke_grund = now, "sitzungslimit"
    raw = secrets.token_urlsafe(32)
    session = LageEinheitZugangSession(
        org_id=zugang.org_id,
        zugang_id=zugang.id,
        generation=zugang.generation,
        session_hash=hash_api_key(raw),
        created_at=now,
        last_seen_at=now,
        laeuft_ab_at=min(zugang.laeuft_ab_at or now, now + timedelta(hours=cfg.gk_sitzung_stunden)),
        client_kurz=(user_agent or "")[:120],
        ip_gruppe=_ip_gruppe(ip),
        verifiziert_at=now if verifiziert else None,
    )
    db.add(session)
    return raw, session


def sitzung_pruefen_mit_grund(
    db: Session, cookie_wert: str
) -> tuple[ZugangPrincipal | None, ZugangFehlergrund | None]:
    """Prüft eine GK-Sitzung und liefert bei Fehlern einen sicheren Grund.

    Bindungsänderungen (einschließlich einer geschlossenen Lage) werden als
    ``WIDERRUFEN`` behandelt; zeitliche Abläufe als ``ABGELAUFEN``. Ein
    unbekanntes Cookie oder eine fehlende PIN-Verifikation ist ``UNGUELTIG``.
    """
    set_tenant_context(db, None)
    now = _now()
    session = (
        db.query(LageEinheitZugangSession)
        .execution_options(include_all_tenants=True)
        .filter(LageEinheitZugangSession.session_hash == hash_api_key(cookie_wert))
        .first()
    )
    if not session:
        return None, ZugangFehlergrund.UNGUELTIG
    if session.revoked_at:
        return None, ZugangFehlergrund.WIDERRUFEN
    if session.laeuft_ab_at <= now:
        return None, ZugangFehlergrund.ABGELAUFEN
    zugang = (
        db.query(LageEinheitZugang)
        .execution_options(include_all_tenants=True)
        .filter(LageEinheitZugang.id == session.zugang_id)
        .first()
    )
    if not zugang:
        return None, ZugangFehlergrund.UNGUELTIG
    if zugang.org_id is None or zugang.org_id != session.org_id:
        session.revoked_at, session.revoke_grund = now, "ungueltig"
        return None, ZugangFehlergrund.WIDERRUFEN
    einheit = db.get(LageEinheit, zugang.einheit_id)
    lage = db.get(MajorIncident, zugang.lage_id)
    leader = db.get(LageEinheitLeader, zugang.leader_id) if zugang.leader_id is not None else None
    if not einheit or not lage or not leader:
        session.revoked_at, session.revoke_grund = now, "ungueltig"
        return None, ZugangFehlergrund.WIDERRUFEN
    invalid_binding = (
        session.generation != zugang.generation
        or zugang.status != "aktiv"
        or not zugang.token_hash
        or einheit.leader_assignment_id != zugang.leader_id
        or leader.end_at is not None
        or leader.phone_e164 != zugang.phone_e164
        or leader.phone_version != zugang.phone_version
        or not org_einstellungen(db, zugang.org_id).gk_zugang_aktiv
        or lage.org_id != zugang.org_id
    )
    if invalid_binding:
        session.revoked_at, session.revoke_grund = now, "ungueltig"
        return None, ZugangFehlergrund.WIDERRUFEN
    if lage.status != "active":
        # A closed Lage is explicitly revoked by close_lage(); retain the same
        # externally visible reason if this validator observes it first.
        return None, ZugangFehlergrund.WIDERRUFEN
    if (
        not zugang.laeuft_ab_at
        or zugang.laeuft_ab_at <= now
        or einheit.status not in {"bereitgestellt", "im_einsatz"}
    ):
        return None, ZugangFehlergrund.ABGELAUFEN
    if zugang.pin_pflicht and session.verifiziert_at is None:
        return None, ZugangFehlergrund.UNGUELTIG
    if not zugang.letzte_aktivitaet_at or zugang.letzte_aktivitaet_at <= now - timedelta(minutes=1):
        zugang.letzte_aktivitaet_at = now
        session.last_seen_at = now
    return ZugangPrincipal(zugang, session, einheit, lage, leader, zugang.org_id), None


def sitzung_pruefen(db: Session, cookie_wert: str) -> ZugangPrincipal | None:
    """Prüft eine GK-Sitzung und liefert nur den Principal für bestehende Aufrufer."""
    principal, _ = sitzung_pruefen_mit_grund(db, cookie_wert)
    return principal


def _clean(value: object, length: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:length]


def nachricht_validieren(text: str) -> None:
    keys = set(re.findall(r"\{([^{}]+)\}", text))
    allowed = {"lage", "einheit", "gruppenkommandant", "link"}
    if keys - allowed:
        raise ValueError("Unbekannte Platzhalter in Nachricht")
    if "{link}" not in text:
        raise ValueError("{link} ist erforderlich")
    if len(text) > 480:
        raise ValueError("Nachricht darf höchstens 480 Zeichen haben")


def nachricht_rendern(
    cfg: OrgEinstellungen, lage: MajorIncident, einheit: LageEinheit, leader: LageEinheitLeader, link: str
) -> str:
    template = cfg.gk_zugang_nachricht or STANDARDNACHRICHT
    nachricht_validieren(template)
    text = template.format(
        lage=_clean(lage.name, 40),
        einheit=_clean(einheit.label, 30),
        gruppenkommandant=_clean(leader.display_name, 40),
        link=link,
    )
    return ("[UEBUNG] " if lage.is_exercise else "") + text


def sms_laenge(text: str) -> tuple[int, str, int]:
    gsm = set(
        "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüßà"
    )
    extended = set("€[]{}\\^~|")
    if all(char in gsm or char in extended for char in text):
        chars = sum(2 if char in extended else 1 for char in text)
        return chars, "GSM-7", 1 if chars <= 160 else (chars + 152) // 153
    chars = len(text)
    return chars, "UCS-2", 1 if chars <= 70 else (chars + 66) // 67


def schwaerze_link(text: str) -> str:
    base = re.escape(settings.effective_public_base_url.rstrip("/"))
    return re.sub(base + r"/gk#[A-Za-z0-9_\-]+", settings.effective_public_base_url.rstrip("/") + "/gk#********", text)


def schwaerze_pin(text: str) -> str:
    return re.sub(r"\b\d{6}\b", "******", text)
