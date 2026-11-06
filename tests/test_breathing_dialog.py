"""Dialog-Regressionspruefungen mit echten Router-Kontexten und Formularen."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models.master import Member, MemberQualification, Qualification
from app.models.user import User
from app.services.breathing_service import create_troop
from tests.test_breathing_routes import breathing_routes  # noqa: F401


def test_new_dialog_crew_footer_pressure_and_suggestion(breathing_routes):
    ctx = breathing_routes
    for name in ("Trupp B", "Trupp C"):
        create_troop(ctx.db, ctx.incident_id, name, [
            {"free_text_name": "A", "start_press": 300},
            {"free_text_name": "B", "start_press": 290},
        ])
    ctx.db.commit()
    html = ctx.client.get(f"/einsatz/{ctx.incident_id}/atemschutz").text
    dialog = html.split('id="newTroopModal"', 1)[1].split('</dialog>', 1)[0]
    assert 'placeholder="Trupp 4"' in dialog
    assert 'name="name"' in dialog and ' required' not in dialog
    assert 'class="bt-dialog-footer"' in dialog
    assert '>300</button>' in dialog
    assert 'x-for="(m, i) in members"' in dialog
    assert 'class="person-select"' in dialog
    assert 'Freie Eingabe (nicht im Register)' in dialog
    assert '@person-select-freitext=' in dialog
    assert ':name="`member_${i}_id`"' in dialog
    assert ':name="`member_${i}_press`"' in dialog
    assert 'name="_csrf"' in dialog
    user = ctx.db.query(User).filter_by(org_id=ctx.troop.incident.primary_org_id).one()
    assert f'value="{user.full_name or user.username}"' in dialog
    assert 'role="alert"' in dialog
    assert 'alert(' not in html
    assert 'alert(' not in Path('app/templates/breathing/board.html').read_text()


def test_only_active_agt_and_occupied_hint(breathing_routes):
    ctx = breathing_routes
    qualification = ctx.db.query(Qualification).filter_by(code="AGT").one()
    org_id = ctx.troop.incident.primary_org_id
    active = Member(firstname="DialogAktiv", lastname="AGT", org_id=org_id)
    inactive = Member(firstname="DialogInaktiv", lastname="AGT", org_id=org_id, active=False)
    non_agt = Member(firstname="DialogOhne", lastname="AGT", org_id=org_id)
    ctx.db.add_all([active, inactive, non_agt])
    ctx.db.flush()
    ctx.db.add_all([MemberQualification(member_id=m.id, qualification_id=qualification.id) for m in (active, inactive)])
    ctx.troop.active_members[0].member_id = active.id
    ctx.db.commit()
    html = ctx.client.get(f"/einsatz/{ctx.incident_id}/atemschutz").text
    dialog = html.split('id="newTroopModal"', 1)[1].split('</dialog>', 1)[0]
    assert 'DialogAktiv AGT' in dialog
    assert 'DialogInaktiv' not in dialog
    assert 'DialogOhne' not in dialog
    assert '"occupied": "Trupp A"' in dialog
    assert 'bereits in Trupp ' in dialog
    assert '>AGT</small>' in dialog


def test_redeploy_prefills_people_roles_and_recovery(breathing_routes):
    ctx = breathing_routes
    ctx.troop.status = "erholt"
    ctx.troop.back_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=18)
    ctx.db.commit()
    html = ctx.client.get(f"/einsatz/{ctx.incident_id}/atemschutz").text
    dialog = html.split(f'id="redeployTroopModal-{ctx.troop.id}"', 1)[1].split('</dialog>', 1)[0]
    assert 'name:"Person A",role:"truppfuehrer"' in dialog
    assert 'name:"Person B",role:"truppmann"' in dialog
    assert 'press:300' not in dialog
    assert 'Erholzeit läuft noch: noch 12 Min.' in dialog
    assert 'class="person-select"' in dialog
    assert 'class="bt-dialog-footer"' in dialog
    assert '>300</button>' in dialog
    assert 'name="_csrf"' in dialog


def test_redeploy_saves_shared_fields(breathing_routes):
    ctx = breathing_routes
    ctx.troop.status = "erholt"
    ctx.db.commit()
    response = ctx.post(f"{ctx.url}/erneut-einsetzen", {
        "name": "Trupp Neu", "unit_name": "Florian 1", "ueberwacher_name": "Neue Aufsicht",
        "angriffsweg": "Stiege", "funkkanal": "Kanal 2", "is_sicherheitstrupp": "true",
        "member_0_name": "Person A", "member_0_role": "truppfuehrer", "member_0_press": "300",
        "member_1_name": "Person B", "member_1_role": "truppmann", "member_1_press": "290",
    })
    assert response.status_code == 303
    ctx.db.refresh(ctx.troop)
    assert ctx.troop.name == "Trupp Neu"
    assert ctx.troop.unit_name == "Florian 1"
    assert ctx.troop.ueberwacher_name == "Neue Aufsicht"
    assert ctx.troop.angriffsweg == "Stiege"
    assert ctx.troop.funkkanal == "Kanal 2"
    assert ctx.troop.is_sicherheitstrupp
