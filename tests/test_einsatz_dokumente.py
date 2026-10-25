"""Phase 4: echte Übernahmen, unveränderliche Seiten und gezielte GK-Freigaben."""

import io
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pypdf import PdfReader

from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.models.einsatz_dokument import EinsatzDokument, EinsatzDokumentFassung
from app.models.incident import Incident, Task
from app.models.katplan import KatplanPlan
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageEinheitLeader,
    LageEinheitZugang,
    MajorIncident,
)
from app.models.master import OrgSettings
from app.models.user import AuditLog, User
from app.services import gk_zugang_service
from app.services.katplan_freigabe_service import freigeben
from tests.conftest import TestingSession
from tests.test_dokumentdruck import post, referenz
from tests.test_dokumentviewer import dokument_pdf
from tests.test_dokumentviewer import dokumente as dokumente
from tests.test_katplan_k1b import login
from tests.test_katplan_k1b import umgebung as basis_umgebung  # noqa: F401


@pytest.fixture
def einsatz(dokumente):
    with TestingSession() as db:
        set_tenant_context(db, None)
        org = dokumente["org_id"]
        incident = Incident(primary_org_id=org, alarm_type_code="TEST", status="active")
        lage = MajorIncident(org_id=org, name="Dokumentenlage", status="active")
        db.add_all([incident, lage])
        db.flush()
        task = Task(incident_id=incident.id, title="Wasserentnahme")
        site = IncidentSite(major_incident_id=lage.id, org_id=org, bezeichnung="Auftrag Wasserentnahme")
        db.add_all([task, site])
        db.flush()
        einheiten = []
        cookies = []
        dispatches = []
        db.query(OrgSettings).filter_by(org_id=org).one().gk_zugang_aktiv = True
        for label in ("RLF", "TLF"):
            einheit = LageEinheit(lage_id=lage.id, label=label, status="bereitgestellt")
            db.add(einheit)
            db.flush()
            leader = LageEinheitLeader(einheit_id=einheit.id, person_name="GK " + label,
                phone_e164="+436641234567", phone_version=1, start_at=datetime.now(UTC), rolle="fuehrer")
            db.add(leader)
            db.flush()
            einheit.leader_assignment_id = leader.id
            db.flush()
            neu = gk_zugang_service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="test")
            cookie, _ = gk_zugang_service.sitzung_anlegen(db, db.get(LageEinheitZugang, neu.zugang_id),
                user_agent=None, ip=None, verifiziert=True)
            cookies.append(cookie)
            einheiten.append(einheit.id)
            dispatch = EinheitSiteDispatch(einheit_id=einheit.id, site_id=site.id, dispatched_at=datetime.now(UTC))
            db.add(dispatch)
            db.flush()
            dispatches.append(dispatch.id)
        db.commit()
        return {"incident": incident.id, "task": task.id, "lage": lage.id, "site": site.id,
                "einheiten": einheiten, "cookies": cookies, "dispatches": dispatches}


def uebernehmen(client, dokumente, einsatz, **overrides):
    return post(client, "/einsatz-dokumente/uebernehmen", **{
        "art": "lage", "einsatz_id": einsatz["lage"], "documents": [referenz(dokumente, pages="4–6")],
        **overrides})


def lesen(client, ident):
    response = client.get(f"/einsatz-dokumente/datei/{ident}")
    assert response.status_code == 200, response.text[:500]
    return response, PdfReader(io.BytesIO(response.content))


def form(client, path, **daten):
    return client.post(path, data={"_csrf": client.cookies.get("ec_csrf", ""), **daten}, follow_redirects=False)


def gk(client, einsatz, index=0):
    client.cookies.clear()
    client.cookies.set(gk_zugang_service.COOKIE, einsatz["cookies"][index])


