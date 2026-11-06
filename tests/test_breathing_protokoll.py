"""Protokoll: alle Zyklen, Audit-Ergänzung, Druckwege und konstante Abfragezahl."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from starlette.requests import Request

from app.core.templating import templates
from app.models.breathing import (
    BreathingAssp, BreathingDeployment, BreathingKontrolle, BreathingTroop,
    BreathingWarnung, BreathingZustellung, PressureLog, TroopMember,
)
from app.models.incident import Incident, IncidentChange
from app.models.master import FireDept, OrgSettings
from app.services.breathing_protokoll import gruppiere_nach_zyklus, load_protokoll_context, troop_protokoll_ereignisse
from app.services.breathing_service import create_troop
from tests.test_breathing_routes import breathing_routes  # noqa: F401

NOW = datetime(2026, 10, 1, 9)


def audit(action, minutes, after=None, member=False, **kwargs):
    return IncidentChange(id=minutes + 100, incident_id=1, action=f"troop.{action}",
                          ts=NOW + timedelta(minutes=minutes), entity_type="troop_member" if member else "breathing_troop",
                          entity_id=11 if member else 1, user_id=7, after_json=json.dumps(after or {}), **kwargs)


def sample():
    old = TroopMember(id=11, free_text_name="Alt", deployment_id=10, objective_press=210,
                      objective_press_at=NOW + timedelta(minutes=5), withdraw_press=120, back_press=70)
    current = TroopMember(id=12, free_text_name="Neu", start_press=300, withdraw_press=80)
    deployment = BreathingDeployment(id=10, lfd_nr=1, status="zurueck", entry_at=NOW,
        withdraw_at=NOW + timedelta(minutes=15), back_at=NOW + timedelta(minutes=20),
        readiness_override_reason="Dringend", readiness_override_at=NOW - timedelta(seconds=1),
        readiness_override_by_user_id=7, members=[old])
    troop = BreathingTroop(id=1, name="Testtrupp", status="im_einsatz", entry_at=NOW + timedelta(hours=1),
                           members=[old, current], deployments=[deployment],
                           funk_verloren_at=NOW + timedelta(minutes=65), notfall_at=NOW + timedelta(minutes=66),
                           notfall_von_user_id=7, escalated_at=NOW + timedelta(minutes=67))
    troop.pressure_logs = [PressureLog(troop_member_id=11, ts=NOW + timedelta(minutes=3), pressure_bar=250,
                                     note="Funk", recorded_by_user_id=7, nachgetragen=True)]
    troop.kontrollen = [BreathingKontrolle(deployment_id=10, stufe="one_third", art="funk",
        niedrigster_druck=180, erledigt_at=NOW + timedelta(minutes=10), nachgetragen=True, user_id=7, note="Kontrolle alt")]
    return troop


def test_sources_cycles_order_actors_retroactive_and_no_duplicates():
    troop = sample()
    rows = [audit("pressure_logged", 3, {"pressure_bar": 250}, member=True),
            audit("kontrolle", 10, {"stufe": "one_third"}),
            audit("started", 0), audit("status_changed", 15, {"status": "rueckzug"}),
            audit("objective_reached", 5, {"pressure_bar": 210, "nachgetragen": True}, member=True),
            audit("back_pressure_reported", 21, {"pressure_bar": 70}, member=True),
            audit("meldung", 7, {"text": "Lage stabil", "nachgetragen": True}),
            audit("standort", 8, {"location_text": "Keller"}), audit("auftrag", 9, {"task_text": "Suche"}),
            audit("funk_verloren", 12, {"active": True}), audit("funk_verloren", 14, {"active": False}),
            audit("notfall", 16, {"active": True}), audit("notfall_cleared", 18, {"active": False}),
            audit("details", 2, {"funkkanal": "Kanal 3"}),
            audit("warnung_zugestellt", 17, {"status": "sent"})]
    warnung = BreathingWarnung(troop_id=1, kind="notfall", zyklus_start=NOW,
        ausgeloest_at=NOW + timedelta(minutes=16), erledigt_at=NOW + timedelta(minutes=19),
        eskaliert_at=NOW + timedelta(minutes=17), eskalation_stufe=1, zustellungen=[
            BreathingZustellung(stufe=1, kanal="sms", user_id=7, empfaenger_label="Leitung", status="failed",
                               fehler="Kein Netz", created_at=NOW + timedelta(minutes=17)),
            BreathingZustellung(stufe=0, kanal="push", user_id=7, status="sent",
                               created_at=NOW + timedelta(minutes=16), gesendet_at=NOW + timedelta(minutes=17)),
        ])
    events = troop_protokoll_ereignisse(troop, audits=reversed(rows), warnungen=[warnung], benutzer={7: "Aufsicht"})
    assert [e["zeit"] for e in events] == sorted(e["zeit"] for e in events)
    assert all(e["zeit"].tzinfo is None for e in events)
    groups = gruppiere_nach_zyklus(events)
    assert list(groups) == [1, 2]
    text = "\n".join(e["text"] for e in events)
    for expected in ["Lage stabil", "Standort: Keller", "Auftrag: Suche", "Dauer 2 min 0 s", "Enddruck: Alt, 70 bar",
                     "Funkverlust", "Notfall", "Readiness-Override: Dringend", "Warnung erledigt", "Eskalation Stufe 1",
                     "SMS an Leitung", "Status: failed, Fehler: Kein Netz", "PUSH an Aufsicht", "Status: sent"]:
        assert expected in text
    assert text.count("Alt: 250 bar") == 1
    assert text.count("Kontrolle 1/3") == 1
    assert "warnung_zugestellt" not in text
    assert len([e for e in events if e["text"] == "Eintritt"]) == 2
    objective = next(e for e in events if e["text"].startswith("Einsatzziel:"))
    assert objective["nachgetragen"] and objective["akteur"] == "Aufsicht" and objective["zyklus"] == 1
    assert next(e for e in events if "Kontrolle alt" in e["text"])["nachgetragen"]
    assert next(e for e in groups[1] if e["text"].startswith("Enddruck"))["zeit"] == NOW + timedelta(minutes=21)


def test_audits_keep_prior_changes_and_last_message_deduplicated():
    troop = sample()
    troop.last_meldung_at = NOW + timedelta(minutes=62)
    troop.last_meldung_text = "Zweite Meldung"
    events = troop_protokoll_ereignisse(troop, audits=[
        audit("meldung", 61, {"text": "Erste Meldung"}), audit("meldung", 62, {"text": "Zweite Meldung"}),
        audit("details", 63, {"funkkanal": "Neu"}),
    ])
    text = "\n".join(e["text"] for e in events)
    assert "Erste Meldung" in text and text.count("Zweite Meldung") == 1 and "Neu" in text
    assert "Dauer bisher" in text


def setup_report(ctx):
    t = ctx.troop
    t.status, t.entry_at, t.back_at = "zurueck", NOW, NOW + timedelta(minutes=25)
    t.planned_duration_min = 30
    t.ueberwacher_name, t.funkkanal, t.angriffsweg = "Aufsicht Bericht", "Kanal 5", "Nordtreppe"
    t.readiness_override_reason, t.readiness_override_at = "Menschenrettung", NOW
    t.notfall_at = NOW + timedelta(minutes=21)
    ctx.db.add(BreathingAssp(incident_id=ctx.incident_id, aktiv=True, aktiviert_at=NOW,
        leiter_name="Leitung Sammelplatz", standort="Parkplatz", flaschen_voll=12, flaschen_leer=3, notiz="Reserve"))
    ctx.db.add(BreathingKontrolle(troop_id=t.id, stufe="one_third", art="funk", niedrigster_druck=190,
                                erledigt_at=NOW + timedelta(minutes=10), nachgetragen=True))
    ctx.db.commit()


def test_route_readonly_filters_contrast_links_and_scope(breathing_routes):  # noqa: F811
    ctx = breathing_routes
    setup_report(ctx)
    response = ctx.client.get(ctx.url + "/protokoll")
    assert response.status_code == 200
    for text in ["Aufsicht Bericht", "Kanal 5", "Kontrolle 1/3", "nachgetragen", "Notfall", "Warnungen/Eskalation",
                 'id="breathingContrastToggle"', 'aria-pressed="false"', 'data-contrast-storage="ec-breathing-hc"',
                 "ECPBreathingContrast.restore", "x-show=", ctx.url + "/pdf"]:
        assert text in response.text
    assert ctx.client.get(f"/einsatz/{ctx.other_incident_id}/atemschutz/{ctx.troop.id}/protokoll").status_code == 404
    ctx.db.add(OrgSettings(org_id=ctx.troop.incident.primary_org_id, atemschutz_ueberwachung_modul_aktiv=False))
    ctx.db.commit()
    assert ctx.client.get(ctx.url + "/protokoll").status_code == 404


def test_route_foreign_tenant(breathing_routes):  # noqa: F811
    ctx = breathing_routes
    foreign = FireDept(slug=f"protokoll-fremd-{ctx.incident_id}", name="Fremd")
    ctx.db.add(foreign)
    ctx.db.flush()
    ctx.db.get(Incident, ctx.incident_id).primary_org_id = foreign.id
    ctx.db.commit()
    assert ctx.client.get(ctx.url + "/protokoll").status_code == 404


@pytest.mark.parametrize("template", ["pdf/incident_report.html", "pdf/troop_protocol.html", "archive/detail.html"])
def test_report_templates_fields(breathing_routes, template):  # noqa: F811
    ctx = breathing_routes
    setup_report(ctx)
    from app.routers.ui_archive import _load_archive_incident
    incident = _load_archive_incident(ctx.incident_id, ctx.db)
    request = Request({"type": "http", "method": "GET", "path": "/archiv/1", "headers": [], "query_string": b""})
    request.state.csrf_token = "test"
    html = templates.env.get_template(template).render(
        request=request, incident=incident, troop=ctx.troop, now=NOW, user=SimpleNamespace(org=None),
        teilnahmen=[], journal=[], objekte=[], can_edit=False, can_delete=False,
        wp_report_available=False, ai_enabled=False, uas_einsatz=None, verlauf=[],
        fahrten_details={}, extra_vehicles=[], base_url="",
    )
    for text in ["Überwacher", "Aufsicht Bericht", "Kanal 5", "Nordtreppe", "versäumt", "Kontrolle 1/3",
                 "190 bar", "nachgetragen", "Notfall", "Menschenrettung", "Atemschutzsammelplatz",
                 "Leitung Sammelplatz", "Parkplatz", "Flaschen voll: 12", "Flaschen leer: 3", "Reserve"]:
        assert text in html
    if template == "archive/detail.html":
        assert ctx.url + "/protokoll" in html
        assert "/atemschutz/formular-leer.pdf" in html


@pytest.mark.parametrize("archive", [False, True])
def test_loader_and_archive_render_have_constant_queries(breathing_routes, archive):  # noqa: F811
    ctx = breathing_routes
    setup_report(ctx)
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    def add_history(t):
        old = BreathingDeployment(troop=t, lfd_nr=1, status="zurueck", entry_at=NOW - timedelta(hours=2),
                                  back_at=NOW - timedelta(hours=1), planned_duration_min=30)
        ctx.db.add(old)
        ctx.db.flush()
        m = TroopMember(troop=t, deployment_id=old.id, free_text_name="Archiviert", start_press=300)
        ctx.db.add(m)
        ctx.db.flush()
        ctx.db.add(PressureLog(troop=t, troop_member=m, troop_member_id=m.id, pressure_bar=200, ts=NOW - timedelta(minutes=90)))
        ctx.db.add(BreathingKontrolle(troop=t, deployment_id=old.id, stufe="one_third", art="funk",
                                     erledigt_at=NOW - timedelta(minutes=100), niedrigster_druck=200))
        ctx.db.add(BreathingWarnung(incident_id=ctx.incident_id, troop_id=t.id, kind="notfall",
            zyklus_start=old.entry_at, ausgeloest_at=old.entry_at, zustellungen=[
                BreathingZustellung(stufe=1, kanal="sms", status="failed", fehler="Netz", created_at=old.entry_at),
            ]))
        ctx.db.flush()

    add_history(ctx.troop)
    ctx.db.commit()

    def measure():
        ctx.db.expire_all()
        incident = ctx.db.get(Incident, ctx.incident_id)
        statements.clear()
        if archive:
            from app.routers.ui_archive import _load_archive_incident
            incident = _load_archive_incident(ctx.incident_id, ctx.db)
            troops = incident.breathing_troops
        else:
            troops = load_protokoll_context(ctx.db, incident)
        count = len(statements)
        for troop in troops:
            for cycle in [*troop.deployments, troop]:
                templates.env.get_template("pdf/_troop_block.html").render(cycle=cycle, user=SimpleNamespace(org=None))
        assert len(statements) == count
        return count

    event.listen(ctx.db.bind, "before_cursor_execute", record)
    try:
        one = measure()
        for i in range(4):
            new_troop = create_troop(ctx.db, ctx.incident_id, f"Trupp {i}", [
                {"free_text_name": "C", "start_press": 300}, {"free_text_name": "D", "start_press": 300},
            ])
            add_history(new_troop)
        ctx.db.commit()
        assert measure() == one
    finally:
        event.remove(ctx.db.bind, "before_cursor_execute", record)


def test_clearing_after_return_before_redeployment_stays_in_old_cycle():
    troop = sample()
    troop.deployments[0].notfall_at = None
    rows = [audit("notfall", 16, {"active": True}),
            audit("notfall_cleared", 22, {"active": False}),
            audit("redeployed", 30)]
    events = troop_protokoll_ereignisse(troop, audits=rows)
    cleared = next(e for e in events if e["text"].startswith("Notfall aufgehoben"))
    assert cleared["zyklus"] == 1 and "Dauer 6 min 0 s" in cleared["text"]


def test_pressure_corrections_and_near_simultaneous_logs_are_retained():
    troop = sample()
    troop.pressure_logs.append(PressureLog(troop_member_id=11, pressure_bar=240,
        ts=NOW + timedelta(minutes=3, seconds=1), nachgetragen=False))
    rows = [audit("pressure_logged", 3, {"pressure_bar": 250, "nachgetragen": True}, member=True),
            audit("pressure_logged", 4, {"pressure_bar": 240, "erfasst_at":
                  (NOW + timedelta(minutes=3, seconds=1)).isoformat()}, member=True),
            audit("back_pressure_reported", 19, {"pressure_bar": 70}, member=True),
            audit("back_pressure_reported", 21, {"pressure_bar": 70}, member=True)]
    events = troop_protokoll_ereignisse(troop, audits=rows)
    assert len([e for e in events if "Alt: 2" in e["text"]]) == 2
    assert len([e for e in events if e["text"].startswith("Enddruck")]) == 2
    assert not next(e for e in events if "Alt: 240" in e["text"])["nachgetragen"]


@pytest.mark.parametrize("pause,identified,expected", [(10, True, True), (30, True, False), (10, False, False)])
def test_recovery_only_when_person_and_pause_are_known(breathing_routes, pause, identified, expected):  # noqa: F811
    from app.models.master import Member
    ctx = breathing_routes
    t = ctx.troop
    member = Member(org_id=t.incident.primary_org_id, firstname="Anna", lastname="Test")
    ctx.db.add(member)
    ctx.db.flush()
    previous = BreathingDeployment(troop=t, lfd_nr=1, status="zurueck", entry_at=NOW,
        back_at=NOW + timedelta(minutes=20), planned_duration_min=30)
    ctx.db.add(previous)
    ctx.db.flush()
    ctx.db.add(TroopMember(troop=t, deployment_id=previous.id, member_id=member.id if identified else None,
                          free_text_name="Anna Test"))
    t.active_members[0].member_id = member.id if identified else None
    t.entry_at, t.status = NOW + timedelta(minutes=20 + pause), "im_einsatz"
    ctx.db.add(OrgSettings(org_id=t.incident.primary_org_id, asuew_erholzeit_min=30))
    ctx.db.commit()
    load_protokoll_context(ctx.db, t.incident)
    assert t.protokoll["erholzeit_verletzt"] is expected
    assert previous.protokoll["fristen"][0]["versaeumt"]
    assert not previous.protokoll["fristen"][1]["versaeumt"]  # Rückkehr genau an der Frist.
    html = templates.env.get_template("breathing/_protokoll_details.html").render(cycle=t)
    assert ("Erholzeit verletzt" in html) is expected


def test_route_available_without_supervision_role(breathing_routes):  # noqa: F811
    from app.models.user import User, UserRole
    ctx = breathing_routes
    user = ctx.db.query(User).filter(User.org_id == ctx.troop.incident.primary_org_id).one()
    for assignment in ctx.db.query(UserRole).filter(UserRole.user_id == user.id).all():
        ctx.db.delete(assignment)
    ctx.db.commit()
    assert ctx.client.get(ctx.url + "/protokoll").status_code == 200
