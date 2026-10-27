"""P5a: Diagnose, Tenant-Grenzen und manuelle Wiederverarbeitung."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.core.timezones import local_date_to_utc, now_local
from app.models.gateway import AlarmIngest, AlarmParserRegelsatz, Gateway
from app.models.incident import Incident, IncidentChange, IncidentLog
from app.models.incident_alarm_field import IncidentAlarmField
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import AuditLog, Role, User, UserRole
from app.services import alarm_apply_service as service
from app.services.dienst_monitor_service import alarmempfang_warnungen
from tests import test_alarm_apply_service as apply_tests
from tests import test_gateway_alarm_apply_nachlauf as gateway_tests
from tests.test_alarm_apply_service import apply, incident, nummer, store
from tests.test_gateway_druckregeln_render import _login

db = apply_tests.db
alarm_api = gateway_tests.alarm_api


@pytest.fixture
def diagnose(db, client, monkeypatch):
    gw = db.info["gateway"]
    flag = db.get(SystemSettings, "gateway_module_enabled")
    if flag is None:
        db.add(SystemSettings(key="gateway_module_enabled", value="true"))
    else:
        flag.value = "true"
    db.add(OrgSettings(org_id=gw.org_id, gateway_module_enabled=True))
    user = User(username=uuid4().hex, display_name="Diagnose Admin", org_id=gw.org_id,
                password_hash=hash_password("Test1234!"), active=True)
    db.add(user)
    db.flush()
    role = db.query(Role).filter_by(code="org_admin").one()
    db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()
    messages = []

    async def broadcast(org_id, event):
        messages.append((org_id, event))

    monkeypatch.setattr("app.services.broadcast.broadcast_org", broadcast)
    for name in ("autoprint_alarm_background", "autoprint_incident_background"):
        monkeypatch.setattr(f"app.services.print_dispatcher.{name}", lambda *a, **kw: None)
    monkeypatch.setattr("app.routers.api_v1._geocode_incident", lambda *a: None)
    monkeypatch.setattr("app.services.objekt_matching_service.match_incident_background", lambda *a: None)
    assert _login(client, user.username, "Test1234!").status_code in (302, 303)
    return client, gw, user, messages


def post(diagnose, row, action):
    client, gw, _, _ = diagnose
    return client.post(f"/gateway/{gw.id}/alarmempfang/{row.id}/{action}",
                       data={"_csrf": client.cookies.get("ec_csrf")}, headers={"HX-Request": "true"})


@pytest.mark.parametrize("role, expected", [("recorder", 403), ("readonly", 403),
                                             ("org_admin", 200), ("admin", 200)])
def test_rollen(diagnose, db, role, expected):
    client, gw, user, _ = diagnose
    ur = db.query(UserRole).filter_by(user_id=user.id).one()
    ur.role_id = db.query(Role).filter_by(code=role).one().id
    db.commit()
    row = store(db, nummer() + " t1 > wasser")
    base = f"/gateway/{gw.id}/alarmempfang"
    response = client.get(base)
    assert response.status_code == expected
    if expected == 200:
        assert 'hx-trigger="every 15s' in response.text and 'name="status"' in response.text
        assert f'href="{base}"' in client.get(f"/gateway/{gw.id}").text
    assert client.get(f"{base}/{row.id}").status_code == expected
    if expected == 403:
        assert post(diagnose, row, "neu-verarbeiten").status_code == 403
        assert post(diagnose, row, "geprueft").status_code == 403


def test_modul_guard(diagnose, db):
    client, gw, _, _ = diagnose
    db.query(OrgSettings).filter_by(org_id=gw.org_id).one().gateway_module_enabled = False
    db.commit()
    assert client.get(f"/gateway/{gw.id}/alarmempfang").status_code == 404


def test_cross_org_und_fremdes_gateway(diagnose, db):
    client, gw, user, _ = diagnose
    fremde_org = FireDept(slug=uuid4().hex, name="Fremd")
    db.add(fremde_org)
    db.flush()
    fremd = Gateway(org_id=fremde_org.id, name="Fremdes Gateway")
    anderes = Gateway(org_id=gw.org_id, name="Anderes Gateway")
    db.add_all([fremd, anderes])
    db.commit()
    fremd_row = store(db, nummer() + " t1 > geheim", gw=fremd)
    anderes_row = store(db, nummer() + " t1 > anderer Empfang", gw=anderes)
    for gateway_id, row in ((fremd.id, fremd_row), (gw.id, fremd_row), (gw.id, anderes_row)):
        base = f"/gateway/{gateway_id}/alarmempfang"
        if gateway_id == fremd.id:
            assert client.get(base).status_code == 404
        assert client.get(f"{base}/{row.id}").status_code == 404
        for aktion in ("neu-verarbeiten", "geprueft"):
            response = client.post(f"{base}/{row.id}/{aktion}",
                                   data={"_csrf": client.cookies.get("ec_csrf")})
            assert response.status_code == 404
    with pytest.raises(ValueError, match="Organisation"):
        service.verarbeite_neu(db, fremd_row, gw.org_id, user)


def test_filter_zeit_nummer_und_limit(diagnose, db):
    client, gw, _, _ = diagnose
    rows = [store(db, nummer() + " t1 > wasser") for _ in range(3)]
    for i, row in enumerate(rows):
        row.leitstellennummer = f"FILTER-{i}"
        row.process_status = "error" if i != 1 else "needs_review"
    rows[2].received_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)
    db.commit()
    base = f"/gateway/{gw.id}/alarmempfang"
    def get(**params):
        response = client.get(base, params=params, headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert '<html' not in response.text
        return response.text
    text = get(status="error", zeitraum="30", nummer="ILTER-2")
    assert f'/alarmempfang/{rows[2].id}"' in text
    assert f'/alarmempfang/{rows[0].id}"' not in text
    assert f'/alarmempfang/{rows[2].id}"' not in get(zeitraum="7")
    assert f'/alarmempfang/{rows[1].id}"' in get(status="needs_review")
    assert client.get(base, params={"status": "reviewed"}).status_code == 400
    assert client.get(base, params={"zeitraum": "365"}).status_code == 400
    for i in range(205):
        db.add(AlarmIngest(org_id=gw.org_id, gateway_id=gw.id, raw_hash=str(i), raw_text="test",
                           parse_status="parsed", process_status="stored"))
    db.commit()
    assert get().count('<tr><td><a href=') == 200


@pytest.mark.parametrize("raw", [None, b"\x00\xff\xfe<script>\x1b", bytes(range(256)) * 20],
                         ids=["keine", "binaermuell", "gekuerzt"])
def test_detail_hex_escaping_und_versionen(diagnose, db, raw):
    client, gw, _, _ = diagnose
    first = store(db, nummer() + " t1 > <script>alert(1)</script>")
    first.raw_bytes = raw
    first.leitstellennummer = "VERSION-TEST"
    first.version_no = 1
    first.parsed = {"hinweise": ["unklar"], "rest_text": "Rest", "qualitaet": 42,
                    "gateway_parsed": {"city": "Vergleichsort"}}
    second = store(db, nummer() + " t1 > folge")
    second.leitstellennummer = first.leitstellennummer
    second.version_no = 2
    db.commit()
    response = client.get(f"/gateway/{gw.id}/alarmempfang/{first.id}")
    assert response.status_code == 200
    text = response.text
    assert '<script>alert(1)</script>' not in text and '&lt;script&gt;' in text
    assert 'Vergleichsort' in text and 'Rest' in text and '42' in text
    assert f'/alarmempfang/{second.id}"' in text
    if raw is None:
        assert 'Keine Rohbytes vorhanden' in text
    else:
        assert raw[:min(16, len(raw))].hex(' ') in text
    if raw and len(raw) > 4096:
        assert 'Anzeige auf die ersten 4 KiB' in text and '0ff0  ' in text and '1000  ' not in text


def test_zaehler_org_tagesgrenze(diagnose, db):
    client, gw, user, _ = diagnose
    org = db.get(FireDept, gw.org_id)
    org.timezone = "Pacific/Auckland"
    start = local_date_to_utc(now_local(org).date().isoformat(), org=org)
    rows = [store(db, nummer() + " t1 > wasser") for _ in range(5)]
    for row, status in zip(rows, ["applied", "error", "needs_review", "applied", "error"], strict=True):
        row.received_at = start + timedelta(seconds=1)
        row.process_status = status
    rows[3].dedup_action = "duplicate"
    rows[4].received_at = start - timedelta(seconds=1)
    db.commit()
    from app.routers.ui_gateway import _alarmempfang_context
    ctx = _alarmempfang_context(db, gw, user, "alle", "heute", "")
    assert ctx["zaehler"] == {"empfangen": 4, "verarbeitet": 2, "fehlerhaft": 2, "duplikate": 1}
    assert ctx["letzter_empfang"] == rows[0].received_at
    assert client.get(f"/gateway/{gw.id}/alarmempfang").status_code == 200


def test_neu_verarbeiten_herkunft_idempotent_ohne_nachlauf(diagnose, db, monkeypatch):
    _, _, user, messages = diagnose
    row = apply(db, nummer() + " t1 > wasser", parsed={"street": "Altgasse", "city": "Wolfurt"})
    inc = incident(db, row)
    inc.address_street = "Manuellgasse"
    service.markiere_manuell(db, inc.id, row.org_id, "address_street")
    row.raw_text = row.raw_text.replace(" t1 ", " t2,r2 ") + " > neue Information"
    version = row.version_no
    db.commit()
    for name in ("enqueue_nachalarmierung", "enqueue_incident_alarm"):
        monkeypatch.setattr(f"app.services.alarm_outbox.{name}",
                            lambda *a, **kw: pytest.fail("Keine Alarmierung bei Wiederverarbeitung"))
    monkeypatch.setattr(service, "nachlauf", lambda *a, **kw: pytest.fail("Kein Drucknachlauf"))
    assert post(diagnose, row, "neu-verarbeiten").status_code == 200
    db.expire_all()
    assert inc.address_street == "Manuellgasse"
    assert "neue Information" in inc.report_text
    assert inc.alarm_type_code == "T2"
    before = (inc.report_text, row.parsed, row.process_attempts, row.version_no,
              db.query(IncidentLog).filter_by(incident_id=inc.id).count(),
              db.query(IncidentChange).filter_by(incident_id=inc.id).count())
    assert post(diagnose, row, "neu-verarbeiten").status_code == 200
    db.expire_all()
    after = (inc.report_text, row.parsed, row.process_attempts, row.version_no,
             db.query(IncidentLog).filter_by(incident_id=inc.id).count(),
             db.query(IncidentChange).filter_by(incident_id=inc.id).count())
    assert before == after and row.version_no == version
    assert db.query(Incident).filter_by(primary_org_id=row.org_id).count() == 1
    assert db.query(IncidentLog).filter_by(incident_id=inc.id,
        text=f"Alarmdaten manuell neu verarbeitet von {user.username}").count() == 1
    assert db.query(AuditLog).filter_by(org_id=row.org_id, user_id=user.id,
        entity_id=row.id, action="alarm_ingest.neu-verarbeiten").count() == 2
    assert messages[-1] == (row.org_id, {"type": "alarm_ingest_changed", "gateway_id": row.gateway_id,
                                       "ingest_id": row.id})


def test_neu_ohne_einsatz_und_aktiver_regelsatz(diagnose, db):
    _, _, _, _ = diagnose
    row = store(db, nummer() + " t1 > wasser")
    row.process_status = "needs_review"
    db.commit()
    assert post(diagnose, row, "neu-verarbeiten").status_code == 200
    db.refresh(row)
    assert row.einsatz_id and row.process_status == "applied" and row.version_no == 1
    einsatz_id = row.einsatz_id
    # Den aktuell aktiven Satz nach der Erstanlage wechseln.
    from app.services.alarm_parser.laufzeit import invalidiere_regelsatz
    from app.services.alarm_parser.regelsatz import STANDARD_REGELSATZ
    db.add(AlarmParserRegelsatz(org_id=row.org_id, version=88, status="aktiv",
                               regeln=STANDARD_REGELSATZ.model_dump(mode="json")))
    db.commit()
    invalidiere_regelsatz(row.org_id)
    assert post(diagnose, row, "neu-verarbeiten").status_code == 200
    db.refresh(row)
    assert row.parser_version == "88" and row.einsatz_id == einsatz_id and row.version_no == 1
    assert db.query(Incident).filter_by(primary_org_id=row.org_id).count() == 1


@pytest.mark.parametrize("mit_einsatz", [False, True])
def test_geprueft_csrf_audit(diagnose, db, mit_einsatz):
    client, gw, user, _ = diagnose
    row = apply(db, nummer() + " t1 > wasser") if mit_einsatz else store(db, nummer() + " t1 > wasser")
    row.process_status = "needs_review"
    row.process_error = "Bitte pruefen"
    db.commit()
    assert client.post(f"/gateway/{gw.id}/alarmempfang/{row.id}/neu-verarbeiten").status_code == 403
    url = f"/gateway/{gw.id}/alarmempfang/{row.id}/geprueft"
    assert client.post(url).status_code == 403
    assert client.post(url, data={"_csrf": "falsch"}).status_code == 403
    assert post(diagnose, row, "geprueft").status_code == 200
    db.refresh(row)
    assert row.process_error is None
    assert row.process_status == ("applied" if mit_einsatz else "needs_review")
    assert db.query(AuditLog).filter_by(org_id=gw.org_id, user_id=user.id, entity_id=row.id,
                                      action="alarm_ingest.geprueft").count() == 1
    if mit_einsatz:
        assert post(diagnose, row, "geprueft").status_code == 409


def test_processing_gesperrt_und_service_tenant(diagnose, db):
    _, gw, user, _ = diagnose
    row = store(db, nummer() + " t1 > wasser")
    row.process_status = "processing"
    db.commit()
    assert post(diagnose, row, "neu-verarbeiten").status_code == 409
    with pytest.raises(ValueError, match="Organisation"):
        service.verarbeite_neu(db, row, gw.org_id + 100, user)


def test_nur_alarm_auch_fehlende_herkunft_geschuetzt(diagnose, db):
    _, _, user, _ = diagnose
    row = apply(db, nummer() + " t1 > wasser")
    inc = incident(db, row)
    feld = db.query(IncidentAlarmField).filter_by(org_id=row.org_id, incident_id=inc.id,
                                                 field="address_city").one()
    db.delete(feld)
    db.commit()
    service.merge_folgenachricht(db, inc, row, {"city": "Andere Stadt"}, row.org_id, nur_alarm=True)
    assert inc.address_city is None


def test_warnung_ab_fuenf_org_und_zeit_gefiltert(diagnose, db):
    client, gw, _, _ = diagnose
    now = datetime.now(UTC).replace(tzinfo=None)
    for i in range(5):
        row = store(db, nummer() + " t1 > wasser")
        row.received_at = now - timedelta(minutes=i)
        row.process_status = "error" if i % 2 else "needs_review"
        db.commit()
        assert bool(alarmempfang_warnungen(db, gw.org_id, now)) == (i == 4)
    response = client.get("/admin/systemstatus/liste")
    assert response.status_code == 200 and "5 fehlerhafte oder zu pruefende Ingests" in response.text
    assert alarmempfang_warnungen(db, gw.org_id + 1000, now) == []
    assert alarmempfang_warnungen(db, gw.org_id, now + timedelta(hours=25)) == []
    from app.services.dienst_monitor_service import pruefe_dienste
    assert not any(c.key == "alarm_ingest" for c in pruefe_dienste(db, gw.org_id))


def test_recovery_broadcast(diagnose, db):
    _, _, _, messages = diagnose
    row = store(db, nummer() + " t1 > wasser")
    row.received_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()
    from app.services.alarm_ingest_recovery import run_alarm_ingest_recovery_once
    run_alarm_ingest_recovery_once()
    assert (row.org_id, {"type": "alarm_ingest_changed", "gateway_id": row.gateway_id,
                         "ingest_id": row.id}) in messages


@pytest.mark.parametrize("fehler", [False, True])
def test_ingest_router_broadcast_auch_bei_fehler(alarm_api, monkeypatch, fehler):
    post_alarm, gateway_id, _ = alarm_api
    messages = []

    async def broadcast(org_id, event):
        messages.append((org_id, event))

    monkeypatch.setattr("app.services.broadcast.broadcast_org", broadcast)
    if fehler:
        def fail(*args, **kwargs):
            raise ValueError("Verarbeitung fehlgeschlagen")
        monkeypatch.setattr("app.services.serial_alarm_service._create_or_link_incident", fail)
    response = post_alarm({"raw_text": nummer() + " t1 > wasser"})
    assert response.status_code == 200
    result = response.json()
    assert result["process_status"] == ("error" if fehler else "applied")
    assert (1, {"type": "alarm_ingest_changed", "gateway_id": gateway_id,
                "ingest_id": result["ingest_id"]}) in messages