def test_abnahme_seiten_board_auftrag_gk_quellupdate_und_druck(client, dokumente, einsatz):
    login(client, dokumente, "org_admin")
    result = uebernehmen(client, dokumente, einsatz)
    assert result.status_code == 200, result.text
    ident = result.json()["ids"][0]
    response, reader = lesen(client, ident)
    assert [p.extract_text().splitlines()[0] for p in reader.pages] == [
        f"Unterlage - Seite {n}" for n in (4, 5, 6)]
    original = response.content
    board = client.get(f'/einsatz-dokumente/liste/lage/{einsatz["lage"]}')
    assert board.status_code == 200, board.text
    assert all(text in board.text for text in ("Unterlage 1", "4,5,6", "Freigabe", "Verknüpfung öffnen"))
    result = form(client, f"/einsatz-dokumente/{ident}/zuordnung", revision=1, auftrag_id=einsatz["site"],
                  einheit_ids=[einsatz["einheiten"][0]])
    assert result.status_code == 303, result.text
    gk(client, einsatz)
    html = client.get("/einheit/api/dokumente")
    assert html.status_code == 200 and "Unterlage 1" in html.text
    url = f"/einheit/api/dokumente/{ident}/pdf"
    assert client.get(url).content == original
    # Viewer-/Druckauswahl bezieht sich auf die drei tatsächlich freigegebenen Seiten.
    druck = client.get(url + "?auswahl=1,3")
    assert len(PdfReader(io.BytesIO(druck.content)).pages) == 2
    assert client.get(url + "?auswahl=4").status_code == 422
    assert client.get(url + "?seiten=1-12").status_code == 422
    gk(client, einsatz, 1)
    assert "Unterlage 1" not in client.get("/einheit/api/dokumente").text
    assert client.get(url).status_code == 404
    assert client.get(f"/einsatz-dokumente/datei/{ident}", follow_redirects=False).status_code in (302, 401, 403)
    # Aktualisierung/Freigabe und sogar Verlust der Quelldatei ändern die Einsatzfassung nicht.
    dokumente["dateien"][0]["path"].write_bytes(dokument_pdf(2))
    with TestingSession() as db:
        set_tenant_context(db, dokumente["org_id"])
        plan = db.get(KatplanPlan, dokumente["plan_id"])
        plan.titel = "Neue Quellversion"
        plan.status = "in_ueberarbeitung"
        user = db.query(User).filter_by(username=dokumente["users"]["katplan_freigeber"]).one()
        freigeben(db, plan, user)
        db.commit()
    dokumente["dateien"][0]["path"].unlink()
    login(client, dokumente, "org_admin")
    assert lesen(client, ident)[0].content == original


def test_mehrere_dokumente_atomare_uebernahme_und_deduplizierung(client, dokumente, einsatz):
    login(client, dokumente, "org_admin")
    result = uebernehmen(client, dokumente, einsatz, documents=[referenz(dokumente, 0, "4-6"),
                                                              referenz(dokumente, 1, "alle")])
    assert result.status_code == 200, result.text
    ids = result.json()["ids"]
    assert len(ids) == 2
    result = uebernehmen(client, dokumente, einsatz)
    assert result.status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, dokumente["org_id"])
        docs = db.query(EinsatzDokument).filter_by(lage_id=einsatz["lage"]).all()
        assert len(docs) == 3
        assert docs[0].fassung_id == docs[2].fassung_id
        vorher = db.query(EinsatzDokumentFassung).count()
    bad = uebernehmen(client, dokumente, einsatz, documents=[referenz(dokumente, 0, "1"),
                                                           referenz(dokumente, 1, "999")])
    assert bad.status_code == 422
    with TestingSession() as db:
        set_tenant_context(db, dokumente["org_id"])
        assert db.query(EinsatzDokument).filter_by(lage_id=einsatz["lage"]).count() == 3
        assert db.query(EinsatzDokumentFassung).count() == vorher


