"""Targeted tests for the hash-only Gruppenkommandant access service."""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.tenant import set_tenant_context
from app.models.major_incident import LageEinheit, LageEinheitLeader, LageEinheitZugang
from app.models.master import FireDept, OrgSettings
from app.services import gk_zugang_service as service
from tests.conftest import TestingSession


@contextmanager
def _session():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _daten(db, *, pin=False, maximum=2):
    org = FireDept(slug=f"gk-zugang-{id(db)}", name="GK Zugang", color="#123456", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, gk_zugang_aktiv=True, gk_zugang_sms_pin=pin, gk_zugang_max_sitzungen=maximum))
    from app.models.major_incident import MajorIncident

    lage = MajorIncident(org_id=org.id, name="Zugangslage", status="active")
    db.add(lage)
    db.flush()
    einheit = LageEinheit(lage_id=lage.id, label="RLF", status="bereitgestellt", resource_type="fahrzeug")
    db.add(einheit)
    db.flush()
    leader = LageEinheitLeader(
        einheit_id=einheit.id,
        person_name="Max Muster",
        phone_e164="+436641234567",
        phone_version=1,
        start_at=service._now(),
        rolle="fuehrer",
    )
    db.add(leader)
    db.flush()
    einheit.leader_assignment_id = leader.id
    db.flush()
    return org, lage, einheit, leader


def _ausgestellt(db, **kwargs):
    org, lage, einheit, leader = _daten(db, **kwargs)
    neu = service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="test")
    db.flush()
    return org, lage, einheit, leader, neu


def test_ausstellung_speichert_nur_hash_und_rotation_invalidiert_sitzung():
    with _session() as db:
        _, lage, einheit, _, first = _ausgestellt(db)
        token1 = first.link.rsplit("#", 1)[1]
        cookie, _ = service.sitzung_anlegen(
            db, db.get(LageEinheitZugang, first.zugang_id), user_agent=None, ip=None, verifiziert=True
        )
        db.flush()
        second = service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="rotation")
        db.flush()
        assert service.token_pruefen(db, token1).zustand == "unbekannt"
        assert service.token_pruefen(db, second.link.rsplit("#", 1)[1]).zustand == "ok"
        assert service.sitzung_pruefen(db, cookie) is None
        assert db.query(LageEinheitZugang).filter_by(einheit_id=einheit.id).count() == 1
        assert second.generation == first.generation + 1


def test_einheit_zugang_ist_eindeutig():
    with _session() as db:
        org, lage, einheit, _, _ = _ausgestellt(db)
        db.add(LageEinheitZugang(org_id=org.id, lage_id=lage.id, einheit_id=einheit.id, status="kein_token"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


@pytest.mark.parametrize(
    ("aenderung", "grund"),
    [
        (lambda db, z, s, lage, e: setattr(s, "revoked_at", service._now()), service.ZugangFehlergrund.WIDERRUFEN),
        (
            lambda db, z, s, lage, e: setattr(s, "laeuft_ab_at", service._now() - timedelta(seconds=1)),
            service.ZugangFehlergrund.ABGELAUFEN,
        ),
        (lambda db, z, s, lage, e: setattr(s, "generation", z.generation + 1), service.ZugangFehlergrund.WIDERRUFEN),
        (lambda db, z, s, lage, e: setattr(z, "status", "widerrufen"), service.ZugangFehlergrund.WIDERRUFEN),
        (
            lambda db, z, s, lage, e: setattr(z, "laeuft_ab_at", service._now() - timedelta(seconds=1)),
            service.ZugangFehlergrund.ABGELAUFEN,
        ),
        (lambda db, z, s, lage, e: setattr(lage, "status", "closed"), service.ZugangFehlergrund.WIDERRUFEN),
        (lambda db, z, s, lage, e: setattr(e, "status", "abgerueckt"), service.ZugangFehlergrund.ABGELAUFEN),
        (lambda db, z, s, lage, e: setattr(e, "leader_assignment_id", None), service.ZugangFehlergrund.WIDERRUFEN),
        (
            lambda db, z, s, lage, e: setattr(
                db.get(LageEinheitLeader, z.leader_id), "phone_version", z.phone_version + 1
            ),
            service.ZugangFehlergrund.WIDERRUFEN,
        ),
        (
            lambda db, z, s, lage, e: setattr(
                db.query(OrgSettings).filter_by(org_id=z.org_id).one(), "gk_zugang_aktiv", False
            ),
            service.ZugangFehlergrund.WIDERRUFEN,
        ),
    ],
)
def test_sitzung_pruefen_mit_grund_deckt_bindung_und_ablauf_ab(aenderung, grund):
    with _session() as db:
        _, lage, einheit, _, neu = _ausgestellt(db)
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        cookie, sitzung = service.sitzung_anlegen(db, zugang, user_agent=None, ip=None, verifiziert=True)
        db.flush()
        aenderung(db, zugang, sitzung, lage, einheit)
        assert service.sitzung_pruefen_mit_grund(db, cookie) == (None, grund)


def test_null_leader_und_pin_und_org_wechsel_haben_sichere_gruende():
    with _session() as db:
        _, _, _, _, neu = _ausgestellt(db, pin=True)
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        cookie, sitzung = service.sitzung_anlegen(db, zugang, user_agent=None, ip=None, verifiziert=False)
        db.flush()
        assert service.sitzung_pruefen_mit_grund(db, cookie) == (None, service.ZugangFehlergrund.UNGUELTIG)
        sitzung.verifiziert_at = service._now()
        zugang.leader_id = None
        assert service.sitzung_pruefen_mit_grund(db, cookie) == (None, service.ZugangFehlergrund.WIDERRUFEN)


def test_sitzungslimit_und_verlaengern_ohne_rotation():
    with _session() as db:
        _, _, einheit, _, neu = _ausgestellt(db, maximum=2)
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        first, first_row = service.sitzung_anlegen(db, zugang, user_agent=None, ip=None, verifiziert=True)
        db.flush()
        service.sitzung_anlegen(db, zugang, user_agent=None, ip=None, verifiziert=True)
        db.flush()
        service.sitzung_anlegen(db, zugang, user_agent=None, ip=None, verifiziert=True)
        db.flush()
        assert first_row.revoked_at is not None
        old_token, old_until = zugang.token_hash, zugang.laeuft_ab_at
        service.verlaengere(db, einheit.id, None)
        assert zugang.token_hash == old_token and zugang.laeuft_ab_at > old_until


def test_nachricht_und_sms_helfer_sind_strikt():
    with _session() as db:
        _, lage, einheit, leader, _ = _ausgestellt(db)
        lage.is_exercise, lage.name, leader.person_name = True, "A\nB", "M\nN"
        rendered = service.nachricht_rendern(
            service.OrgEinstellungen(gk_zugang_nachricht="{lage} {gruppenkommandant} {link}"),
            lage,
            einheit,
            leader,
            "https://x/gk#token",
        )
        assert rendered.startswith("[UEBUNG] ") and "A B" in rendered and "M N" in rendered
        for text in ("{foo}{link}", "kein Link", "{lage:>10}{link}", "{lage.__class__}{link}", "x" * 481):
            with pytest.raises(ValueError):
                service.nachricht_validieren(text)
        assert service.sms_laenge("äöüß") == (4, "GSM-7", 1)
        assert service.sms_laenge("€" * 81) == (162, "GSM-7", 2)
        assert service.sms_laenge("—" * 71) == (71, "UCS-2", 2)
        assert service.schwaerze_pin("PIN 123456") == "PIN ******"
