"""Pairing, Zugangsdaten und technische Metadaten des Infoscreen-Agents."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.models.infoscreen_agent import InfoscreenDevice
from app.routers.infoscreen_agent_api import require_device
from app.services import infoscreen_agent_service as service
from tests.conftest import TestingSession as SessionLocal

PAIR_URL = "/api/v1/infoscreen-agent/pair"


def create_device(org_id=1):
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        device = InfoscreenDevice(org_id=org_id, name="Monitor " + uuid4().hex)
        db.add(device)
        db.flush()
        code = service.erzeuge_pairing_code(db, device)
        db.commit()
        return device.id, code


def authenticate(db, token, scheme="Bearer"):
    return require_device(
        Request(
            {
                "type": "http",
                "headers": [
                    (b"authorization", f"{scheme} {token}".encode()),
                ],
            }
        ),
        db,
    )


def test_pairing_ok_hash_ttl_und_wiederverwendung(client):
    device_id, code = create_device()
    assert len(code) == 8
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        device = db.get(InfoscreenDevice, device_id)
        assert device.pairing_code_hash == hash_api_key(code)
        assert device.pairing_expires_at.tzinfo is None
        rest = device.pairing_expires_at - datetime.now(UTC).replace(tzinfo=None)
        assert timedelta(minutes=59) < rest <= timedelta(minutes=60)
    response = client.post(PAIR_URL, json={"code": " " + code.lower() + " "})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["device_id"] == device_id
    with SessionLocal() as db:
        device = authenticate(db, data["device_token"])
        assert device.status == "active" and device.org_id == 1
        assert device.device_token_hash == hash_api_key(data["device_token"])
        assert device.pairing_code_hash is None and device.pairing_expires_at is None
        assert device.last_seen_at.tzinfo is None and device.last_ip
        assert db.info["current_org_id"] == device.org_id
    assert client.post(PAIR_URL, json={"code": code}).status_code == 401


@pytest.mark.parametrize("zustand", ["abgelaufen", "falsch", "gesperrt", "fehlende_org"])
def test_pairing_ungueltig(client, zustand):
    device_id, code = create_device()
    with SessionLocal() as db:
        set_tenant_context(db, None)
        device = db.get(InfoscreenDevice, device_id)
        if zustand == "abgelaufen":
            device.pairing_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        elif zustand == "falsch":
            code = "ZZZZZZZZ"
        elif zustand == "gesperrt":
            device.status = "revoked"
        else:
            device.org_id = None
        db.commit()
    assert client.post(PAIR_URL, json={"pairing_code": code}).status_code == 401
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(InfoscreenDevice, device_id).device_token_hash is None


@pytest.mark.parametrize("body", [{}, [], {"code": 123}, {"code": " "}])
def test_pairing_body_ungueltig(client, body):
    assert client.post(PAIR_URL, json=body).status_code == 400


def test_pairing_json_ungueltig(client):
    assert client.post(PAIR_URL, content="{ungueltig").status_code == 400


def test_pairing_drossel_schuetzt_auch_gueltigen_code(client):
    _, code = create_device()
    for _ in range(5):
        assert client.post(PAIR_URL, json={"code": "ZZZZZZZZ"}).status_code == 401
    assert client.post(PAIR_URL, json={"code": code}).status_code == 429


def test_sperren_entsperren_rotation_und_last_seen(client):
    device_id, code = create_device()
    token = client.post(PAIR_URL, json={"code": code}).json()["device_token"]
    with SessionLocal() as db:
        device = authenticate(db, token)
        service.mark_seen(db, device, ip="127.0.0.2", agent_version="1.0", os_version="Ubuntu", hostname="halle")
        db.commit()
        assert device.last_seen_at.tzinfo is None
        assert (device.last_ip, device.agent_version, device.os_version, device.hostname) == (
            "127.0.0.2",
            "1.0",
            "Ubuntu",
            "halle",
        )
        device.agent_connection_id = "alt"
        device.agent_worker_id = "worker"
        device.agent_presence_expires_at = datetime.now(UTC).replace(tzinfo=None)
        old_code = service.erzeuge_pairing_code(db, device)
        new_token = service.rotate_token(db, device)
        db.commit()
        assert service.resolve_device_token(db, token) is None
        assert service.pair(db, old_code) is None
        assert authenticate(db, new_token).id == device_id
        assert device.agent_connection_id is None and device.agent_worker_id is None
        assert device.agent_presence_expires_at is None
        pending_code = service.erzeuge_pairing_code(db, device)
        service.sperren(device)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            authenticate(db, new_token)
        assert exc.value.status_code == 401
        assert service.pair(db, pending_code) is None
        with pytest.raises(ValueError):
            service.rotate_token(db, device)
        with pytest.raises(ValueError):
            service.erzeuge_pairing_code(db, device)
        with pytest.raises(ValueError):
            service.mark_seen(db, device)
        service.entsperren(device)
        db.commit()
        assert device.status == "pending"
        assert service.resolve_device_token(db, new_token) is None
        code = service.erzeuge_pairing_code(db, device)
        db.commit()
    response = client.post(PAIR_URL, json={"code": code})
    assert response.status_code == 200
    assert response.json()["device_token"] not in (token, new_token)


@pytest.mark.parametrize("scheme,token", [("Bearer", ""), ("Basic", "falsch"), ("Bearer", "falsch")])
def test_device_auth_ungueltig(scheme, token):
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            authenticate(db, token, scheme)
        assert exc.value.status_code == 401
        assert exc.value.headers["WWW-Authenticate"] == "Bearer"


def test_neuer_pairing_code_verwirft_alten(client):
    device_id, old_code = create_device()
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        code = service.erzeuge_pairing_code(db, db.get(InfoscreenDevice, device_id))
        db.commit()
    assert client.post(PAIR_URL, json={"code": old_code}).status_code == 401
    assert client.post(PAIR_URL, json={"code": code}).status_code == 200


def test_pairing_code_kann_auch_parallel_nur_einmal_eingeloest_werden():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _, code = create_device()
    barrier = Barrier(4)

    def pair_once(_index):
        with SessionLocal() as db:
            set_tenant_context(db, None)
            barrier.wait(timeout=10)
            result = service.pair(db, code)
            db.commit()
            return result is not None

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(pair_once, range(4))) == 1
