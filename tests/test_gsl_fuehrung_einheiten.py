"""Regressionstests für Führungsänderungen an Einheitenaufträgen."""
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from app.core.tenant import set_tenant_context
from app.models.major_incident import IncidentSite, LageEinheit, MajorIncident, SiteLogEntry
from app.services import resource_service as rs
from tests.conftest import TestingSession


@contextmanager
def _session():
    db = TestingSession()
    set_tenant_context(db, 1)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _daten(db):
    lage = MajorIncident(name="Führungs-Test", org_id=1)
    db.add(lage)
    db.flush()
    site = IncidentSite(major_incident_id=lage.id, org_id=1, bezeichnung="Stelle A")
    db.add(site)
    einheit = LageEinheit(
        lage_id=lage.id, label="TLF", resource_type="fahrzeug", status=rs.STATUS_BEREITGESTELLT,
    )
    db.add(einheit)
    db.flush()
    return lage, site, einheit


def test_disponieren_und_auftrag_aendern_speichern_fuehrungsdaten():
    with _session() as db:
        lage, site, einheit = _daten(db)
        dispatch = rs.dispatch_to_site(
            db, einheit.id, lage.id, site.id, auftrag="  Wasserentnahme  ", reihenfolge=2,
            author_name="EL", user_id=1,
        )
        assert (dispatch.auftrag, dispatch.reihenfolge, dispatch.version) == ("Wasserentnahme", 2, 1)
        assert rs.aendere_auftrag(
            db, dispatch, auftrag="Neuer Auftrag", reihenfolge=1, author_name="EL", user_id=1,
        )
        assert dispatch.version == 2
        assert dispatch.geaendert_at is not None
        db.flush()
        assert db.query(SiteLogEntry).filter_by(incident_site_id=site.id).one().text == "Auftrag geändert: TLF"
        db.rollback()


def test_beendete_dispatches_werden_angezeigt_und_wiedereroeffnet():
    with _session() as db:
        lage, site, einheit = _daten(db)
        dispatch = rs.dispatch_to_site(db, einheit.id, lage.id, site.id)
        dispatch.beendet_at = datetime.now(UTC)
        dispatch.beendet_grund = "Erledigt"
        assert rs.get_active_dispatches_for_site(db, site.id) == []
        assert rs.get_dispatches_for_site_anzeige(db, site.id) == [dispatch]
        rs.oeffne_auftrag_wieder(db, dispatch, author_name="EL", user_id=1)
        assert dispatch.einheit_status == "zugewiesen"
        assert dispatch.beendet_at is None
        assert dispatch.version == 2
        db.rollback()


# ── HTTP-Routen der Führung (Funk-Stellvertretung, Auftrag, Rollen) ──────────

from app.core.security import hash_password, sign_session  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.major_incident import EinheitSiteDispatch, SitePhase  # noqa: E402
from app.models.user import Role, User, UserRole  # noqa: E402
from tests.test_einheit_api import _als, _daten as _api_daten, _h  # noqa: E402


def _mit_rolle(org_von_user: int, code: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_id = db.get(User, org_von_user).org_id
        user = User(username=f"fe-{code}-{datetime.now(UTC).timestamp()}", password_hash=hash_password("x"),
                    display_name=code, org_id=org_id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code=code).one().id))
        db.commit()
        return user.id
    finally:
        db.close()


def _post(client, url, data=None):
    client.get("/lage")  # setzt das CSRF-Cookie dieser Session
    headers = _h(client)
    return client.post(url, data={**(data or {}), "_csrf": client.cookies.get("ec_csrf")},
                       headers={**headers, "HX-Request": "true"})


def _broadcasts(monkeypatch):
    events = []

    async def fake(lage_id, event):
        events.append(event)

    import app.routers.ui_major_incident as router_modul
    monkeypatch.setattr(router_modul, "broadcast_lage", fake)
    return events


