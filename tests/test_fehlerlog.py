"""Fehlerdiagnose: Geheimnisschutz und tasklokale Korrelations-IDs."""

import asyncio
import logging
import re

import httpx
import pytest
from sqlalchemy.exc import OperationalError
from starlette.requests import Request
from starlette.responses import Response

from app.core.fehlerlog import korrelation_setzen, korrelations_id, log_fehler, neue_korrelations_id
from app.main import session_middleware
from app.services import gateway_presence, teams_alarm_service

logger = logging.getLogger("test.fehlerlog")
GEHEIMNISSE = ("token-geheim", "+436641234567", "password=geheim", "https://webhook.example/token-geheim")


def test_pflichtfelder_und_eine_zeile(caplog, monkeypatch):
    monkeypatch.setattr(gateway_presence, "worker_id", lambda: "worker-test")
    token = korrelation_setzen("request-1234")
    try:
        log_fehler(logger, "Versand fehlgeschlagen", RuntimeError("vertraulich"), dauer_s=0.123,
                   channel="sms", org_id="42", job_id=7, versuch=2, code=1013)
    finally:
        korrelations_id.reset(token)
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert not record.exc_info and not record.stack_info
    for feld in ("Versand fehlgeschlagen", "fehlerklasse=RuntimeError", "dauer_ms=123",
                 "worker_id=worker-test", "korrelation_id=request-1234", "channel=sms",
                 "org_id=42", "job_id=7", "versuch=2", "code=1013"):
        assert feld in record.message
    assert "\n" not in record.message


@pytest.mark.parametrize("geheimnis", GEHEIMNISSE)
def test_geheimnisse_auch_in_erlaubten_kontextschluesseln_verwerfen(caplog, geheimnis):
    class VertraulichesObjekt:
        def __str__(self):
            raise AssertionError("Fremde Objekte niemals in Text umwandeln")

    log_fehler(logger, "Versand\nfehlgeschlagen", RuntimeError(geheimnis),
               token=geheimnis, phone=geheimnis, password=geheimnis, webhook_url=geheimnis,
               channel=geheimnis, kanal=geheimnis, incident_id=geheimnis,
               org_id=geheimnis, job_id=geheimnis, versuch=geheimnis, code=geheimnis,
               unbekannt=VertraulichesObjekt())
    log_fehler(logger, "Objekt", RuntimeError(geheimnis), job_id=VertraulichesObjekt())
    assert geheimnis not in caplog.text
    assert "Traceback" not in caplog.text
    assert all("\n" not in record.message for record in caplog.records)
    assert all("job_id=" not in record.message for record in caplog.records)


def test_operational_error_nur_numerischer_dbapi_code(caplog):
    geheim = " ".join(GEHEIMNISSE)
    for code in (1213, "1020", geheim):
        exc = OperationalError(f"SELECT {geheim}", {"password": geheim}, Exception(code, geheim))
        log_fehler(logger, "DB fehlgeschlagen", exc)
    assert "dbapi_code=1213" in caplog.records[0].message
    assert "dbapi_code=1020" in caplog.records[1].message
    assert "dbapi_code=" not in caplog.records[2].message
    assert all("fehlerklasse=OperationalError" in record.message for record in caplog.records)
    assert "SELECT" not in caplog.text
    assert all(geheimnis not in caplog.text for geheimnis in GEHEIMNISSE)


async def test_kontext_isolation_paralleler_tasks(caplog):
    eltern_token = korrelation_setzen("eltern-1234")
    bereit = asyncio.Event()
    anzahl = 0

    async def aufgabe(id):
        nonlocal anzahl
        token = korrelation_setzen(id)
        try:
            anzahl += 1
            if anzahl == 2:
                bereit.set()
            await bereit.wait()
            log_fehler(logger, id, ValueError("geheim"))
            return korrelations_id.get()
        finally:
            korrelations_id.reset(token)

    try:
        assert await asyncio.gather(aufgabe("task-0001"), aufgabe("task-0002")) == ["task-0001", "task-0002"]
        assert korrelations_id.get() == "eltern-1234"
        for id in ("task-0001", "task-0002"):
            assert any(record.message.startswith(id) and f"korrelation_id={id}" in record.message
                       for record in caplog.records)
    finally:
        korrelations_id.reset(eltern_token)


@pytest.mark.parametrize("pfad", ["/static/test.css", "/login"])
@pytest.mark.parametrize("header", [None, "request-1234", "a" * 64, "kurz", "a" * 65,
                                   "request_1234", "request-1234\n", "+436641234567", "https://webhook.example"])
@pytest.mark.parametrize("fehler", [False, True])
async def test_middleware_setzt_und_resetet_id(pfad, header, fehler):
    headers = [] if header is None else [(b"x-request-id", header.encode())]
    request = Request({"type": "http", "path": pfad, "headers": headers, "query_string": b""})
    vorher = korrelations_id.get()
    eltern_token = korrelation_setzen("eltern-1234")

    async def naechster(request):
        id = korrelations_id.get()
        if header is not None and re.fullmatch(r"[A-Za-z0-9-]{8,64}", header):
            assert id == header
        else:
            assert re.fullmatch(r"[0-9a-f]{32}", id)
            assert id != "eltern-1234"
        if fehler:
            raise RuntimeError("Testfehler")
        return Response()

    try:
        if fehler:
            with pytest.raises(RuntimeError, match="Testfehler"):
                await session_middleware(request, naechster)
        else:
            assert (await session_middleware(request, naechster)).status_code == 200
        assert korrelations_id.get() == "eltern-1234"
    finally:
        korrelations_id.reset(eltern_token)
    assert korrelations_id.get() == vorher


def test_neue_id_und_einstellbares_level(caplog):
    assert neue_korrelations_id() != neue_korrelations_id()
    assert re.fullmatch(r"[0-9a-f]{32}", neue_korrelations_id())
    token = korrelation_setzen()
    try:
        assert re.fullmatch(r"[0-9a-f]{32}", korrelations_id.get())
        log_fehler(logger, "Fehler", ValueError("geheim"), level=logging.ERROR)
        assert caplog.records[0].levelno == logging.ERROR
        assert "dauer_ms=None" in caplog.text
    finally:
        korrelations_id.reset(token)


async def test_teams_transportfehler_ohne_geheimnisse_in_folgelogs(monkeypatch, caplog):
    class Client:
        async def post(self, *args, **kwargs):
            raise httpx.ReadError(" ".join(GEHEIMNISSE))

    monkeypatch.setattr(teams_alarm_service, "_teams_http_client", Client)
    ergebnis = await teams_alarm_service._post_payload(GEHEIMNISSE[-1], {}, log_label="Test")
    assert ergebnis == (False, False, "ReadError", True)
    assert all(geheimnis not in caplog.text for geheimnis in GEHEIMNISSE)
