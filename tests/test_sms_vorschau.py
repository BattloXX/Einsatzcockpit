"""Nachalarm-Vorschau: Vorlagen, Versandtext, Segmentierung und Org-Isolation."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentOrg
from app.models.incident_alarm_field import IncidentAlarmField
from app.models.master import AlarmType, FireDept, OrgSettings
from app.models.sms import SmsLog
from app.services import sms_dispatch_service as dispatch
from app.services.sms_vorschau_service import lade_sms_vorschau, sms_text_vorschau

ZEIT = datetime(2026, 1, 15, 12, 34, tzinfo=UTC)
VORLAGE = "{stichwort}|{adresse}|{ort}|{meldung}|{einsatzgrund}|{datum}|{zeit}|{link}|{anmeldelink}|{leitstellennummer}|{unbekannt} "


@pytest.fixture
def daten():
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = FireDept(name="SMS Vorschau", slug="sms-vorschau-test", timezone="Europe/Vienna")
        fremd = FireDept(name="Fremde Org", slug="sms-vorschau-fremd")
        db.add_all([org, fremd])
        db.flush()
        einstellungen = OrgSettings(org_id=org.id, einsatzinfo_sms_enabled=True, einsatzinfo_sms_template=VORLAGE)
        alarmtyp = AlarmType(org_id=org.id, code="T9", label="Vorschautest", category="T")
        einsatz = Incident(
            primary_org_id=org.id, nummer=987, started_at=ZEIT.replace(tzinfo=None),
            alarm_type_code="T9", address_street="Dorfstrasse", address_no="5", address_city="Testort",
            report_text="Meldung", reason="Grund", alarm_token="vorschau-token", access_pin_hash="hash",
            lis_operation_number="f26012345", is_exercise=False,
        )
        db.add_all([einstellungen, alarmtyp, einsatz])
        db.flush()
        db.add(IncidentAlarmField(org_id=org.id, incident_id=einsatz.id, field="codes",
                                 value='["r2", "_drehleiter"]', source="alarm"))
        db.flush()
        yield db, org, fremd, einstellungen, alarmtyp, einsatz
        db.rollback()


def test_org_vorlage_alle_felder_und_zusatzcodes(daten):
    db, org, _, _, _, _ = daten
    set_tenant_context(db, org.id)
    vorschau, = lade_sms_vorschau(db, org.id)
    assert vorschau.nummer == 987
    text = vorschau.erstalarm.text
    assert text.startswith("T9|Dorfstrasse 5, Testort|Testort|Meldung|Grund|15.01.2026|13:34|")
    assert "/alarm/vorschau-token|" in text
    assert "/alarm/vorschau-token/anmelden|f26012345|" in text
    assert vorschau.nachalarm.text == "NACHALARMIERUNG r2, _drehleiter " + text
    assert vorschau.nachalarm.herkunft == "Beispiel mit aktueller Vorlage"


def test_alarmtyp_override(daten):
    db, org, _, _, alarmtyp, einsatz = daten
    alarmtyp.einsatzinfo_sms_template = "Eigene Vorlage {stichwort} {meldung}"
    db.flush()
    vorschau, = lade_sms_vorschau(db, org.id)
    assert vorschau.nachalarm.text == "NACHALARMIERUNG r2, _drehleiter Eigene Vorlage T9 Meldung"


def test_uebungen_fehlen_in_der_vorschau(daten):
    db, org, _, _, _, einsatz = daten
    einsatz.is_exercise = True
    db.flush()
    assert lade_sms_vorschau(db, org.id) == []


def test_elemente_zeigen_platzhalter_inhalte(daten):
    db, org, _, _, _, _ = daten
    vorschau, = lade_sms_vorschau(db, org.id)
    elemente = {name: (wert, verwendet) for name, wert, verwendet in vorschau.elemente}
    assert elemente["stichwort"] == ("T9", True)
    assert elemente["meldung"][0] == "Meldung"
    assert elemente["leitstellennummer"][0] == "f26012345"


def test_echte_sms_werden_unveraendert_gezeigt_und_fremde_logs_ignoriert(daten):
    db, org, fremd, _, _, einsatz = daten
    for org_id, variant, text, offset in [
        (org.id, "", "Urspruenglich gesendeter Erstalarm", 0),
        (org.id, "nach:code:r2", "Originaler Nachalarm", 1),
        (org.id, "nach:code:r3", "Letzter Nachalarm", 2),
        (fremd.id, "nach:code:r4", "GEHEIMER FREMDTEXT", 3),
    ]:
        db.add(SmsLog(org_id=org_id, incident_id=einsatz.id, source="alarm", variant=variant,
                      text=text, success_count=1, sent_at=ZEIT.replace(tzinfo=None) + timedelta(minutes=offset)))
    db.flush()
    set_tenant_context(db, org.id)
    vorschau, = lade_sms_vorschau(db, org.id)
    assert vorschau.erstalarm.text == "Urspruenglich gesendeter Erstalarm"
    assert vorschau.nachalarm.text == "Letzter Nachalarm"
    assert vorschau.nachalarm.herkunft == "Tatsächlich gesendet"
    assert vorschau.nachalarm.zeichen == len("Letzter Nachalarm")


def test_org_isolation_kollaboration_limit_und_reihenfolge(daten):
    db, org, fremd, _, _, einsatz = daten
    db.add(Incident(primary_org_id=fremd.id, nummer=9999, started_at=ZEIT + timedelta(days=10)))
    gemeinsam = Incident(primary_org_id=fremd.id, nummer=8888, started_at=ZEIT + timedelta(days=9))
    db.add(gemeinsam)
    db.flush()
    db.add(IncidentOrg(incident_id=gemeinsam.id, org_id=org.id))
    for i in range(6):
        db.add(Incident(primary_org_id=org.id, nummer=1000 + i, started_at=ZEIT + timedelta(days=i + 1)))
    db.flush()
    for kontext in (None, org.id):
        set_tenant_context(db, kontext)
        ergebnis = lade_sms_vorschau(db, org.id)
        assert [v.nummer for v in ergebnis] == [8888, 1005, 1004, 1003, 1002]
        assert ergebnis[0].nachalarm.text.startswith("NACHALARMIERUNG Beispiel-Code ")
        assert einsatz.nummer not in [v.nummer for v in ergebnis]


def test_keine_einsaetze(daten):
    db, _, fremd, *_ = daten
    assert lade_sms_vorschau(db, fremd.id) == []


def test_unbestaetigtes_protokoll_nicht_als_gesendet_markiert(daten):
    db, org, _, _, _, einsatz = daten
    db.add(SmsLog(org_id=org.id, incident_id=einsatz.id, source="alarm", variant="nach:code:r2",
                  text="Noch wartend", success_count=0))
    db.flush()
    vorschau, = lade_sms_vorschau(db, org.id)
    assert vorschau.nachalarm.text == "Noch wartend"
    assert "unbestätigt" in vorschau.nachalarm.herkunft


@pytest.mark.parametrize("text,segmente,zeichensatz", [
    ("a" * 160, 1, "GSM-7"), ("a" * 161, 2, "GSM-7"),
    ("a" * 306, 2, "GSM-7"), ("a" * 307, 3, "GSM-7"),
    ("^" * 80, 1, "GSM-7"), ("€" * 81, 2, "GSM-7"),
    ("漢" * 70, 1, "Unicode"), ("漢" * 71, 2, "Unicode"),
    ("漢" * 134, 2, "Unicode"), ("漢" * 135, 3, "Unicode"),
    ("\U0001f600" * 36, 2, "Unicode"), ("", 0, "GSM-7"),
])
def test_sms_segmentgrenzen(text, segmente, zeichensatz):
    result = sms_text_vorschau(text)
    assert (result.zeichen, result.segmente, result.zeichensatz) == (len(text), segmente, zeichensatz)


@pytest.mark.asyncio
@pytest.mark.parametrize("override,uebung", [(None, False), ("Override " + VORLAGE, False)])
async def test_refactor_versandtext_entspricht_vorschau(daten, monkeypatch, override, uebung):
    db, org, _, einstellungen, alarmtyp, einsatz = daten
    alarmtyp.einsatzinfo_sms_template = override
    einsatz.is_exercise = uebung
    db.flush()
    vorschau, = lade_sms_vorschau(db, org.id)

    class FesteZeit(datetime):
        @classmethod
        def now(cls, tz=None):
            return ZEIT

    mock_db = MagicMock()

    def query(model):
        q = MagicMock()
        q.filter.return_value.first.return_value = {
            Incident: einsatz, OrgSettings: einstellungen, AlarmType: alarmtyp,
        }.get(model)
        q.filter.return_value.with_for_update.return_value.first.return_value = einsatz
        return q

    mock_db.query.side_effect = query
    mock_db.get.return_value = org
    monkeypatch.setattr(dispatch, "datetime", FesteZeit)
    monkeypatch.setattr(dispatch, "current_read_session", lambda factory: mock_db)
    monkeypatch.setattr(dispatch, "collect_einsatzinfo_recipients", lambda *a: {
        "+430000": SimpleNamespace(id=1, full_name="Test"),
    })
    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *a, **kw: True)
    monkeypatch.setattr("app.services.sms_outbox.queue_log", lambda *a: [])
    monkeypatch.setattr("app.services.sms_service.resolve_sms_config", lambda *a: None)
    monkeypatch.setattr("app.services.sms_service.sms_available", lambda *a: False)
    await dispatch.dispatch_einsatzinfo(
        org.id, "T9", "Dorfstrasse 5, Testort", "Testort", "Meldung", "Grund", uebung,
        link=vorschau.erstalarm.text.split("|")[7],
        leitstellennummer="f26012345", incident_id=einsatz.id,
        variant="nach:code:r2+_drehleiter", text_prefix="NACHALARMIERUNG r2, _drehleiter ",
    )
    log = next(call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], SmsLog))
    assert log.text == vorschau.nachalarm.text


def test_admin_seite_leerhinweis_und_escaped_sms(client):
    from app.models.user import User
    from tests.test_sms_groups import _create_admin

    username = _create_admin()
    client.get("/login")
    response = client.post("/login", data={
        "username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    assert response.status_code == 302
    response = client.get("/admin/einsatzinfo-sms")
    assert response.status_code == 200
    assert "Noch keine Einsätze für diese Organisation vorhanden." in response.text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org_id = db.query(User).filter(User.username == username).one().org_id
        db.add(Incident(primary_org_id=org_id, nummer=54321, report_text="<script>alarm()</script>"))
        db.commit()
    response = client.get("/admin/einsatzinfo-sms")
    assert response.status_code == 200
    assert "Vorschau: SMS bei einer Änderung (Nachalarmierung)" in response.text
    assert "Einsatz 54321" in response.text
    assert "NACHALARMIERUNG Beispiel-Code" in response.text
    assert "&lt;script&gt;alarm()&lt;/script&gt;" in response.text
    assert "<script>alarm()</script>" not in response.text
