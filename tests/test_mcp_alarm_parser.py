"""P7a: echte MCP-Aufrufe, Datenschutz, Freigaben, Mengenlimit und Org-Grenzen."""
import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.registry import TOOLS
from app.mcp.tools import alarm_parser as tools
from app.models.gateway import AlarmIngest, AlarmParserRegelsatz, Gateway
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import AuditLog, Role, User, UserRole
from app.services.alarm_parser import STANDARD_REGELSATZ
from app.services.alarm_parser.katalog import invalidiere_kontext
from tests import test_gateway_alarmempfang as gateway_tests
from tests.test_mcp_benutzer import _rufe
from tests.test_mcp_fundament import _enable_mcp, _mcp, _oauth_tokens

# _rufe ist im Bestand in test_mcp_benutzer definiert, nicht in test_mcp_fundament.
db = gateway_tests.db
diagnose = gateway_tests.diagnose

NAMEN = {"alarm_rohdaten_liste", "alarm_rohdaten_lesen", "alarm_parser_statistik",
         "alarm_parser_regelsatz_lesen", "alarm_ortsdaten_pruefen"}
GEHEIM = "Notrufer Max Mustermann +43 664 1234567 max@example.at AT611904300234573201"


@pytest.fixture
def daten(client, monkeypatch):
    rate_limiter = tools._limiter
    rate_limiter.cache_clear()
    monkeypatch.setattr(tools.settings, "REDIS_URL", "")
    seed = _enable_mcp(username=uuid4().hex, slug=uuid4().hex)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = db.get(User, seed["id"])
        ur = db.query(UserRole).filter_by(user_id=user.id).one()
        ur.role_id = db.query(Role).filter_by(code="admin").one().id
        org = db.get(FireDept, seed["org_id"])
        org.city, org.street = "Wolfurt", "Hauptstrasse"
        setting = db.query(OrgSettings).filter_by(org_id=org.id).one()
        setting.gateway_module_enabled = True
        setting.alarm_parser_mcp_aktiv = True
        flag = db.get(SystemSettings, "gateway_module_enabled")
        if flag is None:
            db.add(SystemSettings(key="gateway_module_enabled", value="true"))
        else:
            flag.value = "true"
        gw = Gateway(org_id=org.id, name="MCP")
        db.add(gw)
        db.flush()
        row = AlarmIngest(org_id=org.id, gateway_id=gw.id, raw_hash=uuid4().hex,
                          raw_text=GEHEIM, raw_bytes=(GEHEIM + "\x02\r\n").encode(), charset="utf-8",
                          process_status="needs_review", parse_status="ok", leitstellennummer="f26008809",
                          received_at=datetime(2026, 10, 26, 12, tzinfo=UTC), parser_version="1",
                          parsed={"status": "teilweise", "qualitaet": 60, "rest_text": GEHEIM,
                                  "hinweise": ["hausnummer_fehlt"], "meldebild": GEHEIM,
                                  "anrufer": "PRIVAT", "mannschaft": ["PRIVAT"], "parser_version": "2"})
        db.add(row)
        db.commit()
        seed.update(ingest_id=row.id, gateway_id=gw.id)
    seed["token"] = _oauth_tokens(client, seed)["access_token"]
    yield seed
    rate_limiter.cache_clear()
    invalidiere_kontext(seed["org_id"])


def rufe(client, daten, name, **kwargs):
    return _rufe(client, daten["token"], name, **kwargs)


def test_registrierung_und_adapter():
    assert {name for name in TOOLS if name.startswith("alarm_")} == NAMEN
    assert all(TOOLS[name].required_roles == ("admin",) and TOOLS[name].module_check for name in NAMEN)


@pytest.mark.parametrize("rolle, erlaubt", [("admin", True), ("org_admin", True), ("system_admin", True),
                                           ("readonly", False), ("incident_leader", False), ("recorder", False)])
