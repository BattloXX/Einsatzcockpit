"""S7: Org-Schalter, Betriebsanzeige und asynchrone Administration."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.master import FireDept, OrgSettings
from app.models.objekt import Objekt
from app.models.suche import SearchChunk, SearchDocument, SearchOutbox
from app.models.user import AuditLog
from app.services.suche.admin import QUELLEN_LABELS
from app.services.suche.outbox import verarbeite_faellige
from tests.conftest import TestingSession
from tests.test_admin_rollenrechte import _login, _user


@pytest.fixture
def umgebung(client, monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "SEARCH_INDEX_WORKER_ENABLED", True)
    with TestingSession() as db:
        set_tenant_context(db, None)
        org, fremd = [FireDept(slug=uuid4().hex, name=name) for name in ("Suchadmin", "Fremde Org")]
        db.add_all([org, fremd])
        db.flush()
        db.add(OrgSettings(org_id=org.id, suche_aktiv=False))
        users = {}
        for rolle in ("org_admin", "readonly"):
            users[rolle] = uuid4().hex
            _user(db, users[rolle], org.id, rolle)
        db.commit()
        info = dict(org=org.id, fremd=fremd.id, users=users)
    assert _login(client, users["org_admin"]).status_code == 302
    yield info


def post(client, pfad, **daten):
    return client.post(pfad, data={"_csrf": client.cookies.get("ec_csrf"), **daten},
                       headers={"HX-Request": "true"}, follow_redirects=False)


def jobs(db, org_id):
    return db.query(SearchOutbox).filter_by(org_id=org_id, quelle="neuaufbau").all()


def test_schalter_speichern_plant_nur_beim_aktivieren(client, umgebung):
    for _ in range(2):
        response = post(client, "/admin/settings/org", org_name="Suchadmin", suche_aktiv_raw="1")
        assert response.status_code in (200, 303)
        with TestingSession() as db:
            set_tenant_context(db, None)
            assert db.query(OrgSettings).filter_by(org_id=umgebung["org"]).one().suche_aktiv
            zeilen = jobs(db, umgebung["org"])
            assert len(zeilen) == 4
            assert {j.quell_id for j in zeilen} == set(QUELLEN_LABELS)
    # Auch ohne Aenderung anderer Modulschalter muss das Audit funktionieren.
    assert post(client, "/admin/settings/org", org_name="Suchadmin").status_code in (200, 303)
    assert post(client, "/admin/settings/org", org_name="Suchadmin", suche_aktiv_raw="1").status_code in (200, 303)
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert len(jobs(db, umgebung["org"])) == 4
        assert db.query(AuditLog).filter_by(org_id=umgebung["org"], action="suche.org_toggle").count() == 3
    page = client.get("/admin/settings")
    assert 'name="suche_aktiv_raw"' in page.text
    assert 'href="/einstellungen/suche"' in page.text


def test_worker_neuaufbau(client, umgebung):
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=umgebung["org"]).one().suche_aktiv = True
        objekt = Objekt(org_id=umgebung["org"], nummer=78, name="Indexaufbau", status="freigegeben")
        db.add(objekt)
        db.flush()
        objekt_id = objekt.id
        job = SearchOutbox(org_id=umgebung["org"], quelle="neuaufbau", quell_id="objekt",
                           naechster_versuch_am=datetime.now(UTC).replace(tzinfo=None))
        db.add(job)
        db.commit()
        ident = job.id
        # Andere Tests im selben xdist-Worker hinterlassen Outbox-Zeilen; alle abarbeiten.
        assert verarbeite_faellige(db, limit=10_000) >= 1
        assert db.get(SearchOutbox, ident).status == "done"
        assert db.query(SearchDocument).filter_by(org_id=umgebung["org"], quelle="objekt",
                                                 quell_id=str(objekt_id)).one()


def test_status_rechte_zaehler_und_escaping(client, umgebung):
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    with TestingSession() as db:
        set_tenant_context(db, None)
        doc = SearchDocument(org_id=umgebung["org"], quelle="objekt", quell_id="status",
                             titel="Status", inhalt_hash="a" * 64, indexiert_am=jetzt)
        doc.chunks = [SearchChunk(org_id=umgebung["org"], chunk_nr=0, text="Status", inhalt_hash="b" * 64)]
        db.add(doc)
        for org_id, fehler in ((umgebung["org"], '<script>alert(1)</script>' + 'x' * 250),
                               (umgebung["fremd"], "FREMDER_FEHLER")):
            db.add(SearchOutbox(org_id=org_id, quelle="objekt", quell_id="kaputt", status="failed",
                               versuche=8, letzter_fehler=fehler, naechster_versuch_am=jetzt))
        db.add(SearchOutbox(org_id=umgebung["org"], quelle="objekt", quell_id="offen", status="retry",
                           naechster_versuch_am=jetzt, erstellt_am=jetzt))
        db.commit()
    page = client.get("/einstellungen/suche")
    assert page.status_code == 200
    assert 'data-quelle="objekt"><td>Objekte</td><td>1</td><td>1</td>' in page.text
    assert 'Offen: 1' in page.text and 'Fehlgeschlagen: 1' in page.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page.text
    assert '<script>alert' not in page.text and 'FREMDER_FEHLER' not in page.text
    assert 'x' * 180 not in page.text
    assert _login(client, umgebung["users"]["readonly"]).status_code == 302
    assert client.get("/einstellungen/suche", follow_redirects=False).status_code == 403
    for aktion in ("neuaufbau", "fehler-zuruecksetzen"):
        assert post(client, "/einstellungen/suche/" + aktion).status_code == 403


def test_neuaufbau_dedupe_reset_tenant_und_csrf(client, umgebung):
    for aktion in ("neuaufbau", "fehler-zuruecksetzen"):
        assert client.post("/einstellungen/suche/" + aktion).status_code == 403
    for _ in range(2):
        response = post(client, "/einstellungen/suche/neuaufbau")
        assert response.status_code == 200
        assert 'id="suche-admin-status"' in response.text and '<html' not in response.text
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert len(jobs(db, umgebung["org"])) == 4
        for org_id in (umgebung["org"], umgebung["fremd"]):
            db.add(SearchOutbox(org_id=org_id, quelle="objekt", quell_id="reset", status="failed", versuche=8,
                               naechster_versuch_am=jetzt + timedelta(days=1)))
        db.commit()
    assert post(client, "/einstellungen/suche/fehler-zuruecksetzen").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, None)
        eigener = db.query(SearchOutbox).filter_by(org_id=umgebung["org"], quell_id="reset").one()
        fremder = db.query(SearchOutbox).filter_by(org_id=umgebung["fremd"], quell_id="reset").one()
        assert eigener.status == "retry" and eigener.versuche == 0
        assert jetzt <= eigener.naechster_versuch_am <= datetime.now(UTC).replace(tzinfo=None)
        assert fremder.status == "failed" and fremder.versuche == 8


def test_systemflag(client, umgebung, monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_ENABLED", False)
    assert client.get("/einstellungen/suche").status_code == 404
    for aktion in ("neuaufbau", "fehler-zuruecksetzen"):
        assert post(client, "/einstellungen/suche/" + aktion).status_code == 404
    page = client.get("/admin/settings")
    assert 'name="suche_aktiv_raw" value="1"  disabled' in page.text
    assert post(client, "/admin/settings/org", suche_aktiv_raw="1").status_code in (200, 303)
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert not db.query(OrgSettings).filter_by(org_id=umgebung["org"]).one().suche_aktiv
        assert not jobs(db, umgebung["org"])
