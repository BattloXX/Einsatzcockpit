"""Phase 11: Positivliste, Freigaben und widerrufbare öffentliche Links."""
import hashlib
import re
import secrets
from dataclasses import FrozenInstanceError, fields
from datetime import datetime

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.probenplanung import ProbeCheckliste, ProbeChecklistItem, ProbeMedia, ProbePublicToken
from app.models.teilnahme import Teilnahme, Termin
from app.services.probenplanung_public import OeffentlicheProbe, oeffentliche_proben
from tests.test_probenplanung_checkliste import _flags, _login, _probeart, _user
from tests.test_probenplanung_plan import _termin


def public_setup(**values):
    _flags()
    art = _probeart(0, "Public " + secrets.token_hex(6))
    termin_id = _termin(art, "Freigegebene Probe", "2026-07-10T20:00", **values)
    plain = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=1).one().probenplanung_public_aktiv = True
        token = ProbePublicToken(org_id=1, art="plan", termin_id=termin_id,
                                 token_hash=hashlib.sha256(plain.encode()).hexdigest())
        db.add(token)
        db.commit()
        return plain, termin_id, token.id, art


@pytest.mark.parametrize("suffix", ["", ".ics"])
def test_oeffentliche_antwort_enthaelt_keine_internen_daten(client, suffix):
    plain, tid, _, _ = public_setup(
        interne_bemerkung="GEHEIM-BEMERKUNG", alarmtext="GEHEIM-ALARM",
        besondere_gefahren="GEHEIM-GEFAHR", besondere_hinweise="GEHEIM-HINWEIS",
        beschreibung="GEHEIM-BESCHREIBUNG", ort="GEHEIM-ORT", info="GEHEIM-INFO",
    )
    with SessionLocal() as db:
        set_tenant_context(db, None)
        cl = ProbeCheckliste(org_id=1, termin_id=tid, template_name="GEHEIM-VORLAGE", template_version=1)
        db.add(cl)
        db.flush()
        db.add(ProbeChecklistItem(org_id=1, checkliste_id=cl.id, titel="GEHEIM-CHECKLISTE",
                                  typ="text", wert_text="GEHEIM-FORTSCHRITT", zustand="erledigt"))
        db.add(Teilnahme(org_id=1, bezug_typ="uebung", bezug_id=tid, freitext_name="GEHEIM-TEILNEHMER"))
        for art, name in [("dokument", "GEHEIM-BRANDSCHUTZPLAN.pdf"), ("skizze", "GEHEIM-SKIZZE.png")]:
            db.add(ProbeMedia(org_id=1, termin_id=tid, art=art, name=name, kind="image",
                              mime_type="image/png", path=name, size_bytes=1))
        db.commit()
    client.cookies.clear()
    response = client.get(f"/p/probenplan/{plain}{suffix}")
    assert response.status_code == 200
    assert "Freigegebene Probe" in response.text
    for verboten in ["GEHEIM", "interne_bemerkung", "alarmtext", "besondere_gefahren", "checkliste",
                     "fortschritt", "teilnehmer", "media", "skizze"]:
        assert verboten.lower() not in response.text.lower()
    assert response.headers["cache-control"] == "private, no-store"
    if not suffix:
        assert '<meta name="robots" content="noindex,nofollow">' in response.text


@pytest.mark.parametrize("ort_frei,info_frei", [(False, False), (True, False), (False, True), (True, True)])
def test_feldfreigaben_fuer_beide_wege(client, ort_frei, info_frei):
    plain, _, token_id, _ = public_setup(ort="OrtFreigabe", info="InfoFreigabe",
                                         public_ort_sichtbar=ort_frei, public_info_sichtbar=info_frei)
    for suffix in ("", ".ics"):
        response = client.get(f"/p/probenplan/{plain}{suffix}")
        assert response.status_code == 200
        assert ("OrtFreigabe" in response.text) == ort_frei
        assert ("InfoFreigabe" in response.text) == info_frei
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(ProbePublicToken, token_id).zuletzt_genutzt_am is not None


@pytest.mark.parametrize("values", [
    {"status": "entwurf"}, {"archiviert_am": datetime(2026, 1, 1)}, {"public_sichtbar": False},
])
def test_nicht_freigegebene_proben_ausgeschlossen(client, values):
    plain, _, _, _ = public_setup(**values)
    for suffix in ("", ".ics"):
        r = client.get(f"/p/probenplan/{plain}{suffix}")
        assert r.status_code == 200
        assert "Freigegebene Probe" not in r.text


def test_token_widerrufen_unbekannt_und_modul_aus(client):
    plain, _, token_id, _ = public_setup()
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(ProbePublicToken, token_id).widerrufen_am = datetime.now()
        db.commit()
    for suffix in ("", ".ics"):
        assert client.get(f"/p/probenplan/{plain}{suffix}").status_code == 404
        assert client.get(f"/p/probenplan/unbekannt{suffix}").status_code == 404
    for flag in ("probenplanung_public_aktiv", "probenplanung_modul_aktiv"):
        plain, _, _, _ = public_setup()
        with SessionLocal() as db:
            set_tenant_context(db, None)
            setattr(db.query(OrgSettings).filter_by(org_id=1).one(), flag, False)
            db.commit()
        for suffix in ("", ".ics"):
            assert client.get(f"/p/probenplan/{plain}{suffix}").status_code == 404


