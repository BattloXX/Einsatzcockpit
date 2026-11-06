"""Waechter fuer DB-Arbeit im Worker und hauptloopgebundene MCP-Nebenwirkungen."""

import asyncio
import inspect
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from mcp.server.auth.middleware import auth_context
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import event

from app.core.tenant import set_tenant_context
from app.db import SessionLocal, engine
from app.mcp import context as context_modul
from app.mcp import server as mcp
from app.mcp.context import MCPPermissionError, load_live_context
from app.mcp.fehler import ExternerDienstFehler
from app.mcp.registry import TOOLS
from app.models.master import OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole
from tests.test_mcp_fundament import _enable_mcp


@pytest.fixture
def zugang(monkeypatch):
    name = "loop-" + uuid4().hex
    user = _enable_mcp(username=name, slug=name)
    token = AccessToken(token="test", client_id="loop-test", scopes=["mcp"],
                        subject=str(user["id"]), claims={"org_id": user["org_id"]})
    # Das Auslesen muss vor to_thread auf dem aufrufenden Loop stattfinden.
    token_kontext = ContextVar("mcp_test_token", default=None)
    def get_token():
        asyncio.get_running_loop()
        return token_kontext.get()
    monkeypatch.setattr(auth_context, "get_access_token", get_token)
    marker = token_kontext.set(token)
    yield user | {"token": token}
    token_kontext.reset(marker)


def _referenz(user_id, org_id):
    """Die bisherige Sichtbarkeit: Live-Kontext und Modulpruefung pro Werkzeug."""
    with SessionLocal() as db:
        set_tenant_context(db, None)
        sichtbar = set()
        for name, definition in TOOLS.items():
            try:
                context = load_live_context(db, user_id, org_id, definition.required_roles)
                if not definition.module_check or definition.module_check(context.org_id, db):
                    sichtbar.add(name)
            except MCPPermissionError:
                continue
        return sichtbar


@contextmanager
def _keine_loop_queries():
    loop_thread = threading.get_ident()
    queries = []
    def pruefen(conn, cursor, statement, parameters, context, executemany):
        queries.append(threading.get_ident())
        assert threading.get_ident() != loop_thread, statement
    event.listen(engine, "before_cursor_execute", pruefen)
    try:
        yield queries
    finally:
        event.remove(engine, "before_cursor_execute", pruefen)


@pytest.mark.parametrize("zustand", ["readonly", "recorder", "org_admin", "inaktiv", "geraet",
                                    "fremde_org", "mcp_org_aus", "mcp_system_aus", "unbekannt"])
def test_list_tools_wie_referenz_ohne_loop_queries(zugang, zustand):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = db.query(User).execution_options(include_all_tenants=True).filter_by(id=zugang["id"]).one()
        if zustand in {"recorder", "org_admin"}:
            role = db.query(Role).filter_by(code=zustand).one()
            db.add(UserRole(user_id=user.id, role_id=role.id))
        elif zustand == "inaktiv":
            user.active = False
        elif zustand == "geraet":
            user.is_device = True
        elif zustand == "fremde_org":
            user.org_id = None
        elif zustand == "mcp_org_aus":
            db.query(OrgSettings).filter_by(org_id=zugang["org_id"]).one().mcp_modul_aktiv = False
        elif zustand == "mcp_system_aus":
            db.query(SystemSettings).filter_by(key="mcp_module_enabled").one().value = "false"
        elif zustand == "unbekannt":
            zugang["id"] = -1
            zugang["token"].subject = "-1"
        db.commit()
    erwartet = _referenz(zugang["id"], zugang["org_id"])
    async def pruefen():
        with _keine_loop_queries() as queries:
            sichtbar = {tool.name for tool in await mcp.server.list_tools()}
        assert queries
        assert sichtbar == erwartet
        if zustand in {"readonly", "recorder", "org_admin"}:
            assert "mcp_whoami" in sichtbar
        else:
            assert sichtbar == set()
    asyncio.run(pruefen())


def test_list_tools_laedt_rechte_und_module_nur_einmal(zugang, monkeypatch):
    enabled = Mock(wraps=context_modul.mcp_effective_enabled)
    laden = Mock(wraps=mcp.load_live_context)
    check = Mock(return_value=True)
    namen = ("mcp_whoami", "einsaetze_liste")
    monkeypatch.setattr(context_modul, "mcp_effective_enabled", enabled)
    monkeypatch.setattr(mcp, "load_live_context", laden)
    monkeypatch.setattr(mcp, "TOOLS", {name: replace(TOOLS[name], module_check=check) for name in namen})
    async def pruefen():
        assert {t.name for t in await mcp.server.list_tools()} == set(namen)
    asyncio.run(pruefen())
    assert laden.call_count == enabled.call_count == check.call_count == 1


def test_list_tools_module_permission_error_bleibt_unsichtbar(zugang, monkeypatch):
    check = Mock(side_effect=MCPPermissionError("Modul gesperrt"))
    monkeypatch.setattr(mcp, "TOOLS", {name: replace(TOOLS[name], module_check=check)
                                     for name in ("mcp_whoami", "einsaetze_liste")})
    async def pruefen():
        assert await mcp.server.list_tools() == []
    asyncio.run(pruefen())
    check.assert_called_once()


def test_normales_tool_ohne_loop_queries(zugang):
    assert not TOOLS["einsaetze_liste"].hauptloop
    async def pruefen():
        with _keine_loop_queries() as queries:
            result = await mcp._call_registered_tool("einsaetze_liste")
        assert queries
        assert result["items"] == []
    asyncio.run(pruefen())


