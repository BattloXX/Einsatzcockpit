"""Sicherer, hash-basierter Zugang für Gruppenkommandanten."""

from __future__ import annotations

import base64
import hashlib
import hmac
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
from app.core.telefon import telefon_anzeige, telefon_maske
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    LageEinheit,
    LageEinheitLeader,
    LageEinheitZugang,
    LageEinheitZugangSession,
    LageEinheitZugangVersand,
    MajorIncident,
)
from app.models.master import OrgSettings
from app.models.sms import SmsLog, SmsLogRecipient
from app.services.broadcast import broadcast_lage
from app.services.exercise_guard import darf_extern
from app.services.sms_service import send_sms, sms_available

TOKEN_PREFIX = "gkz_"
QR_TOKEN_PREFIX = "gkq_"
COOKIE = "ec_gk"
QR_COOKIE = "ec_qr"
STANDARDNACHRICHT = """GSL {lage}
Einheit: {einheit}
Du bist als Gruppenkommandant zugewiesen. Einsätze, Status, Lagemeldungen und Fotos:
{link}
Gilt nur für deine Einheit in dieser Lage. Bitte nicht weitergeben."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def zugang_schluessel() -> bytes:
    """Liefert den unabhängigen HMAC-Schlüssel für ableitbare Zugangstoken."""
    if settings.GSL_ZUGANG_KEY:
        return settings.GSL_ZUGANG_KEY.encode()
    if not (settings.DEBUG or settings.TEST_SYSTEM):
        raise ValueError("GSL_ZUGANG_KEY muss in der Produktionsumgebung gesetzt sein")
    return hmac.new(settings.SECRET_KEY.encode(), b"gsl-zugang-key-fallback", hashlib.sha256).digest()


def token_ableiten(typ: str, zugang_id: int, generation: int) -> str:
    """Leitet einen reproduzierbaren, nicht speicherbaren Zugangstoken ab."""
    if typ not in {"personal", "qr"}:
        raise ValueError("Unbekannter Zugangstyp")
    digest = hmac.new(zugang_schluessel(), f"{typ}|{zugang_id}|{generation}".encode(), hashlib.sha256).digest()
    prefix = TOKEN_PREFIX if typ == "personal" else QR_TOKEN_PREFIX
    return prefix + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def token_neu_berechnen(zugang: LageEinheitZugang) -> str:
    """Berechnet den aktuellen Klartexttoken ausschließlich für SMS/Druck intern neu."""
    return token_ableiten(zugang.typ, zugang.id, zugang.generation)


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
    gk_qr_aktiv: bool = False
    gk_qr_gueltigkeit_stunden: int = 72
    gk_qr_pin: bool = False


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


@dataclass(frozen=True)
class AutoSmsAuftrag:
    """Kurzlebiger Auftrag; der Klartext-Link darf nie die Datenbank erreichen."""

    zugang_id: int
    versand_id: int
    link: str
    leader_id: int
    phone_version: int
    lage_id: int
    einheit_id: int
    org_id: int

    def __repr__(self) -> str:
        return (
            "AutoSmsAuftrag("
            f"zugang_id={self.zugang_id}, versand_id={self.versand_id}, "
            f"link={schwaerze_link(self.link)!r}, leader_id={self.leader_id}, "
            f"phone_version={self.phone_version}, lage_id={self.lage_id}, "
            f"einheit_id={self.einheit_id}, org_id={self.org_id})"
        )


class ZugangFehlergrund(StrEnum):
    WIDERRUFEN = "widerrufen"
    ABGELAUFEN = "abgelaufen"
    UNGUELTIG = "ungueltig"


class SitzungAktiv(Exception):
    def __init__(self, zuletzt_aktiv: datetime):
        self.zuletzt_aktiv = zuletzt_aktiv
        super().__init__("Aktive Gruppenkommandanten-Sitzung")


@dataclass(frozen=True)
class VersandErgebnis:
    status: str
    fehler: str | None
    generation: int | None


@dataclass(frozen=True)
class KopieErgebnis:
    link: str
    text: str
    laeuft_ab_at: datetime
    generation: int


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
    leader: LageEinheitLeader | None
    org_id: int
    typ: str = "personal"
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
    zugang = (
        db.query(LageEinheitZugang)
        .filter(LageEinheitZugang.einheit_id == einheit.id, LageEinheitZugang.typ == "personal")
        .with_for_update()
        .first()
    )
    if zugang is None:
        try:
            with db.begin_nested():
                zugang = LageEinheitZugang(
                    org_id=lage.org_id,
                    lage_id=lage.id,
                    einheit_id=einheit.id,
                    typ="personal",
                    phone_version=leader.phone_version,
                    status="kein_token",
                    created_at=now,
                    updated_at=now,
                )
                db.add(zugang)
                db.flush()
        except IntegrityError:
            zugang = (
                db.query(LageEinheitZugang)
                .filter(LageEinheitZugang.einheit_id == einheit.id, LageEinheitZugang.typ == "personal")
                .with_for_update()
                .one()
            )
    assert zugang is not None
    zugang.vorheriger_token_hash = zugang.token_hash or zugang.vorheriger_token_hash
    zugang.generation += 1
    raw = token_ableiten("personal", zugang.id, zugang.generation)
    zugang.token_hash = hash_api_key(raw)
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


def qr_pin_ableiten(zugang_id: int, generation: int) -> str:
    """Deterministischer QR-PIN; er wird weder gespeichert noch protokolliert."""
    digest = hmac.new(zugang_schluessel(), f"qrpin|{zugang_id}|{generation}".encode(), hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"


def qr_pin_fuer_fuehrung(zugang: LageEinheitZugang) -> str | None:
    """Gibt den QR-PIN ausschließlich für eine autorisierte Führungsansicht aus."""
    return qr_pin_ableiten(zugang.id, zugang.generation) if zugang.typ == "qr" and zugang.pin_pflicht else None


def stelle_qr_zugang_aus(
    db: Session, lage: MajorIncident, einheit: LageEinheit, *, user_id: int | None, grund: str, neu: bool = False
) -> NeuerZugang:
    cfg = org_einstellungen(db, lage.org_id)
    if not cfg.gk_qr_aktiv:
        raise ValueError("QR-Zugang ist für diese Organisation deaktiviert")
    if lage.status != "active":
        raise ValueError("Zugang kann nur für eine aktive Lage ausgestellt werden")
    if einheit.status == "abgerueckt":
        raise ValueError("Für abgerückte Einheiten kann kein Zugang ausgestellt werden")
    now = _now()
    zugang = db.query(LageEinheitZugang).filter(
        LageEinheitZugang.einheit_id == einheit.id, LageEinheitZugang.typ == "qr"
    ).with_for_update().first()
    if (zugang and not neu and zugang.status == "aktiv" and zugang.token_hash and zugang.laeuft_ab_at
            and zugang.laeuft_ab_at > now):
        return NeuerZugang(link_fuer(token_neu_berechnen(zugang)), zugang.generation, zugang.laeuft_ab_at, zugang.id)
    if zugang is None:
        try:
            with db.begin_nested():
                zugang = LageEinheitZugang(org_id=lage.org_id, lage_id=lage.id, einheit_id=einheit.id, typ="qr",
                                            status="kein_token", created_at=now, updated_at=now)
                db.add(zugang)
                db.flush()
        except IntegrityError:
            zugang = db.query(LageEinheitZugang).filter(
                LageEinheitZugang.einheit_id == einheit.id, LageEinheitZugang.typ == "qr"
            ).with_for_update().one()
    assert zugang is not None
    zugang.vorheriger_token_hash = zugang.token_hash or zugang.vorheriger_token_hash
    zugang.generation += 1
    raw = token_ableiten("qr", zugang.id, zugang.generation)
    zugang.token_hash = hash_api_key(raw)
    zugang.status, zugang.laeuft_ab_at = "aktiv", now + timedelta(hours=max(1, min(168, cfg.gk_qr_gueltigkeit_stunden)))
    zugang.pin_pflicht = cfg.gk_qr_pin
    zugang.qr_pin_pflicht = cfg.gk_qr_pin
    zugang.pin_hash = hash_api_key(qr_pin_ableiten(zugang.id, zugang.generation)) if cfg.gk_qr_pin else None
    zugang.pin_gueltig_bis, zugang.pin_versuche, zugang.pin_gesperrt_bis = None, 0, None
    zugang.widerruf_grund, zugang.widerrufen_at, zugang.widerrufen_von = None, None, None
    zugang.ausgestellt_at, zugang.ausgestellt_von, zugang.updated_at = now, user_id, now
    _revoke_sessions(db, zugang.id, "rotation", now)
    write_audit(db, "gsl.zugang.qr_ausgestellt", org_id=lage.org_id, user_id=user_id,
                entity_type="lage_einheit", entity_id=einheit.id,
                payload={"lage_id": lage.id, "einheit_id": einheit.id, "generation": zugang.generation, "grund": grund})
    _journal(db, lage, einheit, f"{einheit.label}: QR-Zugang ausgestellt", user_id)
    return NeuerZugang(link_fuer(raw), zugang.generation, zugang.laeuft_ab_at, zugang.id)


def widerrufe(db: Session, einheit_id: int, *, grund: str, user_id: int | None = None, typ: str | None = None) -> None:
    rows = db.query(LageEinheitZugang).filter(LageEinheitZugang.einheit_id == einheit_id)
    if typ is not None:
        rows = rows.filter(LageEinheitZugang.typ == typ)
    zugeaenge = rows.with_for_update().all()
    if not zugeaenge:
        return
    for zugang in zugeaenge:
        _widerrufe_einen(db, zugang, grund, user_id)


def _widerrufe_einen(db: Session, zugang: LageEinheitZugang, grund: str, user_id: int | None) -> None:
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
    einheit = db.get(LageEinheit, zugang.einheit_id)
    write_audit(
        db,
        "gsl.zugang.widerrufen",
        org_id=zugang.org_id,
        user_id=user_id,
        entity_type="lage_einheit",
        entity_id=zugang.einheit_id,
        payload={
            "lage_id": zugang.lage_id,
            "einheit_id": zugang.einheit_id,
            "zugang_id": zugang.id,
            "generation": zugang.generation,
            "grund": grund,
        },
    )
    if lage and einheit:
        name = "QR-Zugang" if zugang.typ == "qr" else "Gruppenkommandanten-Zugang"
        _journal(db, lage, einheit, f"{einheit.label}: {name} widerrufen ({grund})", user_id)


def widerrufe_alle_fuer_lage(db: Session, lage_id: int, grund: str) -> None:
    for row in db.query(LageEinheitZugang).filter(LageEinheitZugang.lage_id == lage_id).all():
        widerrufe(db, row.einheit_id, grund=grund, typ=row.typ)


def widerrufe_alle_fuer_org(db: Session, org_id: int, grund: str = "manuell") -> int:
    """Widerruft alle Zugänge einer Organisation und gibt deren Anzahl zurück."""
    anzahl = 0
    for row in db.query(LageEinheitZugang).filter(LageEinheitZugang.org_id == org_id).all():
        widerrufe(db, row.einheit_id, grund=grund, typ=row.typ)
        anzahl += 1
    return anzahl


def verlaengere(db: Session, einheit_id: int, user_id: int | None) -> LageEinheitZugang:
    zugang = (
        db.query(LageEinheitZugang)
        .filter(LageEinheitZugang.einheit_id == einheit_id, LageEinheitZugang.typ == "personal")
        .with_for_update()
        .first()
    )
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


def token_pruefen(db: Session, token: str, typ: str = "personal") -> TokenPruefung:
    if typ not in {"personal", "qr"} or (token.startswith(QR_TOKEN_PREFIX) and typ != "qr"):
        return TokenPruefung("unbekannt", None)
    set_tenant_context(db, None)
    zugang = (
        db.query(LageEinheitZugang)
        .execution_options(include_all_tenants=True)
        .filter(LageEinheitZugang.token_hash == hash_api_key(token), LageEinheitZugang.typ == typ)
        .first()
    )
    if not zugang:
        ersetzt = (
            db.query(LageEinheitZugang)
            .execution_options(include_all_tenants=True)
            .filter(LageEinheitZugang.vorheriger_token_hash == hash_api_key(token), LageEinheitZugang.typ == typ)
            .first()
        )
        return TokenPruefung("beendet", ersetzt) if ersetzt else TokenPruefung("unbekannt", None)
    prefix = TOKEN_PREFIX if typ == "personal" else QR_TOKEN_PREFIX
    # Alte Zufallstoken werden weiter ausschließlich über ihren gespeicherten
    # Hash geprüft. Ein Token in der Länge des neuen HMAC-Formats muss dagegen
    # auch mit dem aktuell konfigurierten Schlüssel ableitbar sein.
    if token.startswith(prefix) and len(token) == len(prefix) + 43 and not hmac.compare_digest(
        token, token_neu_berechnen(zugang)
    ):
        return TokenPruefung("unbekannt", None)
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
        typ=zugang.typ,
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
    db: Session, cookie_wert: str, typ: str = "personal"
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
        .filter(LageEinheitZugangSession.session_hash == hash_api_key(cookie_wert), LageEinheitZugangSession.typ == typ)
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
    if not einheit or not lage or (typ == "personal" and not leader):
        session.revoked_at, session.revoke_grund = now, "ungueltig"
        return None, ZugangFehlergrund.WIDERRUFEN
    invalid_binding = (
        session.typ != typ
        or zugang.typ != typ
        or session.generation != zugang.generation
        or zugang.status != "aktiv"
        or not zugang.token_hash
        or (typ == "personal" and (einheit.leader_assignment_id != zugang.leader_id or leader is None
            or leader.end_at is not None or leader.phone_e164 != zugang.phone_e164
            or leader.phone_version != zugang.phone_version))
        or (typ == "personal" and not org_einstellungen(db, zugang.org_id).gk_zugang_aktiv)
        or (typ == "qr" and not org_einstellungen(db, zugang.org_id).gk_qr_aktiv)
        or lage.org_id != zugang.org_id
    )
    if invalid_binding:
        session.revoked_at, session.revoke_grund = now, "ungueltig"
        return None, ZugangFehlergrund.WIDERRUFEN
    if lage.status != "active":
        # A closed Lage is explicitly revoked by close_lage(); retain the same
        # externally visible reason if this validator observes it first.
        return None, ZugangFehlergrund.WIDERRUFEN
    if not zugang.laeuft_ab_at or zugang.laeuft_ab_at <= now or einheit.status not in {"bereitgestellt", "im_einsatz"}:
        return None, ZugangFehlergrund.ABGELAUFEN
    if zugang.pin_pflicht and session.verifiziert_at is None:
        return None, ZugangFehlergrund.UNGUELTIG
    if not zugang.letzte_aktivitaet_at or zugang.letzte_aktivitaet_at <= now - timedelta(minutes=1):
        zugang.letzte_aktivitaet_at = now
        session.last_seen_at = now
    return ZugangPrincipal(zugang, session, einheit, lage, leader, zugang.org_id, typ=typ), None


def sitzung_pruefen(db: Session, cookie_wert: str, typ: str = "personal") -> ZugangPrincipal | None:
    """Prüft eine GK-Sitzung und liefert nur den Principal für bestehende Aufrufer."""
    principal, _ = sitzung_pruefen_mit_grund(db, cookie_wert, typ)
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


def _mask_phone(phone: str) -> str:
    return ("*" * max(0, len(phone) - 4)) + phone[-4:]


def _token_aus_link(link: str | None) -> str | None:
    if not link:
        return None
    match = re.fullmatch(re.escape(settings.effective_public_base_url.rstrip("/")) + r"/gk#([A-Za-z0-9_-]+)", link)
    return match.group(1) if match else None


def _aktuelle_sitzung(db: Session, zugang: LageEinheitZugang) -> datetime | None:
    since = _now() - timedelta(minutes=30)
    row = (
        db.query(LageEinheitZugangSession)
        .filter(
            LageEinheitZugangSession.zugang_id == zugang.id,
            LageEinheitZugangSession.revoked_at.is_(None),
            LageEinheitZugangSession.last_seen_at >= since,
        )
        .order_by(LageEinheitZugangSession.last_seen_at.desc())
        .first()
    )
    return row.last_seen_at if row else None


def _versand(
    db: Session,
    zugang: LageEinheitZugang,
    einheit: LageEinheit,
    *,
    kanal: str,
    ausloeser: str,
    user_id: int | None,
    status: str,
    fehler: str | None = None,
) -> LageEinheitZugangVersand:
    row = LageEinheitZugangVersand(
        org_id=zugang.org_id,
        zugang_id=zugang.id,
        einheit_id=einheit.id,
        leader_id=zugang.leader_id,
        generation=zugang.generation,
        kanal=kanal,
        ausloeser=ausloeser,
        user_id=user_id,
        status=status,
        fehler=fehler,
        created_at=_now(),
        abgeschlossen_at=_now() if status != "geplant" else None,
    )
    db.add(row)
    return row


def _zugang_ohne_token(
    db: Session, lage: MajorIncident, einheit: LageEinheit, leader: LageEinheitLeader
) -> LageEinheitZugang:
    """Legt nur die FK-Basis für ein übersprungenes Protokoll an, ohne Tokenrotation."""
    zugang = (
        db.query(LageEinheitZugang)
        .filter(LageEinheitZugang.einheit_id == einheit.id, LageEinheitZugang.typ == "personal")
        .first()
    )
    if zugang:
        return zugang
    now = _now()
    zugang = LageEinheitZugang(
        org_id=lage.org_id,
        lage_id=lage.id,
        einheit_id=einheit.id,
        typ="personal",
        leader_id=leader.id,
        phone_e164=leader.phone_e164,
        phone_version=leader.phone_version,
        status="kein_token",
        created_at=now,
        updated_at=now,
    )
    db.add(zugang)
    db.flush()
    return zugang


def plane_auto_sms(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    leader: LageEinheitLeader,
    aenderung: str,
) -> AutoSmsAuftrag | None:
    """Erzeugt Outbox und Zugang atomar, versendet aber bewusst noch nicht."""
    cfg = org_einstellungen(db, lage.org_id)
    if not (cfg.gk_zugang_aktiv and cfg.gk_zugang_auto_sms) or aenderung not in {"neu", "wechsel", "telefon"}:
        return None
    if not leader.phone_e164:
        zugang = _zugang_ohne_token(db, lage, einheit, leader)
        versand = _versand(
            db, zugang, einheit, kanal="sms", ausloeser="auto", user_id=None,
            status="uebersprungen", fehler="Keine Telefonnummer",
        )
        # A previously revoked access still names the former leader; this log is
        # about the current assignment and must not alter its token state.
        versand.leader_id = leader.id
        return None
    auto_schluessel = f"{leader.id}:{leader.phone_version}"
    if db.query(LageEinheitZugangVersand.id).filter(
        LageEinheitZugangVersand.auto_schluessel == auto_schluessel
    ).first():
        return None
    try:
        with db.begin_nested():
            # The savepoint also rolls back the rotation if a concurrent writer won.
            neu = stelle_zugang_aus(db, lage, einheit, user_id=None, grund="auto")
            ausgestellter_zugang = db.get(LageEinheitZugang, neu.zugang_id)
            assert ausgestellter_zugang is not None
            versand = _versand(
                db, ausgestellter_zugang, einheit, kanal="sms", ausloeser="auto", user_id=None, status="geplant"
            )
            versand.auto_schluessel = auto_schluessel
            db.flush()
            return AutoSmsAuftrag(
                zugang_id=ausgestellter_zugang.id, versand_id=versand.id, link=neu.link,
                leader_id=leader.id, phone_version=leader.phone_version,
                lage_id=lage.id, einheit_id=einheit.id, org_id=lage.org_id,
            )
    except IntegrityError:
        return None


def _kurzer_fehler(exc: Exception | None) -> str:
    # Provider-Details can contain arbitrary data; deliberately do not persist them.
    return "SMS-Versand fehlgeschlagen" if exc else "SMS konnte nicht zugestellt werden"


async def sende_zugangs_sms(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    user_id: int | None,
    ausloeser: str,
    bestehender_link: str | None = None,
    bestaetigt: bool = False,
) -> VersandErgebnis:
    cfg = org_einstellungen(db, lage.org_id)
    leader = db.get(LageEinheitLeader, einheit.leader_assignment_id) if einheit.leader_assignment_id else None
    if not cfg.gk_zugang_aktiv:
        raise ValueError("Gruppenkommandanten-Zugang ist für diese Organisation deaktiviert")
    if not leader or leader.end_at is not None or not leader.phone_e164:
        raise ValueError("Aktueller Gruppenkommandant benötigt eine gültige Telefonnummer")
    zugang = (
        db.query(LageEinheitZugang)
        .filter(LageEinheitZugang.einheit_id == einheit.id, LageEinheitZugang.typ == "personal")
        .first()
    )
    if not sms_available(lage.org_id, db):
        zugang = zugang or _zugang_ohne_token(db, lage, einheit, leader)
        _versand(
            db,
            zugang,
            einheit,
            kanal="sms",
            ausloeser=ausloeser,
            user_id=user_id,
            status="uebersprungen",
            fehler="Kein SMS-Anbieter verbunden",
        )
        db.commit()
        return VersandErgebnis("uebersprungen", "Kein SMS-Anbieter verbunden", zugang.generation)
    if not darf_extern("sms", is_exercise=lage.is_exercise, org_id=lage.org_id, db=db):
        zugang = zugang or _zugang_ohne_token(db, lage, einheit, leader)
        _versand(
            db,
            zugang,
            einheit,
            kanal="sms",
            ausloeser=ausloeser,
            user_id=user_id,
            status="uebersprungen",
            fehler="Übung: SMS unterdrückt",
        )
        db.commit()
        return VersandErgebnis("uebersprungen", "Übung: SMS unterdrückt", zugang.generation)
    if zugang and not bestaetigt:
        active = _aktuelle_sitzung(db, zugang)
        if active:
            raise SitzungAktiv(active)
    token = _token_aus_link(bestehender_link)
    reuse = bool(token and zugang and zugang.token_hash == hash_api_key(token) and zugang.status == "aktiv")
    neu = None if reuse else stelle_zugang_aus(db, lage, einheit, user_id=user_id, grund="rotation")
    if neu:
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
    assert zugang is not None
    row = _versand(db, zugang, einheit, kanal="sms", ausloeser=ausloeser, user_id=user_id, status="geplant")
    db.commit()
    link = bestehender_link if reuse else neu.link  # type: ignore[union-attr]
    assert link is not None
    text = nachricht_rendern(cfg, lage, einheit, leader, link)
    chars, _encoding, segments = sms_laenge(text)
    now = _now()
    result = None
    try:
        result = await send_sms(lage.org_id, leader.phone_e164, text, timeout=15)
        success = bool(getattr(result, "success", result is True))
        status = "gesendet" if success else "fehlgeschlagen"
        error = None if success else _kurzer_fehler(None)
    except TimeoutError:
        success, status, error = False, "unklar", "Ergebnis unbekannt – bitte beim Gruppenkommandanten nachfragen"
    except Exception as exc:  # provider exceptions are intentionally not exposed
        success, status, error = False, "fehlgeschlagen", _kurzer_fehler(exc)
    log = SmsLog(
        org_id=lage.org_id,
        source="gk_zugang",
        text=schwaerze_link(text),
        recipient_count=1,
        success_count=1 if success else 0,
        provider=getattr(result, "provider", None),
        triggered_by_user_id=user_id,
        completed_at=now,
    )
    log.recipients.append(
        SmsLogRecipient(
            phone_number=leader.phone_e164,
            name="Gruppenkommandant",
            success=success,
            sent_at=now,
            provider=getattr(result, "provider", None),
        )
    )
    db.add(log)
    row.status, row.fehler, row.ziel_maske, row.zeichen, row.segmente, row.abgeschlossen_at = (
        status,
        error,
        _mask_phone(leader.phone_e164),
        chars,
        segments,
        now,
    )
    db.flush()
    row.sms_log_id = log.id
    write_audit(
        db,
        f"gsl.zugang.sms_{'auto' if ausloeser == 'auto' else 'manuell'}",
        org_id=lage.org_id,
        user_id=user_id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_id": einheit.id, "generation": zugang.generation, "status": status},
    )
    db.commit()
    return VersandErgebnis(status, error, zugang.generation)


async def sende_auto_sms(auftrag: AutoSmsAuftrag) -> None:
    """Versendet einen bereits committeten Auto-Auftrag ohne Wiederholung."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(LageEinheitZugangVersand).filter(
            LageEinheitZugangVersand.id == auftrag.versand_id,
            LageEinheitZugangVersand.org_id == auftrag.org_id,
        ).first()
        if not row or row.status != "geplant":
            return
        lage = db.query(MajorIncident).filter(
            MajorIncident.id == auftrag.lage_id, MajorIncident.org_id == auftrag.org_id
        ).first()
        einheit = db.query(LageEinheit).filter(
            LageEinheit.id == auftrag.einheit_id, LageEinheit.lage_id == auftrag.lage_id
        ).first()
        leader = db.query(LageEinheitLeader).filter(LageEinheitLeader.id == auftrag.leader_id).first()
        zugang = db.query(LageEinheitZugang).filter(
            LageEinheitZugang.id == auftrag.zugang_id, LageEinheitZugang.org_id == auftrag.org_id
        ).first()
        token = _token_aus_link(auftrag.link)
        cfg = org_einstellungen(db, auftrag.org_id)
        valid = bool(
            lage and einheit and leader and zugang and token and lage.status == "active"
            and cfg.gk_zugang_aktiv and cfg.gk_zugang_auto_sms
            and einheit.leader_assignment_id == leader.id and leader.end_at is None
            and leader.phone_version == auftrag.phone_version and zugang.status == "aktiv"
            and leader.einheit_id == einheit.id and zugang.einheit_id == einheit.id
            and row.generation == zugang.generation
            and zugang.token_hash == hash_api_key(token)
        )
        if not valid:
            row.status, row.fehler, row.abgeschlossen_at = (
                "verworfen", "Gruppenkommandant oder Nummer inzwischen geaendert", _now()
            )
            db.commit()
            return
        assert lage is not None and einheit is not None and leader is not None and zugang is not None
        phone = leader.phone_e164
        assert phone is not None
        if not sms_available(auftrag.org_id, db):
            row.status, row.fehler, row.abgeschlossen_at = "uebersprungen", "Kein SMS-Anbieter verbunden", _now()
            db.commit()
            return
        if not darf_extern("sms", is_exercise=lage.is_exercise, org_id=auftrag.org_id, db=db):
            row.status, row.fehler, row.abgeschlossen_at = "uebersprungen", "Übung: SMS unterdrückt", _now()
            db.commit()
            return
        text = nachricht_rendern(cfg, lage, einheit, leader, auftrag.link)
        chars, _encoding, segments = sms_laenge(text)
        now, result = _now(), None
        try:
            result = await send_sms(auftrag.org_id, phone, text, timeout=15)
            success = bool(getattr(result, "success", result is True))
            status, error = ("gesendet", None) if success else ("fehlgeschlagen", _kurzer_fehler(None))
        except TimeoutError:
            success, status, error = False, "unklar", "Ergebnis unbekannt – bitte beim Gruppenkommandanten nachfragen"
        except Exception:
            success, status, error = False, "fehlgeschlagen", _kurzer_fehler(Exception())
        log = SmsLog(org_id=auftrag.org_id, source="gk_zugang", text=schwaerze_link(text), recipient_count=1,
                     success_count=1 if success else 0, provider=getattr(result, "provider", None), completed_at=now)
        log.recipients.append(SmsLogRecipient(phone_number=phone, name="Gruppenkommandant", success=success,
                                              sent_at=now, provider=getattr(result, "provider", None)))
        db.add(log)
        row.status, row.fehler, row.ziel_maske, row.zeichen, row.segmente, row.abgeschlossen_at = (
            status, error, _mask_phone(phone), chars, segments, now)
        db.flush()
        row.sms_log_id = log.id
        write_audit(db, "gsl.zugang.sms_auto", org_id=auftrag.org_id, user_id=None, entity_type="lage_einheit",
                    entity_id=einheit.id, payload={"lage_id": lage.id, "einheit_id": einheit.id,
                                                     "generation": zugang.generation, "status": status})
        db.commit()
    except Exception:
        # Do not expose provider errors (they can contain recipient or message data).
        try:
            row = db.query(LageEinheitZugangVersand).filter(
                LageEinheitZugangVersand.id == auftrag.versand_id, LageEinheitZugangVersand.org_id == auftrag.org_id
            ).first()
            if row and row.status == "geplant":
                row.status, row.fehler, row.abgeschlossen_at = "fehlgeschlagen", "SMS-Versand fehlgeschlagen", _now()
                db.commit()
        except Exception:
            db.rollback()
    finally:
        try:
            await broadcast_lage(auftrag.lage_id, {"type": "ressource:changed", "einheit_id": auftrag.einheit_id})
        finally:
            db.close()


