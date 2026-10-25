"""Phase 7: zentrale Bytes, Freigaben, Pflege, Seiten, MCP und Tenantgrenzen."""

import importlib.util
import io
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pypdf import PdfReader, PdfWriter

from app.core.tenant import set_tenant_context
from app.mcp.context import MCPPermissionError
from app.mcp.registry import TOOLS
from app.models.incident import Incident
from app.models.katplan import (
    KatplanAenderung,
    KatplanAnhang,
    KatplanDokumentSeite,
    KatplanPlan,
    KatplanVersion,
)
from app.models.master import OrgSettings, SystemSettings
from app.models.user import User
from app.services import katplan_anhang_service as service
from app.services import katplan_quelle_service as quellen
from app.services.katplan_freigabe_service import freigeben
from app.services.katplan_service import KatplanKonflikt, lege_element_an
from app.services.storage_service import reconcile_storage
from tests.conftest import TestingSession
from tests.test_dokumentviewer import dokument_pdf
from tests.test_dokumentviewer import dokumente as dokumente
from tests.test_katplan_k1b import login, post
from tests.test_katplan_k1b import umgebung as basis_umgebung  # noqa: F401
from tests.test_katplan_k6a import usage
from tests.test_mcp_katplan import rufe

NEUE_TOOLS = {
    "einsatzwissen_kategorien", "katplan_dokumentbibliothek", "katplan_dokument_referenzieren",
    "katplan_dokument_pflegen", "katplan_pruefstatus", "einsatz_dokumente_auflisten",
    "einsatz_dokument_zuordnen", "dokumentdruck_vorbereiten", "katplan_dokument_zusammenfassung",
}


def bestand(db, info):
    set_tenant_context(db, info["org_id"])
    user = db.query(User).filter_by(username=info["users"]["katplan_freigeber"]).one()
    plan = db.get(KatplanPlan, info["plan_id"])
    a = db.query(KatplanAnhang).filter_by(uuid=info["dateien"][0]["uuid"]).one()
    return plan, user, a


def test_identische_uploads_eine_datei_quota_und_letzte_referenz(dokumente):
    with TestingSession() as db:
        plan, user, _ = bestand(db, dokumente)
        data = dokument_pdf(2, kennung="Neu dedupliziert")
        vor = usage(db, plan.org_id)
        a = service.anhang_hochladen(db, plan, user, data, "a.pdf", "application/pdf")
        b = service.anhang_hochladen(db, plan, user, data, "b.pdf", "application/pdf")
        assert a.uuid != b.uuid and a.pfad == b.pfad and a.datei_hash == b.datei_hash
        assert usage(db, plan.org_id) == vor + len(data)
        pfad = quellen.absolute_pfad(a.pfad)
        assert pfad.read_bytes() == data
        assert reconcile_storage(db, plan.org_id) == usage(db, plan.org_id)
        service.anhang_entfernen(db, plan, user, a)
        assert pfad.is_file() and usage(db, plan.org_id) == vor + len(data)
        service.anhang_entfernen(db, plan, user, b)
        assert not pfad.exists() and usage(db, plan.org_id) == vor


def test_mehrere_massnahmen_plaene_seiten_und_freigabe(client, dokumente):
    with TestingSession() as db:
        plan, user, a = bestand(db, dokumente)
        anderer_plan = KatplanPlan(org_id=plan.org_id, titel="Zweiter Wissenseintrag")
        db.add(anderer_plan)
        db.flush()
        e1 = lege_element_an(db, anderer_plan, user, typ="massnahme", titel="Erste Maßnahme")
        e2 = lege_element_an(db, anderer_plan, user, typ="massnahme", titel="Zweite Maßnahme")
        refs = [service.dokument_referenzieren(db, anderer_plan, user, "anhang:" + a.uuid,
                    element=e, seiten="3-5,8") for e in (e1, e2)]
        assert {r.pfad for r in refs} == {a.pfad}
        version = freigeben(db, anderer_plan, user)
        uid, vid = refs[0].uuid, version.id
        db.commit()
    login(client, dokumente, "readonly")
    url = f"/plaene/anhang/{uid}?version_id={vid}"
    response = client.get(url + "&auszug=1")
    assert response.status_code == 200
    assert len(PdfReader(io.BytesIO(response.content)).pages) == 4
    response = client.get(url + "&auszug=1&auswahl=2-3")
    assert len(PdfReader(io.BytesIO(response.content)).pages) == 2
    assert len(PdfReader(io.BytesIO(client.get(url).content)).pages) == 12