def test_rollen_live(client, daten, rolle, erlaubt):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        ur = db.query(UserRole).filter_by(user_id=daten["id"]).one()
        ur.role_id = db.query(Role).filter_by(code=rolle).one().id
        db.commit()
    liste = _mcp(client, daten["token"], "tools/list", {}, 101).text
    assert all((name in liste) == erlaubt for name in NAMEN)
    result = rufe(client, daten, "alarm_rohdaten_liste")
    assert ("__fehler__" not in result) == erlaubt


@pytest.mark.parametrize("ebene, flag", [("org", "alarm_parser_mcp_aktiv"), ("org", "mcp_modul_aktiv"),
                                         ("org", "gateway_module_enabled"), ("system", "mcp_module_enabled"),
                                         ("system", "gateway_module_enabled")])
def test_live_schalter(client, daten, ebene, flag):
    assert "rohdaten" in rufe(client, daten, "alarm_rohdaten_liste")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        if ebene == "org":
            setattr(db.query(OrgSettings).filter_by(org_id=daten["org_id"]).one(), flag, False)
        else:
            db.get(SystemSettings, flag).value = "false"
        db.commit()
    listed = _mcp(client, daten["token"], "tools/list", {}, 102).text
    assert not any(name in listed for name in NAMEN)
    for name in NAMEN:
        args = {"ingest_id": daten["ingest_id"]} if name == "alarm_rohdaten_lesen" else {}
        assert "__fehler__" in rufe(client, daten, name, **args)


def test_ausgaben_maskiert_hex_und_whitelist(client, daten):
    for name in NAMEN:
        args = {"ingest_id": daten["ingest_id"]} if name == "alarm_rohdaten_lesen" else {}
        result = rufe(client, daten, name, **args)
        assert "__fehler__" not in result, result
        text = json.dumps(result)
        for verboten in ("Max", "Mustermann", "664", "max@example.at", "AT611904300234573201", "PRIVAT"):
            assert verboten not in text
        if name == "alarm_rohdaten_lesen":
            decoded = bytes.fromhex(result["hex_auszug"]).decode()
            assert "[Telefon]" in decoded and "[Name]" in decoded and "[IBAN]" in decoded
            assert "Max" not in decoded and "664" not in decoded and "@" not in decoded
            assert len(bytes.fromhex(result["hex_auszug"])) <= 512
            assert "\\x02" in result["steuerzeichen"]
            assert result["parserergebnis"]["parser_version"] == "2"
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(AlarmIngest).filter_by(id=daten["ingest_id"], org_id=daten["org_id"]).one().raw_text == GEHEIM
        logs = db.query(AuditLog).filter_by(org_id=daten["org_id"], action="alarm_ingest.mcp_gelesen").all()
        assert len(logs) == 5 and all(log.user_id == daten["id"] for log in logs)
        assert all("Max" not in log.payload_json and "Telefon" not in log.payload_json for log in logs)
        assert all(set(json.loads(log.payload_json)) == {"werkzeug", "anzahl", "filter"} for log in logs)


def test_cross_org(client, daten):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=uuid4().hex, name="Fremd")
        db.add(org)
        db.flush()
        gw = Gateway(org_id=org.id, name="Fremd")
        db.add(gw)
        db.flush()
        row = AlarmIngest(org_id=org.id, gateway_id=gw.id, raw_hash=uuid4().hex,
                          raw_text="FREMDES GEHEIMNIS", leitstellennummer="f26008809")
        db.add(row)
        db.add(AlarmParserRegelsatz(org_id=org.id, version=1, regeln={"version": "FREMDES GEHEIMNIS"}))
        db.commit()
        fremd_id = row.id
    assert "__fehler__" in rufe(client, daten, "alarm_rohdaten_lesen", ingest_id=fremd_id)
    for name in ("alarm_rohdaten_liste", "alarm_rohdaten_lesen", "alarm_parser_regelsatz_lesen"):
        args = {"ingest_id": daten["ingest_id"]} if name == "alarm_rohdaten_lesen" else {}
        result = rufe(client, daten, name, **args)
        assert "FREMDES" not in json.dumps(result)
        if name == "alarm_rohdaten_lesen":
            assert result["versionen"] == []
        if name == "alarm_parser_regelsatz_lesen":
            assert result["entwuerfe"] == []