def test_rechte_csrf_fremde_ziele_und_ungueltige_quellen(client, dokumente, einsatz):
    login(client, dokumente, "readonly")
    assert uebernehmen(client, dokumente, einsatz).status_code == 403
    login(client, dokumente, "katplan_verwalter")
    assert uebernehmen(client, dokumente, einsatz).status_code == 403
    login(client, dokumente, "org_admin")
    assert client.post("/einsatz-dokumente/uebernehmen", json={"art": "lage", "einsatz_id": einsatz["lage"],
                       "documents": [referenz(dokumente)]}).status_code == 403
    for url in ("https://example.com/file.pdf", "/etc/passwd", "//example.com/file.pdf",
                referenz(dokumente)["url"] + "&entwurf=1", "/plaene/anhang/fehlt"):
        assert uebernehmen(client, dokumente, einsatz, documents=[{"url": url}]).status_code in (404, 422)
    assert uebernehmen(client, dokumente, einsatz, einsatz_id=99999999).status_code == 404
    assert uebernehmen(client, dokumente, einsatz, einheit_ids=[99999999]).status_code == 404
    assert uebernehmen(client, dokumente, einsatz, auftrag_id=99999999).status_code == 404
    login(client, dokumente, "fremd")
    assert uebernehmen(client, dokumente, einsatz).status_code == 403


def test_vertraulich_nur_ausdrueckliche_freigabe_und_leserfilter(client, dokumente, einsatz):
    login(client, dokumente, "org_admin")
    refs = [{"url": f'/plaene/anhang/{dokumente["geheim"]}?version_id={dokumente["version_id"]}'}]
    result = uebernehmen(client, dokumente, einsatz, documents=refs, einheit_ids=[einsatz["einheiten"][0]])
    assert result.status_code == 422
    result = uebernehmen(client, dokumente, einsatz, documents=refs)
    assert result.status_code == 200, result.text
    ident = result.json()["ids"][0]
    gk(client, einsatz)
    assert client.get(f"/einheit/api/dokumente/{ident}/pdf").status_code == 404
    login(client, dokumente, "readonly")
    assert client.get(f"/einsatz-dokumente/datei/{ident}").status_code == 404
    assert "Geheime Unterlage" not in client.get(f'/einsatz-dokumente/liste/lage/{einsatz["lage"]}').text
    login(client, dokumente, "org_admin")
    assert form(client, f"/einsatz-dokumente/{ident}/zuordnung", revision=1,
                einheit_ids=[einsatz["einheiten"][0]]).status_code == 422
    assert form(client, f"/einsatz-dokumente/{ident}/zuordnung", revision=1,
                einheit_ids=[einsatz["einheiten"][0]], vertraulich_bestaetigt=True).status_code == 303
    gk(client, einsatz)
    assert client.get(f"/einheit/api/dokumente/{ident}/pdf").status_code == 200


def test_auftragsfreigabe_widerruf_entfernen_und_protokoll(client, dokumente, einsatz):
    login(client, dokumente, "org_admin")
    ident = uebernehmen(client, dokumente, einsatz, auftrag_id=einsatz["site"]).json()["ids"][0]
    gk(client, einsatz)
    url = f"/einheit/api/dokumente/{ident}/pdf"
    assert client.get(url).status_code == 404  # Zuordnung ist keine Freigabe.
    login(client, dokumente, "org_admin")
    assert form(client, f"/einsatz-dokumente/{ident}/zuordnung", revision=1,
                auftrag_id=einsatz["site"], auftrag_freigeben=True).status_code == 303
    gk(client, einsatz)
    assert client.get(url).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.get(EinheitSiteDispatch, einsatz["dispatches"][0]).withdrawn_at = datetime.now(UTC)
        db.commit()
    assert client.get(url).status_code == 404
    gk(client, einsatz, 1)
    assert client.get(url).status_code == 200
    login(client, dokumente, "org_admin")
    assert form(client, f"/einsatz-dokumente/{ident}/entfernen", revision=1).status_code == 409
    assert form(client, f"/einsatz-dokumente/{ident}/entfernen", revision=2).status_code == 303
    assert client.get(f"/einsatz-dokumente/datei/{ident}").status_code == 404
    gk(client, einsatz, 1)
    assert client.get(url).status_code == 404
    with TestingSession() as db:
        set_tenant_context(db, None)
        logs = db.query(AuditLog).filter_by(entity_type="einsatz_dokument", entity_id=ident).all()
        assert {entry.action for entry in logs} == {
            "einsatz.dokument.uebernommen", "einsatz.dokument.zuordnung_geaendert", "einsatz.dokument.entfernt"}
        assert db.get(EinsatzDokument, ident).entfernt_am


