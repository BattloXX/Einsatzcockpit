"""Push-Diagnose: Organisationsfilter, Datenschutz und Empfangsbestaetigungen."""
import json
from datetime import timedelta
from uuid import uuid4

import pytest

from app.admin_mcp.context import admin_session as SessionLocal
from app.admin_mcp.registry import ADMIN_TOOLS, READ_TOOLS, collect
from app.models.admin_security import utcnow
from app.models.master import FireDept
from app.models.user import FcmDeliveryLog, FcmToken, PushLog, User
from app.services import push_service, system_diagnose as diagnose
from tests import test_admin_mcp_oauth as oauth
from tests.test_admin_mcp_tools import call

admin_env = oauth.admin_env


@pytest.fixture
def push_daten(monkeypatch, tmp_path):
    now = utcnow()
    monkeypatch.setattr(diagnose.presence, "utc", lambda value=None: value.replace(tzinfo=None) if value else now)
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}")
    monkeypatch.setattr(push_service, "_push_cfg", lambda db: {
        "fcm_enabled": True, "fcm_project_id": "Test", "fcm_credentials_path": str(credentials)})
    monkeypatch.setattr(push_service, "_get_fcm_app", lambda cfg: object())
    monkeypatch.setattr(push_service, "_fcm_app", None)
    monkeypatch.setattr(push_service, "_fcm_unavailable", False)
    import importlib.util
    original = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: object() if name == "firebase_admin" else original(name, *a))
    ids = []
    secrets = []
    with SessionLocal() as db:
        for index in range(2):
            secret = "PRIVAT-" + uuid4().hex
            secrets.append(secret)
            org = FireDept(slug=uuid4().hex, name=secret)
            db.add(org)
            db.flush()
            user = User(username=secret, display_name=secret, org_id=org.id)
            db.add(user)
            db.flush()
            token = FcmToken(user_id=user.id, token=secret, capabilities="native_alarm" if index == 0 else "nativeXalarm",
                             last_used_at=now - timedelta(days=1 if index == 0 else 8))
            db.add(token)
            db.flush()
            push = PushLog(org_id=org.id, title=secret, body=secret, source="fcm_test_alarm",
                           sent_at=now - timedelta(seconds=120), sent_count=3, total_count=4)
            db.add(push)
            db.flush()
            for success, ack, age in ((True, None, 120), (True, 15, 120), (False, None, 120), (True, None, 30)):
                sent = now - timedelta(seconds=age)
                db.add(FcmDeliveryLog(push_log_id=push.id, fcm_token_id=token.id, user_id=user.id,
                                      success=success, error_code=None if success else "UNREGISTERED",
                                      sent_at=sent, delivered_at=sent + timedelta(seconds=ack) if ack else None))
            ids.append((org.id, push.id, token.id))
        db.commit()
    return ids, secrets


def test_org_filter_datenschutz_und_ack(push_daten):
    ids, secrets = push_daten
    with SessionLocal() as db:
        result = collect(db, "admin_push_pruefen", org_id=ids[0][0])
        assert result["status"] == "warn"
        evidence = result["evidence"]
        assert evidence["tokens_je_org"] == [{"org_id": ids[0][0], "anzahl": 1, "native_alarm": 1, "aktiv_7_tage": 1}]
        assert [push["id"] for push in evidence["pushes"]] == [ids[0][1]]
        assert evidence["pushes"][0]["sent_at"].endswith("Z")
        assert evidence["zusammenfassung"] == {"angenommen_ohne_ack": 1, "fehlgeschlagen": 1, "bestaetigt": 1}
        deliveries = evidence["pushes"][0]["zustellungen"]
        assert [d["ack_sekunden"] for d in deliveries if d["delivered_at"]] == [15]
        assert all(d["native_alarm"] is True for d in deliveries)
        encoded = json.dumps(evidence)
        assert all(secret not in encoded for secret in secrets)
        assert "user_id" not in encoded and "title" not in encoded and "body" not in encoded
        assert collect(db, "admin_push_pruefen", org_id=ids[0][0], quelle="andere")["evidence"]["pushes"] == []
        all_pushes = collect(db, "admin_push_pruefen")["evidence"]["pushes"]
        assert {row[1] for row in ids} <= {row["id"] for row in all_pushes}
        other = collect(db, "admin_push_pruefen", org_id=ids[1][0])["evidence"]
        assert other["tokens_je_org"] == [{"org_id": ids[1][0], "anzahl": 1, "native_alarm": 0, "aktiv_7_tage": 0}]
        assert all(d["native_alarm"] is False for d in other["pushes"][0]["zustellungen"])
        assert len(collect(db, "admin_push_pruefen", limit=1)["evidence"]["pushes"]) == 1
        token = db.get(FcmToken, ids[0][2])
        db.delete(token)
        db.commit()
        assert all(d["native_alarm"] is None for d in collect(
            db, "admin_push_pruefen", org_id=ids[0][0])["evidence"]["pushes"][0]["zustellungen"])


def test_zustellungen_begrenzt_aber_vollstaendig_gezaehlt(push_daten):
    org_id, push_id, token_id = push_daten[0][0]
    with SessionLocal() as db:
        for _ in range(21):
            db.add(FcmDeliveryLog(push_log_id=push_id, fcm_token_id=token_id, success=True,
                                  sent_at=utcnow() - timedelta(seconds=120)))
        db.commit()
        evidence = collect(db, "admin_push_pruefen", org_id=org_id)["evidence"]
        assert len(evidence["pushes"][0]["zustellungen"]) == 20
        assert evidence["zusammenfassung"]["angenommen_ohne_ack"] == 22


@pytest.mark.parametrize("limit", [0, 51, True, 1.5])
def test_limit_validierung(limit):
    with pytest.raises(ValueError, match="Push-Limit"):
        collect(None, "admin_push_pruefen", limit=limit)


@pytest.mark.parametrize("failure", ["deaktiviert", "initialisierung"])
def test_fcm_nicht_verfuegbar(push_daten, monkeypatch, failure):
    if failure == "deaktiviert":
        monkeypatch.setattr(push_service, "_push_cfg", lambda db: {"fcm_enabled": False})
    else:
        monkeypatch.setattr(push_service, "_get_fcm_app", lambda cfg: None)
    with SessionLocal() as db:
        assert collect(db, "admin_push_pruefen", org_id=push_daten[0][0][0])["status"] == "fail"
        assert collect(db, "admin_push_pruefen", org_id=-1)["status"] == "ok"


def test_tool_registriert(client, admin_env, push_daten):
    token = oauth.tokens(client, admin_env)["access_token"]
    assert "admin_push_pruefen" in oauth.admin_call(client, token, "tools/list").text
    assert ADMIN_TOOLS["admin_push_pruefen"].module == "push"
    assert "admin_push_pruefen" not in READ_TOOLS
    result = call(client, token, "admin_push_pruefen", {"org_id": push_daten[0][0][0]})
    assert result["modul"] == "push" and result["status"] == "warn"
    assert call(client, token, "admin_push_pruefen", {"limit": 51})["status"] == "unbekannt"