def test_pflege_snapshot_konflikt_und_zustaendige(client, dokumente):
    with TestingSession() as db:
        plan, user, a = bestand(db, dokumente)
        original = db.get(KatplanVersion, dokumente["version_id"]).plan_json
        version, uid, user_id = plan.version, a.uuid, user.id
    result = rufe(dokumente, "katplan_dokument_pflegen", "katplan_freigeber", plan_id=dokumente["plan_id"],
        version=version, anhang_uuid=uid, felder={"pruefdatum": "2026-10-25", "gueltig_bis": "2027-10-25",
            "pruefintervall_monate": 12, "zustaendig_user_id": user_id, "sort": -5})
    assert result["status"] == "in_ueberarbeitung" and result["anhang"]["pruefdatum"] == "2026-10-25"
    with pytest.raises(ValueError, match="Versionskonflikt"):
        rufe(dokumente, "katplan_dokument_pflegen", "katplan_freigeber", plan_id=dokumente["plan_id"],
             version=version, anhang_uuid=uid, felder={"sort": 99})
    with TestingSession() as db:
        plan, user, a = bestand(db, dokumente)
        assert db.get(KatplanVersion, dokumente["version_id"]).plan_json == original
        assert a.pruefdatum == date(2026, 10, 25) and a.sort == -5
        assert db.query(KatplanAenderung).filter_by(plan_id=plan.id, quelle="mcp", feld=uid).count() >= 1
        with pytest.raises(ValueError, match="Person"):
            service.anhang_aendern(db, plan, user, a, beschriftung=a.beschriftung, urheber="",
                aufnahme_datum=None, vertraulich=False, sort=0, pflege={"zustaendig_user_id": 99999999})
    login(client, dokumente)
    response = post(client, f'/plaene/{dokumente["plan_id"]}/anhaenge/{uid}', version=version, sort=100)
    assert response.status_code == 409
    assert "zwischenzeitlich" in response.text


@pytest.mark.parametrize("felder", [{"status": "freigegeben"}, {"sort": True}, {"beschriftung": 5},
    {"pruefintervall_monate": 0}, {"pruefintervall_monate": 61}, {"pruefdatum": "kein Datum"},
    {"gueltig_bis": 3}, {"zustaendig_user_id": True}])
def test_mcp_ungueltige_pflege_rollback(dokumente, felder):
    with TestingSession() as db:
        plan, _, a = bestand(db, dokumente)
        version, uid = plan.version, a.uuid
    with pytest.raises(ValueError):
        rufe(dokumente, "katplan_dokument_pflegen", "katplan_freigeber", plan_id=dokumente["plan_id"],
             version=version, anhang_uuid=uid, felder=felder)
    with TestingSession() as db:
        plan, _, a = bestand(db, dokumente)
        assert plan.version == version and a.sort == 0


def test_austausch_erhaelt_alte_freigabe_und_andere_zuordnungen(client, dokumente):
    with TestingSession() as db:
        plan, user, a = bestand(db, dokumente)
        andere = service.dokument_referenzieren(db, plan, user, "anhang:" + a.uuid)
        version, uid = plan.version, a.uuid
        alte_bytes, andere_uuid = quellen.absolute_pfad(a.pfad).read_bytes(), andere.uuid
        db.commit()
    login(client, dokumente)
    url = f'/plaene/{dokumente["plan_id"]}/anhaenge/{uid}/ersetzen'
    response = client.post(url, data={"_csrf": client.cookies.get("ec_csrf"), "version": version},
        files={"datei": ("neu.pdf", dokument_pdf(3), "application/pdf")})
    assert response.status_code == 200
    with TestingSession() as db:
        plan, _, a = bestand(db, dokumente)
        assert a.geloescht_am is not None and plan.status == "in_ueberarbeitung"
        assert quellen.absolute_pfad(a.pfad).read_bytes() == alte_bytes
        assert db.query(KatplanAenderung).filter_by(plan_id=plan.id, aktion="ersetzt").count() == 1
    login(client, dokumente, "readonly")
    assert client.get(f'/plaene/anhang/{uid}?version_id={dokumente["version_id"]}').content == alte_bytes
    assert client.get(f"/plaene/anhang/{andere_uuid}").status_code == 404


def test_quelle_unveraendert_dedup_mehrfachreferenz_und_loeschschutz(dokumente):
    with TestingSession() as db:
        plan, user, _ = bestand(db, dokumente)
        data = dokument_pdf(3, kennung="Lehrbehelf")
        q = quellen.quelle_hochladen(db, plan, user, data, "lehrbehelf.pdf", titel="Lehrbehelf")
        assert quellen.quelle_hochladen(db, plan, user, data, "erneut.pdf", titel="Lehrbehelf").id == q.id
        ref = service.dokument_referenzieren(db, plan, user, "quelle:" + q.uuid, seiten="2-3")
        upload = service.anhang_hochladen(db, plan, user, data, "anhang.pdf", "application/pdf")
        assert q.pfad == ref.pfad == upload.pfad
        assert quellen.absolute_pfad(q.pfad).read_bytes() == data
        assert reconcile_storage(db, plan.org_id) == usage(db, plan.org_id)
        quellen.quelle_entfernen(db, plan, user, q)
        assert q.archiviert and quellen.absolute_pfad(ref.pfad).is_file()