def test_normaler_einsatz_abschluss_archiv_und_endgueltiges_loeschen(client, dokumente, einsatz):
    from app.services.einsatz_dokument_service import endgueltig_entfernen

    login(client, dokumente, "org_admin")
    ident = uebernehmen(client, dokumente, einsatz, art="einsatz", einsatz_id=einsatz["incident"],
                        auftrag_id=einsatz["task"]).json()["ids"][0]
    original = lesen(client, ident)[0].content
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.get(Incident, einsatz["incident"]).status = "closed"
        db.commit()
    assert lesen(client, ident)[0].content == original
    assert client.get(f'/einsatz-dokumente/einsatz/{einsatz["incident"]}').status_code == 200
    assert uebernehmen(client, dokumente, einsatz, art="einsatz", einsatz_id=einsatz["incident"]).status_code == 409
    assert form(client, f"/einsatz-dokumente/{ident}/entfernen", revision=1).status_code == 409
    # Derselbe Inhalt in einer anderen Lage muss beim endgültigen Löschen erhalten bleiben.
    anderer = uebernehmen(client, dokumente, einsatz).json()["ids"][0]
    with TestingSession() as db:
        set_tenant_context(db, None)
        fid = db.get(EinsatzDokument, ident).fassung_id
        endgueltig_entfernen(db, einsatz["incident"])
        db.commit()
        assert db.get(EinsatzDokumentFassung, fid) is not None
    assert lesen(client, anderer)[0].content == original


def test_allgemeine_boardtokens_keine_dokumentenrechte(client, dokumente, einsatz):
    login(client, dokumente, "org_admin")
    ident = uebernehmen(client, dokumente, einsatz).json()["ids"][0]
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=dokumente["users"]["org_admin"]).one()
        token = sign_session(user.id, qr=True, lage_id=einsatz["lage"])
    client.cookies.clear()
    client.cookies.set("session", token)
    assert client.get(f"/einsatz-dokumente/datei/{ident}", follow_redirects=False).status_code in (302, 401, 403)


def test_sammlung_und_einzelbild(client, dokumente, einsatz):
    from app.models.katplan import KatplanElement

    login(client, dokumente, "org_admin")
    with TestingSession() as db:
        set_tenant_context(db, None)
        uuid = db.get(KatplanElement, dokumente["element_id"]).uuid
    response = client.get(f'/einsatz-dokumente/sammlung/{dokumente["plan_id"]}/{uuid}'
                          f'?version_id={dokumente["version_id"]}')
    assert response.status_code == 200, response.text
    docs = response.json()["documents"]
    assert len(docs) == 5
    result = uebernehmen(client, dokumente, einsatz, documents=[{"url": d["url"]} for d in docs])
    assert result.status_code == 200, result.text
    assert len(result.json()["ids"]) == 5
    assert any(lesen(client, ident)[1].pages[0].images for ident in result.json()["ids"])


def test_nur_freigegebene_dokumente_und_automatisch_gepinnte_version(client, dokumente, einsatz):
    from app.models.katplan import KatplanVersion
    from app.services.katplan_anhang_service import anhang_hochladen

    login(client, dokumente, "org_admin")
    result = uebernehmen(client, dokumente, einsatz, documents=[
        {"url": referenz(dokumente)["url"].split("?")[0], "pages": "1"}])
    assert result.status_code == 200, result.text
    with TestingSession() as db:
        set_tenant_context(db, dokumente["org_id"])
        obj = db.get(EinsatzDokument, result.json()["ids"][0])
        assert f'version_id={dokumente["version_id"]}' in obj.quell_url
        assert json.loads(obj.quelle_json)["freigabe_hash"] == db.get(
            KatplanVersion, dokumente["version_id"]).inhalt_hash
        plan = db.get(KatplanPlan, dokumente["plan_id"])
        user = db.query(User).filter_by(username=dokumente["users"]["org_admin"]).one()
        draft = anhang_hochladen(db, plan, user, dokument_pdf(), "entwurf.pdf", "application/pdf")
        draft_url = f"/plaene/anhang/{draft.uuid}"
        db.commit()
    assert uebernehmen(client, dokumente, einsatz, documents=[{"url": draft_url}]).status_code == 404