def test_exakte_unveraenderliche_positivliste_und_template_context(client):
    plain, _, _, _ = public_setup()
    assert {f.name for f in fields(OeffentlicheProbe)} == {
        "datum", "beginn", "ende", "ganztaegig", "probeart_name", "probeart_farbe",
        "thema", "objekt", "ort", "info", "status_abgesagt",
    }
    with SessionLocal() as db:
        set_tenant_context(db, 999999)  # Selektor darf nicht vom angemeldeten Tenant abhängen.
        p = oeffentliche_proben(db, plain)[0].probe
        with pytest.raises(FrozenInstanceError):
            p.info = "Manipulation"
    r = client.get(f"/p/probenplan/{plain}")
    assert all(type(p) is OeffentlicheProbe for p in r.context["proben"])
    assert set(r.context) == {"request", "proben"}


def test_token_verwaltung_einmalig_rotation_widerruf_und_csrf(client):
    _flags()
    _user("public_admin", "org_admin")
    csrf = _login(client, "public_admin")
    url = "/admin/probenplanung/oeffentlich"
    assert client.post(url, data={"bezeichnung": "Test"}).status_code == 403
    r = client.post(url, data={"_csrf": csrf, "bezeichnung": "Einmaliger Link"})
    assert r.status_code == 200
    plain = re.search(r"/p/probenplan/([A-Za-z0-9_-]+)", r.text).group(1)
    assert "webcal://" in r.text and plain + ".ics" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert plain not in client.get(url).text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        token = db.query(ProbePublicToken).filter_by(token_hash=hashlib.sha256(plain.encode()).hexdigest()).one()
        token_id = token.id
        assert plain not in repr(token.__dict__)
    rotated = client.post(f"{url}/{token_id}/regenerieren", data={"_csrf": csrf})
    assert rotated.status_code == 200
    new_plain = re.search(r"/p/probenplan/([A-Za-z0-9_-]+)", rotated.text).group(1)
    assert new_plain != plain
    assert client.get(f"/p/probenplan/{plain}").status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, None)
        new_id = db.query(ProbePublicToken).filter_by(
            token_hash=hashlib.sha256(new_plain.encode()).hexdigest()).one().id
    assert client.post(f"{url}/{new_id}/widerrufen", data={"_csrf": csrf},
                       follow_redirects=False).status_code == 303
    assert client.get(f"/p/probenplan/{new_plain}.ics").status_code == 404


def test_token_verwaltung_nur_org_admin(client):
    _flags()
    _user("public_leser", "readonly")
    csrf = _login(client, "public_leser")
    url = "/admin/probenplanung/oeffentlich"
    assert client.get(url).status_code == 403
    for path in (url, url + "/1/widerrufen", url + "/1/regenerieren", url + "/freigabe"):
        assert client.post(path, data={"_csrf": csrf}).status_code == 403


def test_robots_sperrt_public_pfade(client):
    assert "Disallow: /p/" in client.get("/robots.txt").text


def test_token_filter_und_systemschalter_fail_closed(client):
    from app.models.master import SystemSettings
    plain, _, token_id, art = public_setup()
    for values in ({"jahr": 2025}, {"jahr": None, "filter_probeart_ids": "[]"},
                   {"filter_probeart_ids": "[99999999]"}):
        with SessionLocal() as db:
            set_tenant_context(db, None)
            token = db.get(ProbePublicToken, token_id)
            for key, value in values.items():
                setattr(token, key, value)
            db.commit()
        for suffix in ("", ".ics"):
            r = client.get(f"/p/probenplan/{plain}{suffix}")
            assert r.status_code == 200 and "Freigegebene Probe" not in r.text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(ProbePublicToken, token_id).filter_probeart_ids = f"[{art}]"
        db.commit()
    assert "Freigegebene Probe" in client.get(f"/p/probenplan/{plain}").text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(ProbePublicToken, token_id).filter_probeart_ids = "ungueltiges-json"
        db.commit()
    for suffix in ("", ".ics"):
        assert client.get(f"/p/probenplan/{plain}{suffix}").status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(ProbePublicToken, token_id).filter_probeart_ids = None
        db.query(SystemSettings).filter_by(key="probenplanung_module_enabled").one().value = "false"
        db.commit()
    for suffix in ("", ".ics"):
        assert client.get(f"/p/probenplan/{plain}{suffix}").status_code == 404


def test_org_admin_kann_fremden_token_nicht_verwalten(client):
    from tests.test_public_tenant_isolation import _setup_zwei_orgs
    other_org = _setup_zwei_orgs()
    _flags()
    _user("public_admin_isolation", "org_admin")
    csrf = _login(client, "public_admin_isolation")
    plain = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        token = ProbePublicToken(org_id=other_org, art="plan", bezeichnung="FremderGeheimerToken",
                                 token_hash=hashlib.sha256(plain.encode()).hexdigest())
        db.add(token)
        db.commit()
        token_id = token.id
    url = "/admin/probenplanung/oeffentlich"
    assert "FremderGeheimerToken" not in client.get(url).text
    for action in ("widerrufen", "regenerieren"):
        assert client.post(f"{url}/{token_id}/{action}", data={"_csrf": csrf}).status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(ProbePublicToken, token_id).is_active


def test_public_freigabe_wird_nur_explizit_aktiviert(client):
    plain, _, _, _ = public_setup()
    _user("public_freigabe_admin", "org_admin")
    csrf = _login(client, "public_freigabe_admin")
    url = "/admin/probenplanung/oeffentlich/freigabe"
    assert client.post(url, data={"_csrf": csrf}, follow_redirects=False).status_code == 303
    assert client.get(f"/p/probenplan/{plain}").status_code == 404
    assert client.post(url, data={"_csrf": csrf, "public_aktiv": "1"}, follow_redirects=False).status_code == 303
    assert client.get(f"/p/probenplan/{plain}").status_code == 200