async def gk_versand_aufraeum_loop() -> None:
    """Markiert nach Prozessabbrüchen liegen gebliebene Auto-Outbox-Einträge."""
    import asyncio

    while True:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            aufraeumen_haengende_versaende(db)
        except Exception:
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(60)


def kopie_ausstellen(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    user_id: int | None,
    modus: str,
    bestehender_link: str | None = None,
    bestaetigt: bool = False,
) -> KopieErgebnis:
    if modus not in {"nachricht", "link"}:
        raise ValueError("Ungültiger Kopiermodus")
    cfg = org_einstellungen(db, lage.org_id)
    leader = db.get(LageEinheitLeader, einheit.leader_assignment_id) if einheit.leader_assignment_id else None
    if not cfg.gk_zugang_aktiv or not leader or not leader.phone_e164:
        raise ValueError("Aktueller Gruppenkommandant benötigt eine gültige Telefonnummer")
    zugang = (
        db.query(LageEinheitZugang)
        .filter(LageEinheitZugang.einheit_id == einheit.id, LageEinheitZugang.typ == "personal")
        .first()
    )
    if zugang and not bestaetigt:
        active = _aktuelle_sitzung(db, zugang)
        if active:
            raise SitzungAktiv(active)
    token = _token_aus_link(bestehender_link)
    reuse = bool(token and zugang and zugang.token_hash == hash_api_key(token) and zugang.status == "aktiv")
    neu = None if reuse else stelle_zugang_aus(db, lage, einheit, user_id=user_id, grund="rotation")
    if neu:
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
    assert zugang is not None
    link = bestehender_link if reuse else neu.link  # type: ignore[union-attr]
    assert link is not None
    text = nachricht_rendern(cfg, lage, einheit, leader, link)
    _versand(db, zugang, einheit, kanal=f"kopie_{modus}", ausloeser="manuell", user_id=user_id, status="gesendet")
    write_audit(
        db,
        "gsl.zugang.link_kopiert",
        org_id=lage.org_id,
        user_id=user_id,
        entity_type="lage_einheit",
        entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_id": einheit.id, "generation": zugang.generation, "modus": modus},
    )
    db.commit()
    assert zugang.laeuft_ab_at is not None
    return KopieErgebnis(link, text if modus == "nachricht" else link, zugang.laeuft_ab_at, zugang.generation)