def test_verschachtelte_auszuege_originalseiten_und_zentraler_druck(client, dokumente, einsatz):
    login(client, dokumente, "org_admin")
    ref = referenz(dokumente, pages="1,3")
    ref["url"] += "&seiten=3-8&auswahl=2-4"
    ident = uebernehmen(client, dokumente, einsatz, documents=[ref]).json()["ids"][0]
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.get(EinsatzDokument, ident).seiten == "4,6"
    result = post(client, "/druck/viewer/vorbereiten", documents=[
        {"url": f"/einsatz-dokumente/datei/{ident}", "pages": "2"}])
    assert result.status_code == 200, result.text
    reader = PdfReader(io.BytesIO(client.get(result.json()["pdf_url"]).content))
    assert reader.pages[0].extract_text().startswith("Unterlage - Seite 6")
    assert form(client, f"/einsatz-dokumente/{ident}/entfernen", revision=1).status_code == 303
    assert client.get(result.json()["pdf_url"]).status_code == 404


def test_rettungskarte_mit_modulpruefung(client, dokumente, einsatz, tmp_path, monkeypatch):
    from app.config import settings
    from app.models.master import SystemSettings
    from app.models.nachschlagewerk import RettungsdatenblattCache

    monkeypatch.setattr(settings, "NACHSCHLAGEWERK_DATA_DIR", str(tmp_path))
    (tmp_path / "rettung.pdf").write_bytes(dokument_pdf())
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="nachschlagewerke_module_enabled").first()
        if flag is None:
            flag = SystemSettings(key="nachschlagewerke_module_enabled")
            db.add(flag)
        flag.value = "true"
        db.query(OrgSettings).filter_by(org_id=dokumente["org_id"]).one().nachschlagewerke_module_enabled = True
        karte = RettungsdatenblattCache(hersteller="Test", modell=uuid4().hex, pfad="rettung.pdf", sha256="f" * 64)
        db.add(karte)
        db.commit()
        url = f"/nachschlagewerk-cache/rettungskarten/{karte.id}/original.pdf"
    login(client, dokumente, "org_admin")
    result = uebernehmen(client, dokumente, einsatz, documents=[{"url": url, "pages": "4-6"}])
    assert result.status_code == 200, result.text
    ident = result.json()["ids"][0]
    assert len(lesen(client, ident)[1].pages) == 3
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=dokumente["org_id"]).one().nachschlagewerke_module_enabled = False
        db.commit()
    assert uebernehmen(client, dokumente, einsatz, documents=[{"url": url}]).status_code == 404
    assert len(lesen(client, ident)[1].pages) == 3


def test_gk_widerruf_fremde_lage_und_kontext(client, dokumente, einsatz):
    login(client, dokumente, "org_admin")
    ident = uebernehmen(client, dokumente, einsatz, einheit_ids=[einsatz["einheiten"][0]]).json()["ids"][0]
    gk(client, einsatz)
    url = f"/einheit/api/dokumente/{ident}/pdf"
    assert client.get(url).status_code == 200
    assert client.get(url, headers={"X-EC-Einheit-Kontext": str(einsatz["einheiten"][1])}).status_code == 409
    with TestingSession() as db:
        set_tenant_context(db, None)
        z = db.query(LageEinheitZugang).filter_by(einheit_id=einsatz["einheiten"][0], typ="personal").one()
        z.status = "widerrufen"
        db.commit()
    assert client.get(url).status_code == 401


