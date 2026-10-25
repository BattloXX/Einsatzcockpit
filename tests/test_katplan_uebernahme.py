"""Uebernahme: Rechte, Snapshot-Herkunft und unabhaengige Einsatzmedien."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.models.incident import Incident, IncidentColumn, IncidentOrg, Message, MessageMedia, Task, TaskMedia
from app.models.katplan import KatplanDokument, KatplanDokumentSeite, KatplanElement
from app.models.master import FireDept, OrgStorageUsage
from app.models.user import AuditLog
from app.services import katplan_anhang_service as anhang_service
from app.services import katplan_freigabe_service as freigabe
from app.services import katplan_uebernahme_service as service
from app.services import media_service
from tests import test_katplan_k1a as basis_fixtures
from tests import test_katplan_k6a as medien_fixtures
from tests.test_katplan_k3a import arbeitsstand
from tests.test_katplan_k6a import docx, pdf, png

db = basis_fixtures.db
org = basis_fixtures.org
storage = medien_fixtures.storage


def benutzer(org, rolle="admin"):
    return SimpleNamespace(org_id=org.id, id=None, display_name="Anna", username="anna",
                           roles=[SimpleNamespace(code=rolle)])


@pytest.fixture
def basis(db, org, storage):
    plan, element, _ = arbeitsstand(db, org)
    element.kennung = "M-1"
    element.beschreibung_html = "<p>Sperre &amp; Umleitung</p>"
    element.daten_json = json.dumps({"schritte": [{"text": "Absperren", "erledigt_hinweis": "Prüfen"}],
                                    "bedarf": [{"bezeichnung": "Sandsäcke", "menge": 20, "einheit": "Stk"}]})
    user = benutzer(org)
    incident = Incident(primary_org_id=org.id)
    db.add(incident)
    db.flush()
    incident.columns = [IncidentColumn(code=code, title=code, column_kind=code)
                        for code in ("tasks", "messages")]
    db.flush()
    return plan, element, user, incident


def uebernehmen(db, basis, **kwargs):
    plan, element, user, incident = basis
    v = service.vorschlag(db, user, plan, element, kwargs.get("leseversion"))
    daten = dict(art="auftrag", titel=v["titel"], text=v["text"], anhang_uuids=[], quellseiten_nrs=[])
    daten.update(kwargs)
    return service.uebernehmen(db, user, plan, element, incident.id, **daten)


def foto(db, basis, *, vertraulich=False, farbe="red"):
    plan, element, user, _ = basis
    return anhang_service.anhang_hochladen(db, plan, user, png(farbe), "sperre.png", "image/png",
                                          element=element, vertraulich=vertraulich)


@pytest.mark.parametrize("art,modell", [("auftrag", Task), ("meldung", Message)])
def test_anlegen_text_quelle_reihenfolge_audit(db, org, basis, art, modell):
    plan, element, user, incident = basis
    link = anhang_service.link_hinzufuegen(db, plan, user, "https://example.org/plan", "Pegeldaten", element=element)
    ergebnis = uebernehmen(db, basis, art=art, anhang_uuids=[link.uuid], meldung_status="achtung")
    karte = db.get(modell, ergebnis.uid)
    assert karte.title == "M-1 Sperren"
    assert "Sperre & Umleitung" in karte.detail and "<p>" not in karte.detail
    assert "Absperren Prüfen" in karte.detail and "Sandsäcke 20 Stk" in karte.detail
    assert f"Quelle: Hochwasser, M-1, Stand v{plan.version}" in karte.detail
    assert "https://example.org/plan" in karte.detail
    assert ergebnis.incident_id == incident.id and ergebnis.medien_anzahl == 0 and not ergebnis.fehler
    if art == "auftrag":
        assert karte.source == "katplan" and ergebnis.task_id == karte.id
    else:
        assert karte.author_name == "Anna" and karte.status == "achtung"
        assert ergebnis.message_id == karte.id and ergebnis.message is karte
    spalte = db.get(IncidentColumn, ergebnis.column_id)
    assert json.loads(spalte.card_order)[0] == {"kind": "task" if art == "auftrag" else "message", "id": karte.id}
    audit = db.query(AuditLog).filter_by(action="einsatz.katplan_uebernommen", incident_id=incident.id).one()
    assert audit.org_id == org.id and audit.entity_id == karte.id
    assert audit.entity_type == ("task" if art == "auftrag" else "message")
    assert json.loads(audit.payload_json) == {"plan_id": plan.id, "element_uuid": element.uuid, "version": plan.version}


@pytest.mark.parametrize("art,modell", [("auftrag", TaskMedia), ("meldung", MessageMedia)])
def test_dateikopie_bleibt_nach_loeschen(db, basis, storage, art, modell):
    a = foto(db, basis)
    quellpfad = a.pfad
    ergebnis = uebernehmen(db, basis, art=art, anhang_uuids=[a.uuid, a.uuid])
    medium = db.query(modell).filter_by(incident_id=basis[3].id).one()
    assert ergebnis.medien_anzahl == 1 and not ergebnis.fehler
    assert medium.storage_path != quellpfad
    ziel = storage / medium.storage_path
    assert ziel.read_bytes() == (storage / quellpfad).read_bytes()
    anhang_service.anhang_entfernen(db, basis[0], basis[2], a)
    db.commit()
    assert not (storage / quellpfad).exists() and ziel.is_file()


@pytest.mark.parametrize("art", ["auftrag", "meldung"])
@pytest.mark.parametrize("bild", [True, False])
def test_quellseite_als_medium(db, org, basis, storage, art, bild):
    plan, element, _, incident = basis
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "seite.png").write_bytes(png())
    (storage / "seite.pdf").write_bytes(pdf())
    dokument = KatplanDokument(org_id=org.id, plan_id=plan.id, titel="Quellplan", dateiname_original="plan.pdf",
                              pfad="original.pdf", groesse_bytes=100, seitenzahl=2)
    db.add(dokument)
    db.flush()
    db.add(KatplanDokumentSeite(org_id=org.id, plan_id=plan.id, dokument_id=dokument.id,
                                seiten_nr=2, bild_pfad="seite.png" if bild else None, einzel_pdf_pfad="seite.pdf"))
    element.quelle_dokument_id, element.quelle_seiten = dokument.id, "2"
    db.flush()
    v = service.vorschlag(db, basis[2], plan, element)
    assert v["quellseiten"] == [{"dokument_uuid": dokument.uuid, "nr": 2, "name": "Quellplan, S. 2"}]
    ergebnis = uebernehmen(db, basis, art=art, quellseiten_nrs=[2])
    modell = TaskMedia if art == "auftrag" else MessageMedia
    medium = db.query(modell).filter_by(incident_id=incident.id).one()
    assert ergebnis.medien_anzahl == 1 and not ergebnis.fehler
    assert medium.kind == ("image" if bild else "pdf")
    assert (storage / medium.storage_path).is_file()
    assert medium.storage_path not in ("seite.png", "seite.pdf")


@pytest.mark.parametrize("fall,status", [("cross_org", 404), ("geschlossen", 409), ("readonly", 403)])
def test_einsatzrechte(db, org, basis, fall, status):
    plan, element, user, incident = basis
    if fall == "cross_org":
        andere = FireDept(slug=uuid4().hex, name="Fremd")
        db.add(andere)
        db.flush()
        incident.primary_org_id = andere.id
    elif fall == "geschlossen":
        incident.status = "closed"
    else:
        user = benutzer(org, "readonly")
    db.flush()
    with pytest.raises(HTTPException) as exc:
        service.uebernehmen(db, user, plan, element, incident.id, art="auftrag", titel="Titel", text="",
                            anhang_uuids=[], quellseiten_nrs=[])
    assert exc.value.status_code == status
    assert db.query(Task).filter_by(incident_id=incident.id).count() == 0


def test_vertraulicher_anhang_snapshot_und_fremde_auswahl(db, org, basis):
    plan, element, _, incident = basis
    a = foto(db, basis, vertraulich=True)
    sichtbar = foto(db, basis)
    version = freigabe.freigeben(db, plan, basis[2])
    db.flush()
    snapshot = freigabe.lade_version(db, plan)[2][0]
    user = benutzer(org, "recorder")
    v = service.vorschlag(db, user, plan, snapshot, version)
    assert [a["uuid"] for a in v["anhaenge"]] == [sichtbar.uuid]
    for auswahl in ({"anhang_uuids": [a.uuid]}, {"anhang_uuids": [str(uuid4())]}, {"quellseiten_nrs": [9]}):
        with pytest.raises(HTTPException) as exc:
            service.uebernehmen(db, user, plan, snapshot, incident.id, art="auftrag", titel="Titel", text="",
                                **({"anhang_uuids": [], "quellseiten_nrs": []} | auswahl), leseversion=version)
        assert exc.value.status_code == 403


def test_snapshot_unveraendert_soft_delete_und_elementbindung(db, org, basis, storage):
    plan, element, admin, incident = basis
    a = foto(db, basis)
    fremd = KatplanElement(org_id=org.id, plan_id=plan.id, titel="Andere", typ="info")
    db.add(fremd)
    db.flush()
    anderer_anhang = anhang_service.anhang_hochladen(db, plan, admin, png(), "anders.png", "image/png", element=fremd)
    version = freigabe.freigeben(db, plan, admin)
    db.flush()
    snapshot = next(e for e in freigabe.lade_version(db, plan)[2] if e.element_uuid == element.uuid)
    anhang_service.anhang_entfernen(db, plan, admin, a)
    plan.titel, element.titel, element.beschreibung_html = "Neu", "Neu", "Neue Beschreibung"
    user = benutzer(org, "recorder")
    v = service.vorschlag(db, user, plan, snapshot, version)
    assert v["titel"] == "M-1 Sperren" and "Quelle: Hochwasser, M-1, Stand v1" in v["text"]
    assert [a["uuid"] for a in v["anhaenge"]] == [a.uuid]
    with pytest.raises(HTTPException) as exc:
        service.uebernehmen(db, user, plan, snapshot, incident.id, art="meldung", titel="T", text="",
                            anhang_uuids=[anderer_anhang.uuid], quellseiten_nrs=[], leseversion=version)
    assert exc.value.status_code == 403
    ergebnis = uebernehmen(db, (plan, snapshot, user, incident), art="meldung",
                           anhang_uuids=[a.uuid], leseversion=version)
    assert ergebnis.medien_anzahl == 1 and not ergebnis.fehler


@pytest.mark.parametrize("art,modell", [("auftrag", TaskMedia), ("meldung", MessageMedia)])
def test_teilfehler_rest_gespeichert(db, basis, storage, art, modell):
    kaputt = foto(db, basis)
    gut = foto(db, basis, farbe="blue")
    (storage / kaputt.pfad).unlink()
    ergebnis = uebernehmen(db, basis, art=art, anhang_uuids=[kaputt.uuid, gut.uuid])
    assert ergebnis.medien_anzahl == 1 and len(ergebnis.fehler) == 1
    assert "sperre.png" in ergebnis.fehler[0]
    assert db.query(modell).filter_by(incident_id=basis[3].id).count() == 1


@pytest.mark.parametrize("art", ["auftrag", "meldung"])
def test_quota_fehler_keine_zieldateien(db, org, basis, storage, art):
    a = foto(db, basis)
    db.flush()
    usage = db.get(OrgStorageUsage, org.id)
    vorher = usage.used_bytes
    org.storage_quota_bytes = vorher
    db.flush()
    ergebnis = uebernehmen(db, basis, art=art, anhang_uuids=[a.uuid])
    assert ergebnis.medien_anzahl == 0 and len(ergebnis.fehler) == 1
    assert "Quota" in ergebnis.fehler[0]
    assert list((storage / str(org.id) / str(basis[3].id)).rglob("*.*")) == []
    db.refresh(usage)
    assert usage.used_bytes == vorher


def test_kandidaten_sichtbarkeit_sortierung_limit(db, org, basis):
    fremd = FireDept(slug=uuid4().hex, name="Fremd")
    db.add(fremd)
    db.flush()
    start = datetime(2030, 1, 1, tzinfo=UTC)
    einsaetze = [Incident(primary_org_id=org.id, started_at=start + timedelta(minutes=i)) for i in range(55)]
    geschlossen = Incident(primary_org_id=org.id, status="closed", started_at=start)
    unsichtbar = Incident(primary_org_id=fremd.id, started_at=start)
    kollab = Incident(primary_org_id=fremd.id, started_at=start + timedelta(days=1))
    db.add_all([*einsaetze, geschlossen, unsichtbar, kollab])
    db.flush()
    db.add(IncidentOrg(incident_id=kollab.id, org_id=org.id))
    db.flush()
    kandidaten = service.kandidaten_einsaetze(db, benutzer(org, "recorder"))
    assert len(kandidaten) == 50
    assert [e.id for e in kandidaten] == [kollab.id] + [e.id for e in reversed(einsaetze[-49:])]
    assert service.kandidaten_einsaetze(db, benutzer(org, "readonly")) == []


@pytest.mark.parametrize("titel", ["", "  ", "x" * 501])
def test_titel_validierung(db, basis, titel):
    with pytest.raises(HTTPException) as exc:
        uebernehmen(db, basis, titel=titel)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("art", ["auftrag", "meldung"])
def test_broadcast_nach_commit_auch_ohne_session(db, basis, monkeypatch, art):
    from app.services.broadcast import manager

    a = foto(db, basis)
    senden = AsyncMock()
    monkeypatch.setattr(manager, "broadcast", senden)
    ergebnis = uebernehmen(db, basis, art=art, anhang_uuids=[a.uuid])
    senden.assert_not_awaited()
    db.expunge_all()
    asyncio.run(service.broadcast_uebernahme(ergebnis))
    payloads = [call.args[1] for call in senden.await_args_list]
    erwartet = ["task_created", "task_updated"] if art == "auftrag" else ["message_created"]
    assert [p["type"] for p in payloads] == erwartet
    assert all(call.args[0] == ergebnis.incident_id for call in senden.await_args_list)
    if art == "auftrag":
        assert payloads[1]["uid"] == ergebnis.task_id and payloads[1]["column_id"] == ergebnis.column_id


def test_store_upload_nutzt_bytes_pipeline(db, basis):
    import io

    task = service.incident_service.add_task(db, basis[3], "Upload")
    datei = UploadFile(io.BytesIO(png()), filename="foto.png")
    result = asyncio.run(media_service.store_upload(datei, task, basis[2], db, basis[2].org_id))
    assert isinstance(result.media, TaskMedia) and result.media.kind == "image" and result.warnings == []


@pytest.mark.parametrize("art,modell", [("auftrag", TaskMedia), ("meldung", MessageMedia)])
def test_buerodatei_kopieren(db, basis, storage, art, modell):
    plan, element, user, incident = basis
    raw = docx()
    a = anhang_service.anhang_hochladen(
        db, plan, user, raw, "checkliste.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", element=element,
    )
    ergebnis = uebernehmen(db, basis, art=art, anhang_uuids=[a.uuid])
    medium = db.query(modell).filter_by(incident_id=incident.id).one()
    assert ergebnis.medien_anzahl == 1 and not ergebnis.fehler
    assert medium.kind == "datei" and medium.original_filename == "checkliste.docx"
    assert medium.storage_path != a.pfad and (storage / medium.storage_path).read_bytes() == raw


def test_vertrauliches_element_und_versionsbindung(db, org, basis):
    plan, element, admin, incident = basis
    element.vertraulich = True
    version = freigabe.freigeben(db, plan, admin)
    db.flush()
    snapshot = freigabe.lade_version(db, plan)[2][0]
    recorder = benutzer(org, "recorder")
    with pytest.raises(HTTPException) as exc:
        service.vorschlag(db, recorder, plan, snapshot, version)
    assert exc.value.status_code == 403
    assert service.vorschlag(db, benutzer(org, "incident_leader"), plan, snapshot)["titel"] == "M-1 Sperren"
    with pytest.raises(HTTPException) as exc:
        service.vorschlag(db, admin, plan, element, version)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        service.vorschlag(db, recorder, plan, element)
    assert exc.value.status_code == 403
    andere_org = FireDept(slug=uuid4().hex, name="Andere")
    db.add(andere_org)
    db.flush()
    with pytest.raises(HTTPException) as exc:
        service.vorschlag(db, benutzer(andere_org), plan, snapshot, version)
    assert exc.value.status_code == 404


def test_unlesbare_daten_und_titelbegrenzung(db, basis):
    plan, element, user, _ = basis
    element.titel = "x" * 500
    for roh in ("{kaputt", '["nicht als Objekt lesbar"]', '{"schritte": [null, {"unbekannt": "x"}]}'):
        element.daten_json = roh
        v = service.vorschlag(db, user, plan, element)
        assert len(v["titel"]) == 500 and "Quelle: Hochwasser, M-1" in v["text"]
