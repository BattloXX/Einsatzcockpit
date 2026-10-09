"""Geraete-Widerruf und GSL-Profile gelten auch fuer WebSockets."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.security import hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import MajorIncident, MajorIncidentStatus
from app.models.master import FireDept
from app.models.user import DeviceToken, Role, User, UserRole


def _geraet(profil: str, rolle: str = "recorder") -> tuple[int, int, int]:
    suffix = uuid4().hex[:10]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"ws-geraet-{suffix}", name="WS Geraet Test")
        db.add(org)
        db.flush()
        user = User(username=f"ws-geraet-{suffix}", password_hash=hash_password("Test1234!"),
                    display_name="WS Geraet", org_id=org.id, active=True, is_device=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == rolle).one().id))
        lage = MajorIncident(org_id=org.id, name="WS Testlage", status=MajorIncidentStatus.active)
        db.add(lage)
        db.flush()
        token = DeviceToken(user_id=user.id, label="WS Tablet", token_hash=hash_api_key(suffix),
                            gsl_profil=profil)
        db.add(token)
        db.commit()
        return user.id, token.id, lage.id
    finally:
        db.close()


def test_aktives_geraet_verbindet_lage_ws(client, setup_db):
    user_id, token_id, lage_id = _geraet("fuehrung")
    client.cookies.set("session", sign_session(user_id, device=True, device_token_id=token_id))
    with client.websocket_connect(f"/ws/lage/{lage_id}") as websocket:
        websocket.send_text("ping")
        assert websocket.receive_text() == "pong"


def test_widerrufenes_geraet_wird_im_lage_ws_abgelehnt(client, setup_db):
    user_id, token_id, lage_id = _geraet("fuehrung")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(DeviceToken, token_id).revoked_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()
    client.cookies.set("session", sign_session(user_id, device=True, device_token_id=token_id))
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/ws/lage/{lage_id}"):
            pass
    assert error.value.code == 4401


def test_einheit_geraet_wird_im_schreibenden_lagedokument_ws_abgelehnt(client, setup_db):
    user_id, token_id, lage_id = _geraet("einheit")
    client.cookies.set("session", sign_session(user_id, device=True, device_token_id=token_id))
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/ws/lagedokument/{lage_id}"):
            pass
    assert error.value.code == 4403