def test_quota_deduplizierung_reconcile_und_letzte_loeschung(client, dokumente, einsatz):
    from app.services.einsatz_dokument_service import endgueltig_entfernen
    from app.services.storage_service import get_org_storage_info, reconcile_storage

    login(client, dokumente, "org_admin")
    kwargs = {"art": "einsatz", "einsatz_id": einsatz["incident"]}
    with TestingSession() as db:
        set_tenant_context(db, None)
        vorher = reconcile_storage(db, dokumente["org_id"])
        db.commit()
    ident = uebernehmen(client, dokumente, einsatz, **kwargs).json()["ids"][0]
    with TestingSession() as db:
        set_tenant_context(db, None)
        fassung = db.get(EinsatzDokumentFassung, db.get(EinsatzDokument, ident).fassung_id)
        erwartung = vorher + len(fassung.inhalt)
        assert get_org_storage_info(db, dokumente["org_id"])["used_bytes"] == erwartung
        assert reconcile_storage(db, dokumente["org_id"]) == erwartung
        db.commit()
    assert uebernehmen(client, dokumente, einsatz, **kwargs).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert get_org_storage_info(db, dokumente["org_id"])["used_bytes"] == erwartung
        endgueltig_entfernen(db, einsatz["incident"])
        db.commit()
        assert get_org_storage_info(db, dokumente["org_id"])["used_bytes"] == vorher


def test_backup_enthaelt_fassung_und_partielle_sicherung_nur_passende_ziele(client, dokumente, einsatz, tmp_path):
    import zipfile

    from app.services.org_export_service import collect_ids, export_org

    login(client, dokumente, "org_admin")
    lage_doc = uebernehmen(client, dokumente, einsatz).json()["ids"][0]
    incident_doc = uebernehmen(client, dokumente, einsatz,
                              art="einsatz", einsatz_id=einsatz["incident"]).json()["ids"][0]
    original = lesen(client, incident_doc)[0].content
    with TestingSession() as db:
        set_tenant_context(db, None)
        gsl = collect_ids(db, dokumente["org_id"], {"gsl"})
        normal = collect_ids(db, dokumente["org_id"], {"einsaetze"})
        keine = collect_ids(db, dokumente["org_id"], {"fahrtenbuch"})
        assert gsl["einsatz_dokument"] == {lage_doc}
        assert normal["einsatz_dokument"] == {incident_doc}
        assert len(gsl["einsatz_dokument_fassung"]) == 1
        assert keine["einsatz_dokument_fassung"] == set()
        archive = export_org(db, dokumente["org_id"], tmp_path, include_media=False)
    with zipfile.ZipFile(archive) as zf:
        import base64

        blob = json.loads(zf.read("data/einsatz_dokument_fassung.jsonl").strip())
        assert base64.b64decode(blob["inhalt"]["__bytes_b64__"]) == original
        docs = [json.loads(line) for line in zf.read("data/einsatz_dokument.jsonl").splitlines() if line]
        assert {d["id"] for d in docs} == {lage_doc, incident_doc}


def test_restore_der_fassungen_und_empfaenger(client, dokumente, einsatz, tmp_path, monkeypatch):
    from app.services import org_import_service
    from app.services.einsatz_dokument_service import pdf_daten, quell_link
    from app.services.org_export_service import export_org

    login(client, dokumente, "org_admin")
    ident = uebernehmen(client, dokumente, einsatz, einheit_ids=[einsatz["einheiten"][0]]).json()["ids"][0]
    original = lesen(client, ident)[0].content
    # Der Backupdienst selbst wird in seiner Suite geprüft; hier zählt der Restoreinhalt.
    monkeypatch.setattr(org_import_service, "_safety_backup", lambda *args: None)
    with TestingSession() as db:
        set_tenant_context(db, None)
        archive = export_org(db, dokumente["org_id"], tmp_path, include_media=False)
        org_import_service.import_org(db, archive, dokumente["org_id"], replace=True)
        obj = db.query(EinsatzDokument).filter_by(org_id=dokumente["org_id"]).one()
        assert pdf_daten(db, obj) == original
        assert len(obj.empfaenger) == 1
        einheit = db.get(LageEinheit, obj.empfaenger[0].einheit_id)
        assert einheit.lage_id == obj.lage_id
        user = db.query(User).filter_by(username=dokumente["users"]["org_admin"]).one()
        plan = db.query(KatplanPlan).filter_by(org_id=dokumente["org_id"]).one()
        assert f"version_id={plan.aktuelle_version_id}" in quell_link(db, user, obj)
        from app.models.master import OrgStorageUsage

        assert db.get(OrgStorageUsage, dokumente["org_id"]) is not None