def test_paging_filter_statistik(client, daten):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        for _i in range(52):
            db.add(AlarmIngest(org_id=daten["org_id"], gateway_id=daten["gateway_id"], raw_hash=uuid4().hex,
                               raw_text="a" * 400, process_status="ok", received_at=datetime(2026, 10, 27, tzinfo=UTC),
                               parsed={"status": "ok", "qualitaet": 100, "rest_text": "Hauptstrase"}))
        db.commit()
    erste = rufe(client, daten, "alarm_rohdaten_liste")
    zweite = rufe(client, daten, "alarm_rohdaten_liste", seite=2)
    assert erste["gesamt"] == 53 and len(erste["rohdaten"]) == 50 and erste["truncated"]
    assert len(zweite["rohdaten"]) == 3 and not zweite["truncated"]
    assert not ({r["id"] for r in erste["rohdaten"]} & {r["id"] for r in zweite["rohdaten"]})
    assert erste["rohdaten"][0]["truncated"] and len(erste["rohdaten"][0]["text"]) == 240
    for args in ({"limit": 51}, {"limit": 0}, {"seite": 0}, {"qualitaet_max": 101}, {"status": "falsch"},
                 {"von": "kaputt"}, {"von": "2026-10-28", "bis": "2026-10-26"}):
        assert "__fehler__" in rufe(client, daten, "alarm_rohdaten_liste", **args)
    result = rufe(client, daten, "alarm_rohdaten_liste", status="needs_review", qualitaet_max=60,
                  leitstellennummer="f26008809", nur_ohne_einsatz=True, von="2026-10-26", bis="2026-10-26T23:59:59Z")
    assert [r["id"] for r in result["rohdaten"]] == [daten["ingest_id"]]
    statistik = rufe(client, daten, "alarm_parser_statistik", bis="2026-10-26T23:59:59")
    assert statistik["gesamt"] == 1
    assert statistik["status"]["teilweise"] == {"anzahl": 1, "anteil": 1}
    assert statistik["hinweise"] == [{"text": "hausnummer_fehlt", "anzahl": 1}]
    assert statistik["alias_kandidaten"]["gemeinden"]
    alle = rufe(client, daten, "alarm_parser_statistik")
    assert alle["ausgewertet"] == 53 and alle["status"]["ok"]["anzahl"] == 52


def test_rate_limit_300_ueber_alle_tools_und_client_ids(daten):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = db.get(User, daten["id"])
        context = MCPContext(db, user, daten["org_id"])
        for i in range(300):
            context.client_id = str(i)
            tools._leselimit(context)
        with pytest.raises(ValueError, match="300"):
            asyncio.run(TOOLS["alarm_ortsdaten_pruefen"].handler(context))
        context.user = type("OtherUser", (), {"id": user.id + 1})()
        tools._leselimit(context)


def test_redis_storage_und_ausfall_geschlossen(daten, monkeypatch):
    from limits.storage import RedisStorage

    assert isinstance(tools._limiter("redis://localhost:6379/15").storage, RedisStorage)
    class Defekt:
        def hit(self, *args):
            raise ConnectionError("Redis nicht erreichbar")
    monkeypatch.setattr(tools, "_limiter", lambda url: Defekt())
    with SessionLocal() as db:
        set_tenant_context(db, None)
        context = MCPContext(db, db.get(User, daten["id"]), daten["org_id"])
        with pytest.raises(ValueError, match="nicht pruefbar"):
            tools._leselimit(context)


