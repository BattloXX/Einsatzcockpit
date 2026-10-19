"""Mess-CSP und anonyme, begrenzte CSP-Berichte."""

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.config import settings
from app.middleware.security_headers import (
    _CSP_BASE,
    SecurityHeadersMiddleware,
    _report_only_policy,
)
from app.routers import csp_report


@pytest.fixture(autouse=True)
def clear_reports():
    csp_report._reports.clear()
    yield
    csp_report._reports.clear()


def _directives(policy):
    return {parts[0]: parts[1:] for d in policy.split(";") if (parts := d.split())}


@pytest.mark.parametrize("path", ["/", "/infoscreen/alarm/token", "/wetter/infoscreen/token"])
def test_report_only_header(monkeypatch, path):
    monkeypatch.setattr(settings, "CSP_REPORT_ONLY", True)
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get(path)
    async def page():
        return HTMLResponse("<p>Messung</p>")

    with TestClient(app) as client:
        response = client.get(path)
        active = response.headers["content-security-policy"]
        report = response.headers["content-security-policy-report-only"]
        before, after = _directives(active), _directives(report)
        assert "'unsafe-inline'" in before["script-src"]
        assert "'unsafe-eval'" in before["script-src"]
        assert after.pop("script-src") == [s for s in before.pop("script-src")
                                             if s != "'unsafe-inline'"]
        assert after.pop("report-uri") == ["/csp-report"]
        assert after == before
        assert "'unsafe-inline'" in after["style-src"]
        if path.startswith("/infoscreen/alarm/"):
            assert after["frame-src"] == ["'self'", "https:"]
        elif path == "/" and not settings.TURNSTILE_SITE_KEY:
            assert active.startswith(_CSP_BASE)
        monkeypatch.setattr(settings, "CSP_REPORT_ONLY", False)
        disabled = client.get(path)
        assert "content-security-policy-report-only" not in disabled.headers
        assert disabled.headers["content-security-policy"] == active


def test_policy_helper_preserves_other_directives():
    policy = (
        "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline' https://cdn.test; "
        "style-src 'unsafe-inline'; frame-src https:"
    )
    assert _report_only_policy(policy) == (
        "default-src 'self'; script-src 'self' 'unsafe-eval' https://cdn.test; "
        "style-src 'unsafe-inline'; frame-src https:; report-uri /csp-report"
    )


def _report(blocked="inline", document="https://app.test/lage/123?token=geheim"):
    return {"csp-report": {"violated-directive": "script-src", "blocked-uri": blocked,
                           "document-uri": document}}


@pytest.mark.parametrize("media_type", ["application/csp-report", "application/json"])
def test_report_anonym_ohne_csrf_aggregiert(client, caplog, media_type):
    client.cookies.clear()
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.csp"):
        for _ in range(2):
            response = client.post("/csp-report", content=json.dumps(_report()),
                                   headers={"Content-Type": media_type})
            assert response.status_code == 204
            assert response.content == b""
    logs = [r for r in caplog.records if r.name == "einsatzleiter.csp"]
    assert len(logs) == 1
    assert "1 Meldungen" in logs[0].getMessage()
    assert "/lage/{id}" in logs[0].getMessage()
    assert "geheim" not in logs[0].getMessage()
    assert "123" not in logs[0].getMessage()


def test_aggregation_stunde_zaehler_und_obergrenze(monkeypatch, caplog):
    now = 10000.0
    monkeypatch.setattr(csp_report, "monotonic", lambda: now)
    monkeypatch.setattr(csp_report, "_MAX_KEYS", 2)
    report = _report()["csp-report"]
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.csp"):
        csp_report._log_report(report)
        csp_report._log_report(report)
        now += 3600
        csp_report._log_report(report)
        assert "2 Meldungen" in caplog.records[-1].getMessage()
        assert len(caplog.records) == 2
        csp_report._log_report(_report("eval")["csp-report"])
        csp_report._log_report(_report("https://cdn.test/file.js")["csp-report"])
    assert len(csp_report._reports) == 2
    assert ("script-src", "inline", "/lage/{id}") not in csp_report._reports


def test_normalisierung_ohne_zugangsdaten_oder_query():
    assert csp_report._blocked_origin("https://user:pass@cdn.test:8443/a?secret=x#token") == "https://cdn.test:8443"
    assert csp_report._blocked_origin("data:text/javascript,geheim") == "unbekannt"
    assert csp_report._document_path("https://app.test/lage/42/site/7?name=geheim#token") == "/lage/{id}/site/{id}"


@pytest.mark.parametrize("body", [
    b"{", b"\xff", b"[]", b'{"csp-report": null}', b'{"csp-report": {"effective-directive": []}}',
])
def test_ungueltiger_report_204(client, body):
    response = client.post("/csp-report", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 204
    assert response.content == b""


def test_body_limit_auch_ohne_content_length(client):
    response = client.post("/csp-report", content=iter([b" " * 8192, b" " * 8193]),
                           headers={"Content-Type": "application/csp-report"})
    assert response.status_code == 413
    response = client.post("/csp-report", content=b" " * 16384,
                           headers={"Content-Type": "application/csp-report"})
    assert response.status_code == 204


def test_rate_limit(client):
    for _ in range(60):
        assert client.post("/csp-report", json={}).status_code == 204
    assert client.post("/csp-report", json={}).status_code == 429


def test_legacy_directive_und_effective_directive(caplog):
    report = _report()["csp-report"]
    report["violated-directive"] = "script-src 'self' https://cdn.test/private?secret=wert"
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.csp"):
        csp_report._log_report(report)
        report["effective-directive"] = "script-src-elem"
        csp_report._log_report(report)
    assert len(caplog.records) == 2
    assert "script-src-elem" in caplog.records[-1].getMessage()
    assert "secret" not in caplog.text