def test_freigegebenes_planpdf_und_unveraenderliche_provenienz(client, dokumente, einsatz):
    login(client, dokumente, "org_admin")
    result = uebernehmen(client, dokumente, einsatz, documents=[
        {"url": f'/plaene/{dokumente["plan_id"]}/pdf?version_id={dokumente["version_id"]}', "pages": "1"}])
    assert result.status_code == 200, result.text
    ident = result.json()["ids"][0]
    assert len(lesen(client, ident)[1].pages) == 1
    with TestingSession() as db:
        set_tenant_context(db, None)
        obj = db.get(EinsatzDokument, ident)
        assert obj.vertraulich  # Administrator-PDF enthält auch vertrauliche Anhänge.
        obj.seiten = "2"
        with pytest.raises(RuntimeError, match="Provenienz"):
            db.flush()
        db.rollback()
        fassung = db.get(EinsatzDokumentFassung, db.get(EinsatzDokument, ident).fassung_id)
        fassung.inhalt = b"geaendert"
        with pytest.raises(RuntimeError, match="unveränderlich"):
            db.flush()


@pytest.mark.parametrize("art,key,prefix", [("einsatz", "incident", "/einsatz"), ("lage", "lage", "/lage")])
def test_board_dokumente_nur_im_menue_und_lazy_dialog(client, dokumente, einsatz, art, key, prefix):
    import re
    from html import unescape

    login(client, dokumente, "org_admin")
    ident = einsatz[key]
    response = client.get(f"{prefix}/{ident}")
    assert response.status_code == 200, response.text[:500]
    html = response.text
    assert not re.search(r"<summary[^>]*>\s*Dokumente\s*</summary>", html)
    menu = re.search(
        r'<div[^>]*role="menu"[^>]*>.*?(<button[^>]*data-ec-dialog="einsatzDokumenteDialog".*?</button>)',
        html, re.S,
    ).group(1)
    assert 'data-ec-dialog="einsatzDokumenteDialog"' in menu
    assert 'role="menuitem"' in menu and '<svg' in menu and ' Dokumente' in menu
    dialog = re.search(r'<dialog[^>]*id="einsatzDokumenteDialog"[^>]*>(.*?)</dialog>', html, re.S).group(0)
    assert 'aria-labelledby="einsatzDokumenteTitel"' in dialog
    assert 'data-ec-action="close-dialog"' in dialog
    assert f'hx-get="/einsatz-dokumente/liste/{art}/{ident}"' in dialog
    trigger = unescape(re.search(r'hx-trigger="([^"]+)"', dialog).group(1))
    assert "load" not in trigger
    assert "einsatz-dokumente-oeffnen from:body" in trigger
    assert "dokumente-aktualisiert" in trigger
    assert f'href="/einsatz-dokumente/{art}/{ident}"' in dialog


@pytest.mark.parametrize("art,key,model", [("einsatz", "incident", Incident), ("lage", "lage", MajorIncident)])
def test_dokumentfenster_geschlossener_einsaetze_ohne_schreibaktionen(client, dokumente, einsatz, art, key, model):
    login(client, dokumente, "org_admin")
    ident = uebernehmen(client, dokumente, einsatz, art=art, einsatz_id=einsatz[key]).json()["ids"][0]
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.get(model, einsatz[key]).status = "closed"
        db.commit()
    response = client.get(f'/einsatz-dokumente/liste/{art}/{einsatz[key]}')
    assert response.status_code == 200
    assert "Verknüpfung öffnen" in response.text
    assert f'/einsatz-dokumente/{ident}/zuordnung' not in response.text
    assert f'/einsatz-dokumente/{ident}/entfernen' not in response.text
    assert form(client, f'/einsatz-dokumente/{ident}/entfernen', revision=1).status_code in (403, 409)