def test_regeln_und_ortsdaten(client, daten):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        for i in range(52):
            db.add(AlarmParserRegelsatz(org_id=daten["org_id"], version=i + 1,
                                       regeln=STANDARD_REGELSATZ.model_dump(mode="json")))
        db.commit()
    result = rufe(client, daten, "alarm_parser_regelsatz_lesen")
    assert result["schema"] == STANDARD_REGELSATZ.model_json_schema()
    assert len(result["entwuerfe"]) == 50 and result["gesamt"] == 52 and result["truncated"]
    assert result["katalog"] == [{"gemeinde": "Wolfurt", "ortsteile": [], "truncated": False}]
    result = rufe(client, daten, "alarm_ortsdaten_pruefen", gemeinde="Wolfurt", strasse="Hauptstrase")
    assert result["gemeinde"]["treffer"] == ["Wolfurt"]
    assert result["strasse"]["aehnlich"] == ["Hauptstrasse"]
    result = rufe(client, daten, "alarm_ortsdaten_pruefen", gemeinde="Wolfurt", strasse="Hauptstrasse")
    assert result["strasse"]["treffer"] == ["Hauptstrasse"]
    assert "__fehler__" in rufe(client, daten, "alarm_ortsdaten_pruefen", gemeinde="x" * 201)


@pytest.mark.parametrize("charset, raw", [("unbekannt", b"secret"), ("utf-8", b"\xff\xfe"),
                                         ("utf-8", (GEHEIM + "x" * 900).encode())])
def test_hex_grenzen_und_unsichere_dekodierung(client, daten, charset, raw):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.query(AlarmIngest).filter_by(id=daten["ingest_id"], org_id=daten["org_id"]).one()
        row.charset, row.raw_bytes = charset, raw
        db.commit()
    result = rufe(client, daten, "alarm_rohdaten_lesen", ingest_id=daten["ingest_id"])
    auszug = bytes.fromhex(result["hex_auszug"])
    assert len(auszug) <= 512
    if charset == "unbekannt" or raw == b"\xff\xfe":
        assert auszug == b""
    else:
        assert result["hex_truncated"] and b"Max" not in auszug


@pytest.mark.parametrize("rolle, status", [("admin", 200), ("org_admin", 200),
                                          ("readonly", 403), ("incident_leader", 403)])
def test_ui_checkbox_rechte_csrf_audit(diagnose, db, rolle, status):
    client, gw, user, _ = diagnose
    ur = db.query(UserRole).filter_by(user_id=user.id).one()
    ur.role_id = db.query(Role).filter_by(code=rolle).one().id
    db.commit()
    url = f"/gateway/{gw.id}/alarm-parser-mcp"
    assert client.post(url, data={"enabled": "true"}).status_code == 403
    response = client.post(url, data={"enabled": "true", "_csrf": client.cookies.get("ec_csrf")},
                           headers={"HX-Request": "true"})
    assert response.status_code == status
    setting = db.query(OrgSettings).filter_by(org_id=gw.org_id).one()
    db.refresh(setting)
    assert setting.alarm_parser_mcp_aktiv == (status == 200)
    if status == 200:
        assert "checked" in response.text and "MCP-Freigabe gespeichert" in response.text
        assert "Parser per MCP" in client.get(f"/gateway/{gw.id}").text
        assert db.query(AuditLog).filter_by(org_id=gw.org_id, user_id=user.id,
                                            action="alarm_parser.mcp_freigabe_geaendert").count() == 1


def test_parserfelder_ohne_namenspraefix_bleiben_maskiert(client, daten):
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        row = db.query(AlarmIngest).filter_by(id=daten["ingest_id"], org_id=daten["org_id"]).one()
        row.parsed = {**row.parsed, "meldebild": "Max Mustermann", "rest_text": "Mustermann"}
        db.commit()
    result = rufe(client, daten, "alarm_rohdaten_lesen", ingest_id=daten["ingest_id"])
    assert result["parserergebnis"]["meldebild"] == "[Name]"
    assert result["parserergebnis"]["rest_text"] == "[Name]"
    result = rufe(client, daten, "alarm_parser_statistik")
    assert "mustermann" not in json.dumps(result).casefold()


def test_statusfilter_ok_und_incomplete(client, daten):
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        row = db.query(AlarmIngest).filter_by(id=daten["ingest_id"], org_id=daten["org_id"]).one()
        row.process_status, row.parse_status = "applied", "incomplete"
        row.parsed = {"status": "ok", "qualitaet": 100}
        db.commit()
    for status in ("ok", "incomplete", "applied"):
        result = rufe(client, daten, "alarm_rohdaten_liste", status=status)
        assert [r["id"] for r in result["rohdaten"]] == [daten["ingest_id"]]


