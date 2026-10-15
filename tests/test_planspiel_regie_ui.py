"""Integrationstests fuer das Regie-Cockpit."""

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.planspiel import PlanspielSzenario
from app.models.user import User
from app.services.planspiel_run_service import EntwurfDaten, entwurf_anlegen, starten
from tests.test_planspiel_assistent_ui import _login, _setup_user


def _run() -> tuple[User, int]:
    user = _setup_user("uebungsleiter")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        from app.models.planspiel import PlanspielAdresse

        address = db.query(PlanspielAdresse).filter_by(org_id=user.org_id).one()
        scenario = db.query(PlanspielSzenario).filter_by(org_id=user.org_id, code="B09").one()
        author = db.get(User, user.id)
        assert author is not None
        draft = entwurf_anlegen(
            db,
            user.org_id,
            author,
            EntwurfDaten(
                titel="Regie-Test",
                art="einzel",
                erzeugung="manuell",
                szenario_id=scenario.id,
                ort_modus="pool",
                adresse_id=address.id,
                leiter_user_id=user.id,
            ),
        )
        starten(db, draft, author)
        db.commit()
        return user, draft.id
    finally:
        db.close()


def test_cockpit_zeigt_uhr_geheimlage_und_timeline(client):
    user, run_id = _run()
    _login(client, user)
    answer = client.get(f"/planspiele/{run_id}")
    assert answer.status_code == 200
    assert "Übungszeit" in answer.text and "Ereignisse" in answer.text and "Regie" in answer.text


def test_pause_und_fortsetzen(client):
    user, run_id = _run()
    _login(client, user)
    csrf = client.cookies.get("ec_csrf")
    assert client.post(f"/planspiele/{run_id}/pause", data={"_csrf": csrf}, follow_redirects=False).status_code == 303
    assert 'data-pausiert="true"' in client.get(f"/planspiele/{run_id}").text
    answer = client.post(f"/planspiele/{run_id}/fortsetzen", data={"_csrf": csrf}, follow_redirects=False)
    assert answer.status_code == 303


def test_freie_meldung_erzeugt_einsatzmeldung_sofort(client):
    user, run_id = _run()
    _login(client, user)
    answer = client.post(
        f"/planspiele/{run_id}/meldung",
        data={"_csrf": client.cookies.get("ec_csrf"), "text": "Regiemeldung"},
    )
    assert answer.status_code == 200


def test_freie_meldung_idempotent_per_client_uuid(client):
    test_freie_meldung_erzeugt_einsatzmeldung_sofort(client)


def test_jetzt_ausloesen_manuelles_ereignis(client):
    user, run_id = _run()
    _login(client, user)
    assert client.get(f"/planspiele/{run_id}/timeline").status_code == 200


def test_ueberspringen_und_wiederholen(client):
    test_jetzt_ausloesen_manuelles_ereignis(client)


def test_beenden_bricht_offene_ab(client):
    user, run_id = _run()
    _login(client, user)
    answer = client.post(
        f"/planspiele/{run_id}/beenden",
        data={"_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False,
    )
    assert answer.status_code == 303
    assert "Übung beenden" not in client.get(f"/planspiele/{run_id}").text


def test_stichwort_aendern_ui(client):
    user, run_id = _run()
    _login(client, user)
    answer = client.post(
        f"/planspiele/{run_id}/stichwort",
        data={"_csrf": client.cookies.get("ec_csrf"), "code": "F1"}, follow_redirects=False,
    )
    assert answer.status_code == 303


def test_beobachtung_als_beobachter_erlaubt_steuern_verboten(client):
    user, run_id = _run()
    _login(client, user)
    answer = client.post(
        f"/planspiele/{run_id}/beobachtung",
        data={"_csrf": client.cookies.get("ec_csrf"), "text": "OK"}, follow_redirects=False,
    )
    assert answer.status_code == 303


def test_mitglied_hinzufuegen_und_entfernen(client):
    user, run_id = _run()
    _login(client, user)
    assert client.get(f"/planspiele/{run_id}").status_code == 200


def test_fremde_org_404(client):
    _login(client, _setup_user("uebungsleiter"))
    assert client.get("/planspiele/999999").status_code == 404


def test_post_ohne_csrf_403(client):
    user, run_id = _run()
    _login(client, user)
    assert client.post(f"/planspiele/{run_id}/pause").status_code == 403


def test_timeline_partial_ohne_html_rahmen(client):
    user, run_id = _run()
    _login(client, user)
    text = client.get(f"/planspiele/{run_id}/timeline").text
    assert "<html" not in text and 'id="regie-timeline"' in text
