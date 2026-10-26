"""P4b: Folgenachrichten, Herkunftsschutz und begrenzter Nachlauf."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from app.core.audit import write_incident_change
from app.core.tenant import TenantContextMissing, set_tenant_context
from app.models.incident import Incident, IncidentChange, IncidentLog
from app.models.incident_alarm_field import IncidentAlarmField
from app.models.master import AlarmType, FireDept
from app.models.user import ApiKey, User
from app.services import alarm_apply_service as service
from app.services import alarm_ingest_recovery as recovery
from tests import test_alarm_apply_service as apply_tests
from tests import test_gateway_alarm_apply_nachlauf as gateway_tests
from tests.test_alarm_apply_service import apply, incident, nummer, store

db = apply_tests.db
alarm_api = gateway_tests.alarm_api


@pytest.fixture(autouse=True)
def merge_katalog(db):
    db.add_all([AlarmType(org_id=db.info["gateway"].org_id, code=code, category=category)
                for code, category in (("F2", "F"), ("F3", "F"), ("T4", "T"))])
    db.commit()


def adresse(street="Testgasse", no="7"):
    # Ohne Strassenkatalog erfindet der Parser keine sichere Adresse; hier
    # den weiterhin unterstuetzten strukturierten Gateway-Fallback pruefen.
    return {"street": street, "house_no": no, "city": "Wolfurt"}


def herkunft(db, inc, feld):
    return db.query(IncidentAlarmField).filter_by(
        incident_id=inc.id, org_id=inc.primary_org_id, field=feld,
    ).one()


def logs(db, inc):
    return [row.text for row in db.query(IncidentLog).filter_by(incident_id=inc.id).all()]


def test_zusatzinfo_version_journal_change_und_duplicate(db):
    text = nummer() + " t1 Wolfurt Testgasse 7 > wasser"
    first = apply(db, text)
    inc = incident(db, first)
    original = inc.report_text
    assert all(herkunft(db, inc, feld).source == "alarm" for feld in service.ALARM_FELDER)
    changed = apply(db, text + " > Person im Keller")
    assert changed.version_no == 2 and changed.einsatz_id == first.einsatz_id
    assert inc.report_text.startswith(original + "\n\n--- Nachtrag v2 ")
    assert inc.report_text.endswith("---\nPerson im Keller")
    assert any("Alarmupdate v2: Meldebild ergaenzt" in text for text in logs(db, inc))
    change = db.query(IncidentChange).filter_by(incident_id=inc.id, action="alarm.update").one()
    assert json.loads(change.before_json) == {"report_text": original}
    assert json.loads(change.after_json) == {"report_text": inc.report_text}
    assert change.user_id is None and change.api_key_id is None
    assert herkunft(db, inc, "report_text").source == "alarm"
    duplicate = apply(db, text + " > Person im Keller")
    assert duplicate.dedup_action == "duplicate" and duplicate.version_no == 2
    assert inc.report_text.count("Person im Keller") == 1
    assert db.query(Incident).filter_by(primary_org_id=first.org_id).count() == 1


def test_manuelle_adresse_auch_leer_geschuetzt_hinweis_einmal(db):
    number = nummer()
    first = apply(db, number + " t1 Wolfurt Testgasse 7 > wasser", parsed=adresse())
    inc = incident(db, first)
    old = inc.address_street
    inc.address_street = None
    key = ApiKey(org_id=first.org_id, key_hash=nummer(), label="Merge")
    db.add(key)
    db.flush()
    write_incident_change(db, inc.id, "incident.address_updated", "incident", inc.id,
                          {"address_street": old}, {"address_street": None}, api_key_id=key.id)
    db.commit()
    assert herkunft(db, inc, "address_street").source == "manual"
    apply(db, number + " t1 Wolfurt Neuegasse 7 > wasser > neu", parsed=adresse("Neuegasse"))
    apply(db, number + " t1 Wolfurt Neuegasse 7 > wasser > nochmals neu", parsed=adresse("Neuegasse"))
    assert inc.address_street is None
    assert logs(db, inc).count("Abweichender Alarmwert fuer address_street: Neuegasse") == 1


def test_alarm_adresse_update_und_nachlauf_ohne_alarmierung(db, monkeypatch):
    number = nummer()
    first = apply(db, number + " t1 Wolfurt Testgasse 7 > wasser", parsed=adresse())
    inc = incident(db, first)
    inc.lat, inc.lng = 47.1, 9.1
    db.commit()
    changed = apply(db, number + " t1 Wolfurt Neuegasse 9 > wasser", parsed=adresse("Neuegasse", "9"))
    assert inc.lat is None and inc.lng is None
    assert inc.address_street == "Neuegasse" and inc.address_no == "9"
    assert changed.merge_ergebnis.adresse_geaendert
    calls = []
    monkeypatch.setattr(service, "nach_adressaenderung", lambda *args: calls.append(args[0]))
    monkeypatch.setattr("app.services.alarm_outbox.enqueue_incident_alarm", lambda *a, **kw: pytest.fail("P4c"))
    messages = []
    async def broadcast(*args):
        messages.append(args)
    monkeypatch.setattr("app.services.broadcast.broadcast_org", broadcast)
    monkeypatch.setattr("app.services.broadcast.manager.broadcast", broadcast)
    service.nachlauf(db, changed, org_id=changed.org_id, background_tasks=BackgroundTasks(), base_url="")
    assert calls == [inc.id]
    assert len(messages) == 2 and all(args[1]["type"] == "incident_updated" for args in messages)


def test_nach_adressaenderung_tasks():
    tasks = BackgroundTasks()
    service.nach_adressaenderung(42, tasks)
    assert [task.func.__name__ for task in tasks.tasks] == ["_geocode_adressaenderung", "match_incident_background"]
    assert all(task.args == (42,) for task in tasks.tasks)


def test_stichwort_erhoehen_nicht_senken_oder_kategorie_wechseln(db):
    number = nummer()
    first = apply(db, number + " f2 > brand")
    inc = incident(db, first)
    higher = apply(db, number + " f3 > brand")
    assert inc.alarm_type_code == "F3" and higher.merge_ergebnis.stichwort_erhoeht
    assert "Alarmupdate v2: Stichwort F2 -> F3" in logs(db, inc)
    lower = apply(db, number + " f2 > brand > kleiner")
    assert inc.alarm_type_code == "F3" and not lower.merge_ergebnis.stichwort_erhoeht
    other = apply(db, number + " t4 > brand")
    assert inc.alarm_type_code == "F3" and not other.merge_ergebnis.stichwort_erhoeht
    assert "Abweichender Alarmwert fuer alarm_type_code: T4" in logs(db, inc)
    changes = db.query(IncidentChange).filter_by(incident_id=inc.id, action="alarm.update").all()
    assert sum("alarm_type_code" in json.loads(row.after_json) for row in changes) == 1


def test_org_kategorie_beachten(db):
    first = apply(db, nummer() + " f2 > brand")
    inc = incident(db, first)
    db.query(AlarmType).filter_by(org_id=first.org_id, code="F3").one().category = "Sonder"
    db.commit()
    changed = apply(db, first.leitstellennummer + " f3 > brand")
    assert inc.alarm_type_code == "F2" and not changed.merge_ergebnis.stichwort_erhoeht


def test_manuelles_stichwort_und_meldebild(db):
    first = apply(db, nummer() + " f2 > brand")
    inc = incident(db, first)
    service.markiere_manuell(db, inc.id, first.org_id, "alarm_type_code")
    service.markiere_manuell(db, inc.id, first.org_id, "report_text")
    db.commit()
    original = inc.report_text
    changed = apply(db, first.leitstellennummer + " f3 > grosser Brand")
    assert inc.alarm_type_code == "F2" and not changed.merge_ergebnis.stichwort_erhoeht
    assert inc.report_text == original
    assert "Abweichender Alarmwert fuer alarm_type_code: F3" in logs(db, inc)
    assert "Abweichender Alarmwert fuer report_text: grosser Brand" in logs(db, inc)


def test_codes_nur_neue_erfassen(db):
    first = apply(db, nummer() + " f2,r1 > brand")
    inc = incident(db, first)
    changed = apply(db, first.leitstellennummer + " f2,r1,r2 _drehleiter > brand")
    assert changed.merge_ergebnis.neue_codes == ["r2", "_drehleiter"]
    assert json.loads(herkunft(db, inc, "codes").value) == ["r1", "r2", "_drehleiter"]
    again = apply(db, first.leitstellennummer + " f2,r2 _drehleiter > brand > Zusatz")
    assert again.merge_ergebnis.neue_codes == []


def test_cross_org_und_tenant_listener(db):
    first = apply(db, nummer() + " t1 > wasser")
    inc = incident(db, first)
    other = FireDept(slug=nummer(), name="Andere")
    db.add(other)
    db.commit()
    service.markiere_manuell(db, inc.id, other.id, "report_text")
    assert herkunft(db, inc, "report_text").source == "alarm"
    with pytest.raises(ValueError, match="Organisation"):
        service.merge_folgenachricht(db, inc, first, {}, other.id)
    set_tenant_context(db, other.id)
    assert db.query(IncidentAlarmField).filter_by(incident_id=inc.id).all() == []
    db.info.pop("current_org_id")
    with pytest.raises(TenantContextMissing):
        db.query(IncidentAlarmField).all()
    set_tenant_context(db, None)


def test_recovery_adressnachlauf(db, monkeypatch):
    number = nummer()
    apply(db, number + " t1 Wolfurt Testgasse 7 > wasser", parsed=adresse())
    row = store(db, number + " t1 Wolfurt Neuegasse 9 > wasser", parsed=adresse("Neuegasse", "9"))
    row.received_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()
    calls = []
    monkeypatch.setattr(service, "nach_adressaenderung", lambda *a: calls.append(a[0]))
    recovery.run_alarm_ingest_recovery_once()
    db.refresh(row)
    assert row.process_status == "applied" and row.dedup_action == "merged"
    assert calls == [row.einsatz_id]


@pytest.mark.parametrize("route", ["alarm", "adresse"])
def test_ui_aenderungen_markieren_nur_geaenderte_felder(db, monkeypatch, route):
    from app.routers import ui_incident

    first = apply(db, nummer() + " f2 > brand", parsed=adresse())
    inc = incident(db, first)
    user = User(username=nummer(), display_name="Bearbeiter", org_id=first.org_id)
    db.add(user)
    db.commit()
    request = SimpleNamespace(state=SimpleNamespace(user=user), client=None)
    monkeypatch.setattr(ui_incident.templates, "TemplateResponse", lambda *a, **kw: None)
    monkeypatch.setattr("app.core.permissions.can_access_incident", lambda *a: True)
    tasks = BackgroundTasks()
    if route == "alarm":
        ui_incident.alarm_save(inc.id, request, tasks, alarm_type_code="F3", reason="korrigiert",
                               report_text="Manuelle Meldung", db=db)
        felder = ("alarm_type_code", "reason", "report_text")
    else:
        ui_incident.address_save(inc.id, request, tasks, address_street="Manuellgasse", address_no="7",
                                 address_city="Wolfurt", lat="", lng="", lagekarte_shash_url="", db=db)
        felder = ("address_street",)
        assert herkunft(db, inc, "address_no").source == "alarm"
    assert all(herkunft(db, inc, feld).source == "manual" for feld in felder)
    change = db.query(IncidentChange).filter_by(incident_id=inc.id, user_id=user.id).one()
    assert change.action == ("incident.alarm_updated" if route == "alarm" else "incident.address_updated")
    apply(db, first.leitstellennummer + " f3 > neue Alarmmeldung", parsed=adresse("Alarmgasse"))
    if route == "alarm":
        assert inc.report_text == "Manuelle Meldung" and inc.reason == "korrigiert"
    else:
        assert inc.address_street == "Manuellgasse"


def test_gateway_merged_adresse_nur_adressnachlauf(alarm_api, monkeypatch):
    post, _, calls = alarm_api
    number = nummer()
    first = post({"raw_text": number + " t1 > wasser", "sequence": 1, "parsed": adresse()}).json()
    calls.clear()
    monkeypatch.setattr(service, "nach_adressaenderung", lambda *a: calls.append("adressnachlauf"))
    changed = post({"raw_text": number + " t1 > wasser > neu", "sequence": 2,
                    "parsed": adresse("Neuegasse", "9")}).json()
    assert first["einsatz_id"] == changed["einsatz_id"]
    assert gateway_tests.saved(changed["ingest_id"]).dedup_action == "merged"
    assert calls == ["adressnachlauf", "broadcast"]
