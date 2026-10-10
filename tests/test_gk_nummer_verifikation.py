"""Nummernbestätigung des Gruppenkommandanten per SMS-Code."""

import json
from types import SimpleNamespace

import pytest

from app.models.major_incident import LageEinheitLeader, LageEinheitNummerVerifikation, LageEinheitZugang
from app.models.sms import SmsLog
from app.services import gk_nummer_service as service
from app.services import gk_zugang_service
from tests.test_gk_qr_zugang import _eingeloest
from tests.test_gk_zugang_service import _ausgestellt, _session


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


class _Sms:
    """Fängt den Code ab, ohne einen Provider anzusprechen."""

    def __init__(self, monkeypatch, *, erfolg=True):
        self.texte = []
        self.erfolg = erfolg

        async def senden(org_id, nummer, text):
            self.texte.append((nummer, text))
            return SimpleNamespace(success=self.erfolg, provider="test")

        monkeypatch.setattr(service, "send_sms", senden)
        monkeypatch.setattr(service, "sms_available", lambda org_id, db: True)

    @property
    def code(self):
        return self.texte[-1][1].rsplit(": ", 1)[1]


def _principal(db, *, einheit, lage, leader, zugang=None):
    return SimpleNamespace(einheit=einheit, lage=lage, leader=leader, org_id=lage.org_id, zugang=zugang)


async def _start(db, p, nummer="+436641112233"):
    return await service.starte_verifikation(db, p, nummer)


def test_start_validiert_nummer_und_nur_aktueller_leader(monkeypatch):
    import asyncio

    _Sms(monkeypatch)
    with _session() as db:
        _, lage, einheit, leader, _ = _ausgestellt(db)
        p = _principal(db, einheit=einheit, lage=lage, leader=leader)
        with pytest.raises(ValueError, match="Ungültige Mobilnummer"):
            asyncio.run(_start(db, p, "abc"))
        leader.end_at = gk_zugang_service._now()
        with pytest.raises(ValueError, match="Kein aktueller"):
            asyncio.run(_start(db, p))


def test_erfolg_setzt_nummer_widerruft_personal_und_plant_sms(monkeypatch):
    import asyncio

    sms = _Sms(monkeypatch)
    with _session() as db:
        _, lage, einheit, leader, neu = _ausgestellt(db)
        p = _principal(db, einheit=einheit, lage=lage, leader=leader)
        asyncio.run(_start(db, p))
        version = leader.phone_version
        ergebnis = service.bestaetige_verifikation(db, p, sms.code)
        assert ergebnis.status == "bestaetigt" and "4411" not in (ergebnis.maske or "")
        db.refresh(leader)
        assert leader.phone_e164 == "+436641112233" and leader.phone_verifiziert_at is not None
        assert leader.phone_version == version + 1
        assert db.get(LageEinheitZugang, neu.zugang_id).status == "widerrufen"
        # Code ist verbraucht: zweiter Versuch scheitert.
        with pytest.raises(ValueError):
            service.bestaetige_verifikation(db, p, sms.code)


def test_falscher_code_sperrt_nach_fuenf_versuchen(monkeypatch):
    import asyncio

    sms = _Sms(monkeypatch)
    with _session() as db:
        _, lage, einheit, leader, _ = _ausgestellt(db)
        p = _principal(db, einheit=einheit, lage=lage, leader=leader)
        asyncio.run(_start(db, p))
        falsch = "000000" if sms.code != "000000" else "111111"
        for _ in range(5):
            with pytest.raises(ValueError, match="ungültig"):
                service.bestaetige_verifikation(db, p, falsch)
        with pytest.raises(ValueError, match="Zu viele"):
            service.bestaetige_verifikation(db, p, sms.code)


def test_abgelaufener_code_wird_abgelehnt(monkeypatch):
    import asyncio

    sms = _Sms(monkeypatch)
    with _session() as db:
        _, lage, einheit, leader, _ = _ausgestellt(db)
        p = _principal(db, einheit=einheit, lage=lage, leader=leader)
        asyncio.run(_start(db, p))
        db.query(LageEinheitNummerVerifikation).update({"gueltig_bis": gk_zugang_service._now()})
        with pytest.raises(ValueError, match="abgelaufen"):
            service.bestaetige_verifikation(db, p, sms.code)