def _dispatch(dispatch_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        d = db.get(EinheitSiteDispatch, dispatch_id)
        return d, db
    except Exception:
        db.close()
        raise


def test_funk_status_vor_ort_hebt_phase_und_broadcastet(client, monkeypatch):
    x = _api_daten()
    events = _broadcasts(monkeypatch)
    _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    r = _post(client, f"/lage/{x['lage']}/stellen/{x['a']}/einheit/{x['d1']}/status", {"status": "vor_ort"})
    assert r.status_code == 200, r.text
    assert "Vor Ort" in r.text
    d, db = _dispatch(x["d1"])
    try:
        assert d.einheit_status == "vor_ort"
        assert d.site.phase == SitePhase.in_arbeit
        texte = [e.text for e in db.query(SiteLogEntry).filter_by(incident_site_id=x["a"], kind="einheit")]
        assert any(t.endswith("(per Funk)") for t in texte)
    finally:
        db.close()
    typen = {e["type"] for e in events}
    assert {"site:card_changed", "einheit:changed", "site_phase_changed"} <= typen


def test_funk_status_ungueltiger_uebergang_409(client, monkeypatch):
    x = _api_daten()
    _broadcasts(monkeypatch)
    _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    r = _post(client, f"/lage/{x['lage']}/stellen/{x['a']}/einheit/{x['d1']}/status", {"status": "abgeschlossen"})
    assert r.status_code == 409


def test_auftrag_aendern_abschluss_und_wiedereroeffnen(client, monkeypatch):
    x = _api_daten()
    events = _broadcasts(monkeypatch)
    _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    basis = f"/lage/{x['lage']}/stellen/{x['a']}/einheit/{x['d1']}"
    r = _post(client, f"{basis}/auftrag", {"auftrag": "Keller auspumpen", "reihenfolge": "1"})
    assert r.status_code == 200 and "Keller auspumpen" in r.text
    assert any(e["type"] == "einheit:changed" for e in events)
    d, db = _dispatch(x["d1"])
    assert (d.auftrag, d.reihenfolge, d.version) == ("Keller auspumpen", 1, 2)
    db.close()
    for status in ("vor_ort", "abgeschlossen"):
        assert _post(client, f"{basis}/status", {"status": status}).status_code == 200
    r = client.get(f"/lage/{x['lage']}/stellen/{x['a']}")
    assert "Auftrag abgeschlossen" in r.text and "Wiedereröffnen" in r.text
    r = _post(client, f"{basis}/wiedereroeffnen")
    assert r.status_code == 200
    d, db = _dispatch(x["d1"])
    assert (d.einheit_status, d.beendet_at, d.version) == ("zugewiesen", None, 3)
    db.close()


def test_alle_einheiten_fertig_hinweis(client, monkeypatch):
    x = _api_daten()
    _broadcasts(monkeypatch)
    _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    # Stelle A hat zwei Dispositionen (d1: TLF, dother: RLF)
    db = SessionLocal()
    set_tenant_context(db, None)
    dother = db.query(EinheitSiteDispatch).filter_by(site_id=x["a"]).filter(EinheitSiteDispatch.id != x["d1"]).one().id
    db.close()
    for dispatch_id in (x["d1"], dother):
        basis = f"/lage/{x['lage']}/stellen/{x['a']}/einheit/{dispatch_id}"
        assert _post(client, f"{basis}/status", {"status": "vor_ort"}).status_code == 200
        assert _post(client, f"{basis}/status", {"status": "abgeschlossen"}).status_code == 200
    r = client.get(f"/lage/{x['lage']}/stellen/{x['a']}")
    assert "Alle Einheiten fertig" in r.text


def test_tablet_kennzeichnung_und_simulationslink(client):
    x = _api_daten()
    # Beide Einheiten haben ein Tablet mit gsl_profil "einheit" (siehe _api_daten).
    _als(client, sign_session(x["admin"]))
    r = client.get(f"/lage/{x['lage']}/stellen/{x['a']}")
    assert r.status_code == 200
    assert "📱" in r.text and "Als Einheit ansehen" in r.text
    assert f"/einheit?sim={x['e1']}" in r.text
    _als(client, sign_session(x["recorder"]))
    r = client.get(f"/lage/{x['lage']}/stellen/{x['a']}")
    assert r.status_code == 200
    assert "Als Einheit ansehen" not in r.text


def test_funk_kennzeichnung_ohne_tablet(client):
    x = _api_daten()
    db = SessionLocal()
    set_tenant_context(db, None)
    from app.models.user import DeviceToken
    for token in db.query(DeviceToken).filter(DeviceToken.id.in_([x["t1"], x["t2"]])).all():
        token.gsl_profil = "fuehrung"
    db.commit()
    db.close()
    _als(client, sign_session(x["recorder"]))
    r = client.get(f"/lage/{x['lage']}/stellen/{x['a']}")
    assert "📻" in r.text and "📱" not in r.text.split("Disponierte")[-1].split("Disponieren")[0]


def test_rollen_und_fremde_stelle(client, monkeypatch):
    x = _api_daten()
    _broadcasts(monkeypatch)
    _als(client, sign_session(_mit_rolle(x["admin"], "readonly")))
    basis = f"/lage/{x['lage']}/stellen/{x['a']}/einheit/{x['d1']}"
    assert _post(client, f"{basis}/status", {"status": "vor_ort"}).status_code == 403
    assert _post(client, f"{basis}/auftrag", {"auftrag": "x"}).status_code == 403
    _als(client, sign_session(_mit_rolle(x["admin"], "incident_leader")))
    # d2 gehört zu Stelle B, nicht zu A → 404
    assert _post(client, f"/lage/{x['lage']}/stellen/{x['a']}/einheit/{x['d2']}/status",
                 {"status": "vor_ort"}).status_code == 404


def test_einheit_geraet_darf_fuehrungsrouten_nicht_nutzen(client, monkeypatch):
    x = _api_daten()
    _broadcasts(monkeypatch)
    _als(client, sign_session(x["u1"], device=True, device_token_id=x["t1"]))
    client.get("/einheit/api/zustand")
    r = client.post(f"/lage/{x['lage']}/stellen/{x['a']}/einheit/{x['d1']}/status",
                    data={"status": "vor_ort", "_csrf": client.cookies.get("ec_csrf")},
                    headers={**_h(client), "HX-Request": "true"})
    assert r.status_code == 403