def test_ortsteil_und_strasse_mehrdeutig(daten, monkeypatch):
    from app.services.alarm_parser import GemeindeInfo, ParserKontext

    snapshot = ParserKontext(gemeinden={
        "wolfurt": GemeindeInfo("Wolfurt", ortsteile={"rickenbach": "Rickenbach"},
                               strassen={"hauptstrasse": "Hauptstrasse"}),
        "bregenz": GemeindeInfo("Bregenz", strassen={"hauptstrasse": "Hauptstrasse"}),
    })
    monkeypatch.setattr(tools, "baue_kontext", lambda db, org_id: snapshot)
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        context = MCPContext(db, db.get(User, daten["id"]), daten["org_id"])
        result = asyncio.run(tools.alarm_ortsdaten_pruefen(context, strasse="Hauptstrasse"))
        assert result["strasse"]["mehrdeutig"]
        result = asyncio.run(tools.alarm_ortsdaten_pruefen(context, gemeinde="Wolfurt", ortsteil="Rickenbach"))
        assert result["ortsteil"]["treffer"] == ["Rickenbach"]
        result = asyncio.run(tools.alarm_ortsdaten_pruefen(context, gemeinde="Wolfurt", strasse="Hauptstrasse"))
        assert not result["strasse"]["mehrdeutig"]


def test_versionen_auf_49_begrenzt_und_audit_filter_ohne_inhalte(client, daten):
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        for _i in range(52):
            db.add(AlarmIngest(org_id=daten["org_id"], gateway_id=daten["gateway_id"], raw_hash=uuid4().hex,
                               raw_text="Version", leitstellennummer="f26008809"))
        db.commit()
    result = rufe(client, daten, "alarm_rohdaten_lesen", ingest_id=daten["ingest_id"])
    assert len(result["versionen"]) == 49 and result["truncated"]
    rufe(client, daten, "alarm_rohdaten_liste", leitstellennummer=GEHEIM)
    rufe(client, daten, "alarm_ortsdaten_pruefen", gemeinde=GEHEIM)
    with SessionLocal() as db:
        set_tenant_context(db, daten["org_id"])
        logs = db.query(AuditLog).filter_by(org_id=daten["org_id"], action="alarm_ingest.mcp_gelesen").all()
        assert len(logs) == 3
        assert all("Max" not in log.payload_json and "@" not in log.payload_json for log in logs)
        assert json.loads(logs[0].payload_json)["anzahl"] == 50


def test_ui_org_schalter_cross_org_und_abschalten(diagnose, db):
    client, gw, _, _ = diagnose
    fremd = FireDept(slug=uuid4().hex, name="Fremd")
    db.add(fremd)
    db.flush()
    anderes = Gateway(org_id=fremd.id, name="Fremd")
    db.add(anderes)
    db.commit()
    csrf = client.cookies.get("ec_csrf")
    assert client.post(f"/gateway/{anderes.id}/alarm-parser-mcp",
                       data={"_csrf": csrf, "enabled": "true"}).status_code == 404
    url = f"/gateway/{gw.id}/alarm-parser-mcp"
    assert client.post(url, data={"_csrf": csrf, "enabled": "true"}, follow_redirects=False).status_code == 303
    assert client.post(url, data={"_csrf": csrf}, headers={"HX-Request": "true"}).status_code == 200
    setting = db.query(OrgSettings).filter_by(org_id=gw.org_id).one()
    db.refresh(setting)
    assert not setting.alarm_parser_mcp_aktiv


def test_maskierung_laesst_alarmzeit_stehen_und_maskiert_telefon():
    from app.services.alarm_parser.maskierung import maskiere

    text = "f26007798 f3 buch > waldbrand (03.09.2026 20:28 Uhr) notrufer Max Mustermann 0664 1234567"
    ergebnis = maskiere(text)
    assert "(03.09.2026 20:28 Uhr)" in ergebnis
    assert "1234567" not in ergebnis and "Mustermann" not in ergebnis
    assert "+43 664 1234567" not in maskiere("tel +43 664 1234567")
