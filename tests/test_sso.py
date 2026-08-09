"""Tests fuer die Protokollierung fehlgeschlagener SSO-Callbacks."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.core.tenant import set_tenant_context
from app.models.master import FireDept
from app.models.sso import OrgSsoConfig
from app.routers.sso import _flow_signer


@pytest.fixture()
def db_session(setup_db):
    from tests.conftest import TestingSession

    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.rollback()
    db.close()


def _sso_org(db_session, slug: str, *, client_secret_enc: str = "broken") -> FireDept:
    org = FireDept(slug=slug, name=f"SSO Test {slug}")
    db_session.add(org)
    db_session.flush()
    db_session.add(OrgSsoConfig(
        org_id=org.id,
        enabled=True,
        tenant_id="11111111-1111-1111-1111-111111111111",
        client_id="22222222-2222-2222-2222-222222222222",
        client_secret_enc=client_secret_enc,
    ))
    db_session.commit()
    return org


def test_callback_mit_falschem_state_wird_protokolliert(
    client: TestClient, caplog: pytest.LogCaptureFixture,
):
    client.cookies.set("sso_flow", _flow_signer.dumps({
        "s": "expected", "slug": "state-test", "v": "verifier", "n": "nonce",
    }))
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.sso"):
        response = client.get(
            "/sso/state-test/callback?state=wrong&code=code",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=sso_failed"
    assert any("flow validation failed" in record.message for record in caplog.records)


def test_callback_mit_oauth_error_wird_protokolliert(
    client: TestClient, caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.sso"):
        response = client.get(
            "/sso/error-test/callback?error=invalid_request&error_description=redirect_uri",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=sso_failed"
    assert any("OAuth error" in record.message for record in caplog.records)


def test_callback_mit_defektem_client_secret_wird_protokolliert(
    client: TestClient, db_session, caplog: pytest.LogCaptureFixture,
):
    org = _sso_org(db_session, "secret-test")
    client.cookies.set("sso_flow", _flow_signer.dumps({
        "s": "state", "slug": org.slug, "v": "verifier", "n": "nonce",
    }))
    with caplog.at_level(logging.ERROR, logger="einsatzleiter.sso"):
        response = client.get(
            f"/sso/{org.slug}/callback?state=state&code=code",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=sso_failed"
    assert any("client secret decryption failed" in record.message for record in caplog.records)
