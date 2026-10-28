"""Inhaltsvergleich, gedrosselte Speicherung und Gateway-Broadcasts fuer Drucker."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from starlette.websockets import WebSocketDisconnect

from app.core.tenant import set_tenant_context
from app.models.gateway import GATEWAY_KIND_LINUX, Printer
from app.routers import ws
from app.services import printer_report_service as dienst
from tests.test_print_agent_ws import _anlegen
from tests.test_ws_gateway_stability import Socket


@pytest.mark.parametrize("veraltet,erreichbar,erwartet", [
    (False, True, False), (False, False, True), (True, True, False),
])
async def test_status_schreiben_und_broadcast(monkeypatch, caplog, veraltet, erreichbar, erwartet):
    daten = _anlegen(997320, kind=GATEWAY_KIND_LINUX, routen=False)
    jetzt = datetime.now(UTC)
    alt = jetzt - timedelta(seconds=100 if veraltet else 30)
    vorher = {"reachable": True, "state": "idle", "checked_at": alt.isoformat(),
              "updated_at": alt.isoformat(), "timestamp": alt.isoformat()}
    with dienst.SessionLocal() as db:
        set_tenant_context(db, None)
        drucker = db.get(Printer, daten["printer_id"])
        drucker.status = vorher
        db.commit()

    commits = []
    sitzungsfabrik = dienst.SessionLocal

    def sitzung():
        db = sitzungsfabrik()
        event.listen(db, "after_commit", lambda _db: commits.append(True))
        return db

    monkeypatch.setattr(dienst, "SessionLocal", sitzung)
    monkeypatch.setattr(ws, "_gateway_config_sync", lambda _id: {})
    broadcast = AsyncMock()
    monkeypatch.setattr(ws, "broadcast_org", broadcast)
    status = {"reachable": erreichbar, "state": "idle", "checked_at": jetzt.isoformat(),
              "updated_at": jetzt.isoformat(), "timestamp": jetzt.isoformat()}
    nachricht = json.dumps({"type": "printer_status", "payload": {
        "printers": [{"printer_id": daten["printer_id"], "status": status}],
    }})
    socket = Socket(nachricht, nachricht, WebSocketDisconnect())
    socket.headers = {"authorization": f"Bearer {daten['token']}"}
    socket.query_params = {}
    with caplog.at_level("INFO", logger="einsatzleiter.print"):
        await ws.print_gateway_ws(socket)

    # Die zweite identische Meldung darf weder committen noch broadcasten.
    assert len(commits) == int(veraltet or erwartet)
    berichte = [aufruf.args[1] for aufruf in broadcast.call_args_list
                if aufruf.args[1]["type"] == "printer_report"]
    assert len(berichte) == int(erwartet)
    with sitzungsfabrik() as db:
        set_tenant_context(db, None)
        gespeichert = db.get(Printer, daten["printer_id"]).status
    assert gespeichert["reachable"] is erreichbar
    if veraltet or erwartet:
        assert datetime.fromisoformat(gespeichert["checked_at"]) >= jetzt.replace(tzinfo=None)
    else:
        assert gespeichert == vorher
    wechsel = [record for record in caplog.records if record.message.startswith("Druckerstatus geaendert")]
    assert len(wechsel) == int(erwartet)
    if erwartet:
        assert wechsel[0].message == (
            f"Druckerstatus geaendert (printer_id={daten['printer_id']}, reachable=True -> False)"
        )


def test_leere_meldung_ohne_sitzung(monkeypatch):
    def verboten():
        pytest.fail("Leere Meldungen benoetigen keine DB-Sitzung")

    monkeypatch.setattr(dienst, "SessionLocal", verboten)
    assert dienst.apply_printer_status(1, 1, {}) is False
