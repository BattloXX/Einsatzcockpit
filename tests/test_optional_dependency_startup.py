"""Optionale Feature-Pakete dürfen weder App-Import noch andere Routen blockieren."""
import builtins
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect

from tests.test_probenplanung_public import public_setup


@pytest.mark.parametrize("failure", ["missing", "broken"])
def test_app_import_without_feature_dependencies(tmp_path, failure):
    # Frischer Interpreter: conftest und andere Tests haben app.main bereits importiert.
    code = '''
import builtins
import sys
packages = {"icalendar", "openpyxl", "qrcode", "anthropic", "pycrdt", "jose"}
if sys.argv[1] == "missing":
    for package in packages:
        sys.modules[package] = None
else:
    original_import = builtins.__import__
    def broken_import(name, *args, **kwargs):
        if name.split(".")[0] in packages:
            raise OSError("Defekte Zusatzbibliothek")
        return original_import(name, *args, **kwargs)
    builtins.__import__ = broken_import
from app.main import app
from fastapi.testclient import TestClient
client = TestClient(app)
assert client.get("/login").status_code == 200
from app.services import ws_bus
assert ws_bus.CH_LAGEDOKUMENT in ws_bus._handlers
assert ws_bus.CH_LAGEDOKUMENT_AWARENESS in ws_bus._handlers
assert "app.services.lis.lis_geo" not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-c", code, failure],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "SECRET_KEY": "test-startup-secret", "DATABASE_URL": f"sqlite:///{tmp_path}/startup.db"},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(params=["missing", "broken"])
def unavailable(request, monkeypatch):
    def block(package):
        if request.param == "missing":
            monkeypatch.setitem(sys.modules, package, None)
        else:
            original_import = builtins.__import__

            def broken_import(name, *args, **kwargs):
                if name == package or name.startswith(package + "."):
                    raise OSError("Defekte Zusatzbibliothek")
                return original_import(name, *args, **kwargs)

            monkeypatch.setattr(builtins, "__import__", broken_import)
    return block


def test_ics_unavailable_only_affects_feed(client, unavailable, caplog):
    plain, *_ = public_setup()
    unavailable("icalendar")
    response = client.get(f"/p/probenplan/{plain}.ics")
    assert response.status_code == 503
    assert "icalendar" in response.text
    assert "fehlt oder ist defekt" in response.text
    assert "pip install -e ." in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert "icalendar kann nicht geladen werden" in caplog.text
    assert client.get(f"/p/probenplan/{plain}").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/p/probenplan/ungueltig.ics").status_code == 404


def test_excel_unavailable_has_feature_errors(unavailable, caplog):
    from app.routers.ui_mailing import list_import_template
    from app.services.einsatz_import_service import EinsatzImportError, parse_einsatz_excel
    from app.services.mailing_service import import_xlsx

    unavailable("openpyxl")
    with pytest.raises(EinsatzImportError, match="openpyxl.*pip install -e"):
        parse_einsatz_excel(b"excel")
    with pytest.raises(ValueError, match="openpyxl.*pip install -e"):
        import_xlsx(None, None, b"excel")
    with pytest.raises(HTTPException) as error:
        list_import_template()
    assert error.value.status_code == 503
    assert "pip install -e ." in error.value.detail
    assert "openpyxl kann nicht geladen werden" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [False, True])
async def test_ai_unavailable_has_service_error(unavailable, monkeypatch, caplog, vision):
    from app.services import ai_service

    monkeypatch.setattr(ai_service, "_get_ai_cfg", lambda: {
        "enabled": True, "api_key": "test", "model_fast": "test", "model_default": "test", "max_tokens": 10,
    })
    unavailable("anthropic")
    with pytest.raises(ai_service.AIServiceError, match="anthropic.*pip install -e"):
        if vision:
            await ai_service._complete_vision("system", "user", [b"image"])
        else:
            await ai_service._complete("system", "user")
    assert "Anthropic-SDK kann nicht geladen werden" in caplog.text


def test_qr_unavailable_dashboard_still_works(client, unavailable, caplog):
    from tests.test_incident_subnav import _login, _setup

    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.incident import Incident, IncidentToken
    from app.models.uas import UASEinsatz

    username, _, incident_id, uas_id = _setup()
    try:
        assert _login(client, username, "Test1234!").status_code in (302, 303)
        unavailable("qrcode")
        response = client.get(f"/einsatz/{incident_id}/dashboard")
        assert response.status_code == 200
        assert "QR-Code nicht verfügbar" in caplog.text
    finally:
        # Keine zusätzlichen Einsatz-IDs für spätere Tests hinterlassen, die
        # daraus feste Objektnummern in der gemeinsamen Test-DB ableiten.
        with SessionLocal() as db:
            set_tenant_context(db, None)
            db.query(IncidentToken).filter(IncidentToken.incident_id == incident_id).delete()
            db.query(UASEinsatz).filter(UASEinsatz.id == uas_id).delete()
            db.query(Incident).filter(Incident.id == incident_id).delete()
            db.commit()


@pytest.mark.parametrize("package", ["pycrdt", "pycrdt.websocket.yroom"])
def test_crdt_unavailable_closes_only_sync(client, unavailable, caplog, package):
    from tests.test_lagedokument_pr2_collab import _login, _make_user_with_lage

    name = "missing_crdt_" + uuid.uuid4().hex[:8]
    _, lage_id = _make_user_with_lage(name, org_slug=name, rolle="incident_leader")
    assert _login(client, name, "Test1234!").status_code in (302, 303)
    unavailable(package)
    with client.websocket_connect(f"/ws/lagedokument/{lage_id}") as ws:
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_bytes()
        assert error.value.code == 1011
        assert "pip install -e ." in error.value.reason
    assert "Lagedokument-Sync nicht verfügbar" in caplog.text
    assert client.get(f"/lage/{lage_id}/lagedokument").status_code == 200
    assert client.get(f"/lage/{lage_id}/lagedokument/druck").status_code == 200


@pytest.mark.asyncio
async def test_sso_unavailable_has_service_error(unavailable, caplog):
    from app.services.sso_service import SsoError, validate_id_token

    unavailable("jose")
    with pytest.raises(SsoError, match="python-jose.*pip install -e") as error:
        await validate_id_token(id_token="test", tenant_id="test", client_id="test", nonce="test")
    assert error.value.code == "sso_failed"
    assert "python-jose kann nicht geladen werden" in caplog.text