def test_hauptloop_tool_broadcast_bleibt_auf_request_loop(zugang, monkeypatch):
    from app.models.major_incident import MajorIncident
    from app.services import gsl_broadcast

    with SessionLocal() as db:
        set_tenant_context(db, None)
        role = db.query(Role).filter_by(code="recorder").one()
        db.add(UserRole(user_id=zugang["id"], role_id=role.id))
        lage = MajorIncident(org_id=zugang["org_id"], name="Loop-Test")
        db.add(lage)
        db.commit()
        lage_id = lage.id
    assert TOOLS["gsl_journal_eintrag"].hauptloop
    broadcasts = []
    laden = mcp._live_context_for_tool
    kontext_threads = []
    def laden_im_thread(*args):
        kontext_threads.append(threading.get_ident())
        return laden(*args)
    monkeypatch.setattr(mcp, "_live_context_for_tool", laden_im_thread)
    async def pruefen():
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        async def broadcast(lage_id):
            assert asyncio.get_running_loop() is loop
            assert threading.get_ident() == loop_thread
            broadcasts.append(lage_id)
        monkeypatch.setattr(gsl_broadcast, "journal_geaendert", broadcast)
        result = await mcp._call_registered_tool("gsl_journal_eintrag", lage_id=lage_id, betreff="Test")
        assert result["id"]
        assert kontext_threads and all(t != loop_thread for t in kontext_threads)
    asyncio.run(pruefen())
    assert broadcasts == [lage_id]


@pytest.mark.parametrize("fehler", [ValueError("Eingabe"), MCPPermissionError("Verboten"),
                                    ExternerDienstFehler(code="timeout", meldung="Extern", retryable=True),
                                    RuntimeError("Intern")])
def test_worker_fehler_mapping_und_session_close(zugang, monkeypatch, fehler):
    geschlossen = []
    async def handler(context):
        assert threading.get_ident() != main_thread
        close = context.db.close
        def schliessen():
            geschlossen.append(threading.get_ident())
            close()
        context.db.close = schliessen
        raise fehler
    monkeypatch.setitem(TOOLS, "mcp_whoami", replace(TOOLS["mcp_whoami"], handler=handler))
    main_thread = threading.get_ident()
    async def pruefen():
        with _keine_loop_queries():
            with pytest.raises(RuntimeError if isinstance(fehler, RuntimeError) else ToolError) as exc:
                await mcp._call_registered_tool("mcp_whoami")
        if isinstance(fehler, ExternerDienstFehler):
            details = json.loads(str(exc.value))
            assert details["code"] == "timeout" and details["retryable"]
        else:
            assert str(exc.value) == str(fehler)
    asyncio.run(pruefen())
    assert len(geschlossen) == 1 and geschlossen[0] != main_thread


def test_broadcast_tools_muessen_hauptloop_verwenden():
    for name, definition in TOOLS.items():
        source = inspect.getsource(definition.handler)
        if any(signal in source for signal in ("broadcast_lage", "gsl_broadcast", "sende_nach_commit")):
            assert definition.hauptloop, name


@pytest.mark.parametrize("name", [
    "gsl_ressource_zugang_senden", "gsl_ressource_anlegen", "gsl_einsatz_wiedereroeffnen",
    "einsatz_auftrag_anlegen", "einsatz_meldung_anlegen",
    "katplan_element_entwurf_anlegen", "katplan_element_entwurf_aendern", "katplan_elemente_verorten",
    "strassensperre_entwurf_aus_pdf", "strassensperre_anlegen", "strassensperre_aktualisieren",
    "strassensperre_geometrie_ermitteln", "objekt_karte_gebaeude_umrisse", "objekt_karte_georef_zuordnen",
])
def test_indirekte_loop_bindungen_bleiben_markiert(name):
    # SMS/Gateway, Broadcast-Helfer, globaler Geocoding-Lock und Redis-Lebensdauer.
    # inspect.getsource allein erkennt diese indirekten Abhaengigkeiten nicht.
    assert TOOLS[name].hauptloop


@pytest.mark.parametrize("phase", ["kontext", "handler"])
def test_request_abbruch_schliesst_session_erst_nach_worker(zugang, monkeypatch, phase):
    gestartet = threading.Event()
    freigabe = threading.Event()
    geschlossen = []
    original = mcp._live_context_for_tool
    def lade(*args):
        definition, context = original(*args)
        close = context.db.close
        def schliessen():
            assert freigabe.is_set()
            geschlossen.append(threading.get_ident())
            close()
        context.db.close = schliessen
        if phase == "kontext":
            gestartet.set()
            assert freigabe.wait(5)
        return definition, context
    async def handler(context):
        gestartet.set()
        assert freigabe.wait(5)
        return {}
    monkeypatch.setattr(mcp, "_live_context_for_tool", lade)
    monkeypatch.setitem(TOOLS, "mcp_whoami", replace(TOOLS["mcp_whoami"], handler=handler))
    async def pruefen():
        with _keine_loop_queries():
            task = asyncio.create_task(mcp._call_registered_tool("mcp_whoami"))
            try:
                assert await asyncio.to_thread(gestartet.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                assert not geschlossen
            finally:
                freigabe.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(geschlossen) == 1
        assert geschlossen[0] != threading.get_ident()
    asyncio.run(pruefen())
