"""Phase-5-Routentests fuer Autosave und punktweises Optimistic Locking."""

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.probenplanung import ProbeCheckliste, ProbeChecklistItem
from tests.test_probenplanung_checkliste import (
    ORG_ID,
    _flags,
    _login,
    _probe_anlegen,
    _probeart,
    _user,
    _vorlage_mit_punkt,
)


def _setup(client, username: str) -> tuple[str, int, int]:
    _user(username, "org_admin")
    _flags()
    csrf = _login(client, username)
    template_id, _, _ = _vorlage_mit_punkt(client, csrf, f"Phase 5 {username}")
    probeart_id = _probeart(template_id, f"P5 {username}")
    termin_id = _probe_anlegen(client, csrf, probeart_id, f"Probe {username}")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        checkliste = db.query(ProbeCheckliste).filter_by(termin_id=termin_id).one()
        item_id = db.query(ProbeChecklistItem).filter_by(checkliste_id=checkliste.id).one().id
    finally:
        db.close()
    return csrf, termin_id, item_id


def _patch(client, csrf: str, termin_id: int, item_id: int, *, version: int, wert: str = "erledigt"):
    return client.patch(
        f"/probenplanung/{termin_id}/checkliste/punkt/{item_id}",
        data={"_csrf": csrf, "feld": "zustand", "wert": wert, "version": str(version)},
    )


def test_patch_version_konflikt_liefert_aktuelles_partial_ohne_mutation(client):
    csrf, termin_id, item_id = _setup(client, "phase5_conflict")
    assert _patch(client, csrf, termin_id, item_id, version=0).status_code == 200
    response = _patch(client, csrf, termin_id, item_id, version=0, wert="offen")
    assert response.status_code == 409
    assert response.headers["HX-Reswap"] == "outerHTML"
    assert "Wurde zwischenzeitlich geändert" in response.text
    assert 'data-version="1"' in response.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        item = db.get(ProbeChecklistItem, item_id)
        assert item.zustand == "erledigt"
        assert item.version == 1
    finally:
        db.close()


def test_korrekte_version_wird_erhoeht_und_fortschritt_aktualisiert(client):
    csrf, termin_id, item_id = _setup(client, "phase5_success")
    response = _patch(client, csrf, termin_id, item_id, version=0)
    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "probe-fortschritt"
    assert 'data-version="1"' in response.text
    progress = client.get(f"/probenplanung/{termin_id}/checkliste/fortschritt")
    assert "1/1 erledigt" in progress.text
    assert 'aria-valuenow="100"' in progress.text


def test_zwei_verschiedene_punkte_blockieren_sich_nicht(client):
    csrf, termin_id, first_id = _setup(client, "phase5_parallel")
    response = client.post(
        f"/probenplanung/{termin_id}/checkliste/punkt",
        data={"_csrf": csrf, "titel": "Parallel", "typ": "checkbox"},
    )
    assert response.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        second_id = db.query(ProbeChecklistItem).filter_by(titel="Parallel").one().id
    finally:
        db.close()
    assert _patch(client, csrf, termin_id, first_id, version=0).status_code == 200
    assert _patch(client, csrf, termin_id, second_id, version=0).status_code == 200


def test_individuellen_punkt_anlegen_und_loeschen_vorlagenpunkt_geschuetzt(client):
    csrf, termin_id, template_item_id = _setup(client, "phase5_crud")
    created = client.post(
        f"/probenplanung/{termin_id}/checkliste/punkt",
        data={"_csrf": csrf, "titel": "Eigener Punkt", "typ": "text", "pflicht": "1"},
    )
    assert created.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        own_id = db.query(ProbeChecklistItem).filter_by(titel="Eigener Punkt").one().id
    finally:
        db.close()
    headers = {"X-CSRF-Token": csrf}
    assert client.delete(f"/probenplanung/{termin_id}/checkliste/punkt/{template_item_id}", headers=headers).status_code == 409
    deleted = client.delete(f"/probenplanung/{termin_id}/checkliste/punkt/{own_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.headers["HX-Trigger"] == "probe-fortschritt"


def test_nicht_relevant_braucht_begruendung_und_aktualisiert_fortschritt(client):
    csrf, termin_id, item_id = _setup(client, "phase5_irrelevant")
    empty = client.post(
        f"/probenplanung/{termin_id}/checkliste/punkt/{item_id}/nicht-relevant",
        data={"_csrf": csrf, "begruendung": " ", "version": "0"},
    )
    assert empty.status_code == 422
    response = client.post(
        f"/probenplanung/{termin_id}/checkliste/punkt/{item_id}/nicht-relevant",
        data={"_csrf": csrf, "begruendung": "Für diesen Standort nicht nötig", "version": "0"},
    )
    assert response.status_code == 200
    assert "Für diesen Standort nicht nötig" in response.text
    assert "0/0 erledigt" in client.get(f"/probenplanung/{termin_id}/checkliste/fortschritt").text


def test_leser_darf_sehen_nicht_aendern_und_modulguard_greift(client):
    _, termin_id, item_id = _setup(client, "phase5_guard_editor")
    _user("phase5_reader", "readonly")
    reader_csrf = _login(client, "phase5_reader")
    assert client.get(f"/probenplanung/{termin_id}/checkliste").status_code == 200
    assert _patch(client, reader_csrf, termin_id, item_id, version=0).status_code == 403
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSettings).filter(OrgSettings.org_id == ORG_ID).one().probenplanung_modul_aktiv = False
        db.commit()
    finally:
        db.close()
    assert client.get(f"/probenplanung/{termin_id}/checkliste").status_code == 404