def test_mcp_tenant_leser_archiv_modul_und_kategorien(dokumente):
    assert NEUE_TOOLS <= set(TOOLS)
    assert all(TOOLS[name].module_check for name in NEUE_TOOLS)
    assert len(rufe(dokumente, "einsatzwissen_kategorien")["kategorien"]) == 6
    with pytest.raises(MCPPermissionError):
        rufe(dokumente, "katplan_dokumentbibliothek")
    assert rufe(dokumente, "katplan_dokumentbibliothek", "fremd")["dokumente"] == []
    with TestingSession() as db:
        plan, user, a = bestand(db, dokumente)
        with pytest.raises(KatplanKonflikt):
            service.pflege_version(db, plan, user, plan.version - 1)
        fremd = db.query(User).execution_options(include_all_tenants=True).filter_by(
            username=dokumente["users"]["fremd"]).one()
        with pytest.raises(ValueError):
            service.dokument_referenzieren(db, plan, fremd, "anhang:" + a.uuid)
        plan.status = "archiviert"
        db.commit()
    assert rufe(dokumente, "katplan_dokumentbibliothek", "katplan_verwalter")["dokumente"] == []
    with TestingSession() as db:
        set_tenant_context(db, dokumente["org_id"])
        db.query(OrgSettings).filter_by(org_id=dokumente["org_id"]).one().katplan_modul_aktiv = False
        db.commit()
    with pytest.raises(MCPPermissionError):
        rufe(dokumente, "einsatzwissen_kategorien")


def test_mcp_einsatz_uebernahme_liste_druck_und_rechte(client, dokumente):
    with TestingSession() as db:
        set_tenant_context(db, dokumente["org_id"])
        incident = Incident(primary_org_id=dokumente["org_id"], alarm_type_code="TEST", status="active")
        db.add(incident)
        db.commit()
        ident = incident.id
    ref = f'/plaene/anhang/{dokumente["dateien"][0]["uuid"]}?version_id={dokumente["version_id"]}'
    daten = {"art": "einsatz", "einsatz_id": ident, "documents": [{"url": ref, "pages": "2-3"}]}
    with pytest.raises(MCPPermissionError):
        rufe(dokumente, "einsatz_dokument_zuordnen", daten=daten)
    result = rufe(dokumente, "einsatz_dokument_zuordnen", "org_admin", daten=daten)
    liste = rufe(dokumente, "einsatz_dokumente_auflisten", art="einsatz", einsatz_id=ident)
    assert [d["id"] for d in liste["dokumente"]] == result["dokument_ids"]
    assert liste["dokumente"][0]["seitenzahl"] == 2
    druck = rufe(dokumente, "dokumentdruck_vorbereiten", documents=[{"url": ref, "pages": "2-3"}])
    assert druck["page_count"] == 2
    login(client, dokumente, "readonly")
    assert client.get(druck["pdf_url"]).status_code == 200
    with pytest.raises(ValueError):
        rufe(dokumente, "dokumentdruck_vorbereiten", documents=[{"url": "https://evil.invalid/x", "pages": "alle"}])


def test_ueberschriften_aus_lesezeichen_und_heuristik(tmp_path):
    pdf = PdfWriter(clone_from=io.BytesIO(dokument_pdf(3)))
    pdf.add_outline_item("Kapitel Sicherheit", 1)
    datei = tmp_path / "quelle.pdf"
    pdf.write(datei)
    seiten = [SimpleNamespace(seiten_nr=1, volltext="1. Einführung\nFließtext"),
              SimpleNamespace(seiten_nr=2, volltext="ANDERE ÜBERSCHRIFT\nFließtext"),
              SimpleNamespace(seiten_nr=3, volltext="Ein normaler Absatz ohne Überschrift.")]
    assert quellen.erkenne_ueberschriften(datei, seiten) == {1: "1. Einführung", 2: "Kapitel Sicherheit"}