def aufraeumen_haengende_versaende(db: Session) -> int:
    cutoff = _now() - timedelta(minutes=2)
    rows = (
        db.query(LageEinheitZugangVersand)
        .filter(LageEinheitZugangVersand.status == "geplant", LageEinheitZugangVersand.created_at < cutoff)
        .all()
    )
    for row in rows:
        row.status, row.fehler, row.abgeschlossen_at = (
            "fehlgeschlagen",
            "Versand abgebrochen - bitte erneut senden",
            _now(),
        )
    if rows:
        db.commit()
    return len(rows)


def zugang_status(db: Session, einheit: LageEinheit) -> dict:
    zugang = (
        db.query(LageEinheitZugang)
        .filter(LageEinheitZugang.einheit_id == einheit.id, LageEinheitZugang.typ == "personal")
        .first()
    )
    leader = db.get(LageEinheitLeader, einheit.leader_assignment_id) if einheit.leader_assignment_id else None
    lage = db.get(MajorIncident, einheit.lage_id)
    if not lage or not org_einstellungen(db, lage.org_id).gk_zugang_aktiv:
        state = "deaktiviert"
    elif not leader or not leader.phone_e164:
        state = "keine_nummer"
    elif not zugang:
        state = "kein_zugang"
    elif zugang.status == "widerrufen":
        state = "widerrufen"
    elif not zugang.laeuft_ab_at or zugang.laeuft_ab_at <= _now():
        state = "abgelaufen"
    elif zugang.laeuft_ab_at <= _now() + timedelta(hours=2):
        state = "laeuft_bald_ab"
    else:
        state = "aktiv"
    sends = (
        []
        if not zugang
        else db.query(LageEinheitZugangVersand)
        .filter(LageEinheitZugangVersand.zugang_id == zugang.id)
        .order_by(LageEinheitZugangVersand.created_at.desc())
        .limit(10)
        .all()
    )
    sessions = (
        []
        if not zugang
        else db.query(LageEinheitZugangSession)
        .filter(LageEinheitZugangSession.zugang_id == zugang.id, LageEinheitZugangSession.revoked_at.is_(None))
        .all()
    )

    def send_data(v):
        return {
            k: getattr(v, k) for k in ("kanal", "status", "fehler", "zeichen", "segmente", "created_at", "ausloeser")
        }

    qr = db.query(LageEinheitZugang).filter(
        LageEinheitZugang.einheit_id == einheit.id, LageEinheitZugang.typ == "qr"
    ).first()
    qr_cfg = bool(lage and org_einstellungen(db, lage.org_id).gk_qr_aktiv)
    if not qr_cfg:
        qr_state = "deaktiviert"
    elif not qr:
        qr_state = "kein_zugang"
    elif qr.status == "widerrufen":
        qr_state = "widerrufen"
    elif not qr.laeuft_ab_at or qr.laeuft_ab_at <= _now():
        qr_state = "abgelaufen"
    else:
        qr_state = "aktiv"
    return {
        "zustand": state,
        "laeuft_ab_at": zugang.laeuft_ab_at if zugang else None,
        "generation": zugang.generation if zugang else None,
        "widerruf_grund": zugang.widerruf_grund if zugang else None,
        "widerrufen_at": zugang.widerrufen_at if zugang else None,
        "aktive_sitzungen": [
            {"id": s.id, "client_kurz": s.client_kurz, "last_seen_at": s.last_seen_at, "created_at": s.created_at}
            for s in sessions
        ],
        "letzte_aktivitaet_at": zugang.letzte_aktivitaet_at if zugang else None,
        "letzter_versand": send_data(sends[0]) if sends else None,
        "versandprotokoll": [send_data(s) for s in sends],
        "sitzung_aktiv": bool(zugang and zugang.token_hash and _aktuelle_sitzung(db, zugang)),
        "nummer_anzeige": telefon_anzeige(leader.phone_e164) if leader and leader.phone_e164 else None,
        "nummer_maske": telefon_maske(leader.phone_e164) if leader and leader.phone_e164 else None,
        "qr": {"status": qr_state, "laeuft_ab_at": qr.laeuft_ab_at if qr else None,
               "generation": qr.generation if qr else None, "pin_pflicht": bool(qr and qr.pin_pflicht),
               "sitzung_aktiv": bool(qr and qr.token_hash and _aktuelle_sitzung(db, qr)),
               "qr_druck_at": qr.qr_druck_at if qr else None},
    }