def test_rate_limit_drei_codes_je_zehn_minuten(monkeypatch):
    import asyncio

    _Sms(monkeypatch)
    with _session() as db:
        _, lage, einheit, leader, _ = _ausgestellt(db)
        p = _principal(db, einheit=einheit, lage=lage, leader=leader)
        for _ in range(3):
            asyncio.run(_start(db, p))
        with pytest.raises(ValueError, match="Zu viele"):
            asyncio.run(_start(db, p))


@pytest.mark.parametrize("aenderung", ["telefon_durch_fuehrung", "leader_wechsel"])
def test_race_fuehrung_aendert_waehrend_verifikation(monkeypatch, aenderung):
    import asyncio

    sms = _Sms(monkeypatch)
    with _session() as db:
        _, lage, einheit, leader, _ = _ausgestellt(db)
        p = _principal(db, einheit=einheit, lage=lage, leader=leader)
        asyncio.run(_start(db, p))
        if aenderung == "telefon_durch_fuehrung":
            leader.phone_version += 1
        else:
            neu = LageEinheitLeader(
                einheit_id=einheit.id, person_name="Neu", phone_e164="+436649998877", phone_version=1,
                start_at=gk_zugang_service._now(), rolle="fuehrer",
            )
            db.add(neu)
            db.flush()
            einheit.leader_assignment_id = neu.id
        db.flush()
        with pytest.raises(ValueError, match="Führung|Kein aktueller"):
            service.bestaetige_verifikation(db, p, sms.code)
        db.refresh(leader)
        assert leader.phone_verifiziert_at is None


def test_kein_klartext_in_log_audit_journal_und_smslog(monkeypatch):
    import asyncio

    from app.models.user import AuditLog

    sms = _Sms(monkeypatch)
    with _session() as db:
        _, lage, einheit, leader, _ = _ausgestellt(db)
        p = _principal(db, einheit=einheit, lage=lage, leader=leader)
        asyncio.run(_start(db, p))
        code = sms.code
        service.bestaetige_verifikation(db, p, code)
        db.flush()
        dump = json.dumps(
            [
                [a.action + (a.payload_json or "") for a in db.query(AuditLog).all()],
                [s.text for s in db.query(SmsLog).filter(SmsLog.source == "gk_nummer").all()],
            ],
            default=str,
        )
        assert code not in dump and "6641112233" not in dump
        assert "******" in dump


def test_sms_fehler_liefert_verstaendliche_meldung(monkeypatch):
    import asyncio

    _Sms(monkeypatch, erfolg=False)
    with _session() as db:
        _, lage, einheit, leader, _ = _ausgestellt(db)
        p = _principal(db, einheit=einheit, lage=lage, leader=leader)
        with pytest.raises(ValueError, match="nicht gesendet"):
            asyncio.run(_start(db, p))


def test_api_qr_sitzung_zustand_hinweis_und_ablauf(client, monkeypatch):
    sms = _Sms(monkeypatch)
    ids = _eingeloest(client, ohne_leader=False)
    zustand = client.get("/einheit/api/zustand").json()
    assert zustand["nummer"]["hinweis"] is True and zustand["nummer"]["verifiziert"] is False
    headers = {"X-CSRF-Token": ids["csrf"]}
    anfrage = client.post("/einheit/api/nummer/anfordern", json={"telefon": "+436641112233"}, headers=headers)
    assert anfrage.status_code == 200, anfrage.text
    assert "6641112233" not in anfrage.text
    falsch = client.post("/einheit/api/nummer/bestaetigen", json={"code": "999999"}, headers=headers)
    assert falsch.status_code == 422
    ok = client.post("/einheit/api/nummer/bestaetigen", json={"code": sms.code}, headers=headers)
    assert ok.status_code == 200 and ok.json()["ok"] is True and ok.json()["sitzung_beendet"] is False
    zustand = client.get("/einheit/api/zustand").json()
    assert zustand["nummer"]["hinweis"] is False and zustand["nummer"]["verifiziert"] is True


def test_api_geraet_und_ohne_sitzung_sind_gesperrt(client):
    csrf = client.get("/gk").cookies.get("ec_csrf")
    antwort = client.post(
        "/einheit/api/nummer/anfordern", json={"telefon": "+436641112233"}, headers={"X-CSRF-Token": csrf}
    )
    assert antwort.status_code in (401, 403)