def test_migration_0311_altbestand_rollback_und_erneutes_upgrade():
    path = Path("alembic/versions/0311_einsatzwissen_pflege.py")
    spec = importlib.util.spec_from_file_location("migration_phase7", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE katplan_anhang (id INTEGER PRIMARY KEY, pfad TEXT)")
        conn.exec_driver_sql("CREATE TABLE katplan_dokument (id INTEGER PRIMARY KEY, pfad TEXT)")
        conn.exec_driver_sql("INSERT INTO katplan_anhang VALUES (1, 'original.pdf')")
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert conn.exec_driver_sql("SELECT pfad, pruefdatum, datei_hash FROM katplan_anhang").one() == (
            "original.pdf", None, None)
        migration.downgrade()
        assert conn.exec_driver_sql("SELECT pfad FROM katplan_anhang").scalar() == "original.pdf"
        migration.upgrade()
        assert migration.down_revision == "0310"
    engine.dispose()


def test_dokumentprueftermin_monatsende_und_ablauf():
    status = service.dokument_pruefstatus({"pruefdatum": "2026-01-31", "pruefintervall_monate": 1,
                                          "gueltig_bis": "2026-02-15"}, date(2026, 2, 28))
    assert status == {"naechste_pruefung": "2026-02-28", "pruefung_faellig": True,
                      "pruefangabe_fehlt": False, "gueltigkeit_abgelaufen": True}
    assert service.dokument_pruefstatus({}, date(2026, 1, 1))["pruefangabe_fehlt"]


def test_quellenueberblick_mcp_nur_berechtigte_seiten(dokumente):
    with TestingSession() as db:
        plan, user, _ = bestand(db, dokumente)
        q = quellen.quelle_hochladen(db, plan, user, dokument_pdf(3), "lehrbehelf.pdf", titel="Lehrbehelf")
        q.status = "fertig"
        for nr in range(1, 4):
            db.add(KatplanDokumentSeite(org_id=plan.org_id, plan_id=plan.id, dokument_id=q.id,
                seiten_nr=nr, einzel_pdf_pfad="seite.pdf", volltext="Originalauszug " + str(nr)))
        db.flush()
        freigeben(db, plan, user)
        ident = q.id
        db.commit()
    antwort = rufe(dokumente, "katplan_dokument_zusammenfassung", plan_id=dokumente["plan_id"],
                    dokument_id=ident, von=1, bis=3)
    assert antwort["verfahren"] == "extraktiv_lokal"
    assert antwort["abschnitte"] == [{"seite": nr, "auszug": "Originalauszug " + str(nr), "gekuerzt": False}
                                     for nr in range(1, 4)]
    with pytest.raises(ValueError):
        rufe(dokumente, "katplan_dokument_zusammenfassung", plan_id=dokumente["plan_id"],
             dokument_id=ident, von=1, bis=21)
    with pytest.raises(ValueError):
        rufe(dokumente, "katplan_dokument_zusammenfassung", "fremd", plan_id=dokumente["plan_id"],
             dokument_id=ident)


def test_neue_mcp_tools_http_registriert_und_schreibrechte(client, dokumente):
    from tests.test_mcp_fundament import _mcp, _oauth_tokens
    from tests.test_mcp_strassensperren import _rpc, _rufe

    with TestingSession() as db:
        plan, user, _ = bestand(db, dokumente)
        db.query(OrgSettings).filter_by(org_id=plan.org_id).one().mcp_modul_aktiv = True
        flag = db.query(SystemSettings).filter_by(key="mcp_module_enabled").first()
        if flag is None:
            db.add(SystemSettings(key="mcp_module_enabled", value="true"))
        else:
            flag.value = "true"
        ident = {"id": user.id, "org_id": user.org_id, "username": user.username}
        db.commit()
    token = _oauth_tokens(client, ident)["access_token"]
    liste = _rpc(_mcp(client, token, "tools/list", {}, 12))["result"]["tools"]
    assert NEUE_TOOLS <= {t["name"] for t in liste}
    antwort = _rufe(client, token, "einsatzwissen_kategorien")
    assert len(antwort["kategorien"]) == 6
    with TestingSession() as db:
        set_tenant_context(db, dokumente["org_id"])
        db.query(OrgSettings).filter_by(org_id=dokumente["org_id"]).one().katplan_modul_aktiv = False
        db.commit()
    liste = _rpc(_mcp(client, token, "tools/list", {}, 13))["result"]["tools"]
    assert not NEUE_TOOLS & {t["name"] for t in liste}
    assert "__fehler__" in _rufe(client, token, "einsatzwissen_kategorien")


@pytest.mark.parametrize("defekt", ["fehlt", "beschaedigt"])
def test_dokumentreferenz_mit_defekter_datei_ohne_halbe_zuordnung(dokumente, defekt):
    with TestingSession() as db:
        plan, user, a = bestand(db, dokumente)
        pfad = quellen.absolute_pfad(a.pfad)
        if defekt == "fehlt":
            pfad.unlink()
        else:
            pfad.write_bytes(b"kein PDF")
        vorher = db.query(KatplanAnhang).filter_by(plan_id=plan.id).count()
        version = plan.version
        with pytest.raises(ValueError, match="Dokument"):
            service.dokument_referenzieren(db, plan, user, "anhang:" + a.uuid, seiten="1")
        assert db.query(KatplanAnhang).filter_by(plan_id=plan.id).count() == vorher
        assert plan.version == version
