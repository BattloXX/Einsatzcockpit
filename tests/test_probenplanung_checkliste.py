"""Phase-4-Tests: Probe-CRUD, Snapshot, Fortschritt und Status."""

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.probenplanung import (
    ChecklistTemplate,
    ChecklistTemplateSection,
    ChecklistTemplateVersion,
    Probeart,
    ProbeChange,
    ProbeCheckliste,
    ProbeChecklistItem,
)
from app.models.teilnahme import Termin
from app.models.user import Role, User, UserRole
from app.services.probe_checklist_service import darf_vorbereitung_abschliessen, fortschritt

ORG_ID = 1
VORLAGEN = "/admin/probenplanung/vorlagen"


def _user(username: str, role_code: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            org_id=ORG_ID,
            active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == role_code).one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()


def _flags() -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        system = db.query(SystemSettings).filter(SystemSettings.key == "probenplanung_module_enabled").first()
        if system is None:
            db.add(SystemSettings(key="probenplanung_module_enabled", value="true"))
        else:
            system.value = "true"
        settings = db.query(OrgSettings).filter(OrgSettings.org_id == ORG_ID).first()
        if settings is None:
            settings = OrgSettings(org_id=ORG_ID)
            db.add(settings)
        settings.probenplanung_modul_aktiv = True
        db.commit()
    finally:
        db.close()


def _login(client, username: str) -> str:
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    response = client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf})
    assert response.status_code == 200
    return client.cookies.get("ec_csrf")


def _vorlage_mit_punkt(client, csrf: str, name: str, *, faellig_tage_vorher: int | None = None) -> tuple[int, int, int]:
    assert client.post(VORLAGEN, data={"_csrf": csrf, "name": name}, follow_redirects=False).status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        template = db.query(ChecklistTemplate).filter(ChecklistTemplate.name == name).one()
        version = db.query(ChecklistTemplateVersion).filter_by(template_id=template.id).one()
        template_id, version_id = template.id, version.id
    finally:
        db.close()
    response = client.post(
        f"{VORLAGEN}/{template_id}/version/{version_id}/bereiche",
        data={"_csrf": csrf, "titel": "Vorbereitung"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        section_id = db.query(ChecklistTemplateSection).filter_by(version_id=version_id).one().id
    finally:
        db.close()
    item_data = {"_csrf": csrf, "titel": "Pflicht V1", "typ": "checkbox", "pflicht": "1"}
    if faellig_tage_vorher is not None:
        item_data["faellig_tage_vorher"] = str(faellig_tage_vorher)
    assert (
        client.post(
            f"{VORLAGEN}/{template_id}/version/{version_id}/bereiche/{section_id}/punkte",
            data=item_data,
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        client.post(
            f"{VORLAGEN}/{template_id}/version/{version_id}/veroeffentlichen",
            data={"_csrf": csrf},
            follow_redirects=False,
        ).status_code
        == 303
    )
    return template_id, version_id, section_id


def _probeart(template_id: int, name: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        probeart = Probeart(
            org_id=ORG_ID,
            name=name,
            kurz=name[:10],
            farbe="#123456",
            termin_typ="uebung",
            checklist_template_id=template_id,
            checkliste_erforderlich=True,
        )
        db.add(probeart)
        db.commit()
        return probeart.id
    finally:
        db.close()


def _probe_anlegen(
    client,
    csrf: str,
    probeart_id: int,
    titel: str,
    beginn: str = "2026-09-04T19:30",
    *,
    ganztaegig: bool = False,
) -> int:
    data = {"_csrf": csrf, "probeart_id": probeart_id, "titel": titel, "beginn": beginn}
    if ganztaegig:
        data["ganztaegig"] = "1"
    response = client.post(
        "/probenplanung/neu",
        data=data,
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


def test_snapshot_bleibt_bei_neuer_vorlagenversion_unveraendert(client):
    _user("phase4_snapshot_admin", "org_admin")
    _flags()
    csrf = _login(client, "phase4_snapshot_admin")
    template_id, _, _ = _vorlage_mit_punkt(client, csrf, "Phase 4 Snapshot")
    probeart_id = _probeart(template_id, "Phase4 Vollprobe")
    probe_a = _probe_anlegen(client, csrf, probeart_id, "Probe A")

    assert (
        client.post(f"{VORLAGEN}/{template_id}/version/neu", data={"_csrf": csrf}, follow_redirects=False).status_code
        == 303
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        version_2 = db.query(ChecklistTemplateVersion).filter_by(template_id=template_id, version=2).one()
        section_2 = db.query(ChecklistTemplateSection).filter_by(version_id=version_2.id).one()
        version_2_id, section_2_id = version_2.id, section_2.id
    finally:
        db.close()
    assert (
        client.post(
            f"{VORLAGEN}/{template_id}/version/{version_2_id}/bereiche/{section_2_id}/punkte",
            data={"_csrf": csrf, "titel": "Erweiterung V2", "typ": "checkbox"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        client.post(
            f"{VORLAGEN}/{template_id}/version/{version_2_id}/veroeffentlichen",
            data={"_csrf": csrf},
            follow_redirects=False,
        ).status_code
        == 303
    )
    probe_b = _probe_anlegen(client, csrf, probeart_id, "Probe B")

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        liste_a = db.query(ProbeCheckliste).filter_by(termin_id=probe_a).one()
        liste_b = db.query(ProbeCheckliste).filter_by(termin_id=probe_b).one()
        assert db.query(ProbeChecklistItem).filter_by(checkliste_id=liste_a.id).count() == 1
        assert db.query(ProbeChecklistItem).filter_by(checkliste_id=liste_b.id).count() == 2
        assert liste_a.template_version == 1
        assert liste_b.template_version == 2
    finally:
        db.close()


def test_fortschritt_nicht_relevant_individuell_und_pflichtlogik(client):
    _user("phase4_progress", "probenverwalter")
    _flags()
    csrf = _login(client, "phase4_progress")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        template = ChecklistTemplate(org_id=ORG_ID, name="Phase 4 Progress")
        db.add(template)
        db.flush()
        probeart = Probeart(
            org_id=ORG_ID, name="Phase4 Progress Art", kurz="P4P", farbe="#654321", checklist_template_id=template.id
        )
        db.add(probeart)
        db.commit()
        probeart_id = probeart.id
    finally:
        db.close()
    termin_id = _probe_anlegen(client, csrf, probeart_id, "Fortschritt")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        checkliste = ProbeCheckliste(org_id=ORG_ID, termin_id=termin_id, template_name="Ad hoc", template_version=1)
        db.add(checkliste)
        db.flush()
        db.add_all(
            [
                ProbeChecklistItem(
                    org_id=ORG_ID,
                    checkliste_id=checkliste.id,
                    quelle="vorlage",
                    titel="Pflicht offen",
                    typ="checkbox",
                    pflicht=True,
                    zustand="offen",
                    faellig_am=date(2020, 1, 1),
                ),
                ProbeChecklistItem(
                    org_id=ORG_ID,
                    checkliste_id=checkliste.id,
                    quelle="vorlage",
                    titel="Ausgeschlossen",
                    typ="checkbox",
                    pflicht=True,
                    zustand="nicht_relevant",
                ),
                ProbeChecklistItem(
                    org_id=ORG_ID,
                    checkliste_id=checkliste.id,
                    quelle="individuell",
                    titel="Ad hoc erledigt",
                    typ="checkbox",
                    pflicht=False,
                    zustand="erledigt",
                ),
            ]
        )
        db.commit()
        db.refresh(checkliste)
        result = fortschritt(checkliste, db.get(FireDept, ORG_ID), heute=date(2026, 1, 1))
        assert (result.gesamt, result.erledigt, result.prozent) == (2, 1, 50)
        assert (result.pflicht_gesamt, result.pflicht_erledigt, result.offene_pflichtpunkte) == (1, 0, 1)
        assert (result.optionale_gesamt, result.optionale_erledigt, result.ueberfaellig) == (1, 1, 1)
        assert darf_vorbereitung_abschliessen(checkliste, db.get(FireDept, ORG_ID)) is False
        checkliste.items[0].zustand = "erledigt"
        assert darf_vorbereitung_abschliessen(checkliste, db.get(FireDept, ORG_ID)) is True
    finally:
        db.close()


def test_statusmatrix_uebersteuerung_und_protokollierung(client):
    _user("phase4_override_admin", "org_admin")
    _flags()
    csrf = _login(client, "phase4_override_admin")
    template_id, _, _ = _vorlage_mit_punkt(client, csrf, "Phase 4 Override")
    termin_id = _probe_anlegen(client, csrf, _probeart(template_id, "Phase4 Override Art"), "Override")
    assert (
        client.post(
            f"/probenplanung/{termin_id}/status",
            data={"_csrf": csrf, "neuer_status": "geplant"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        client.post(
            f"/probenplanung/{termin_id}/status",
            data={"_csrf": csrf, "neuer_status": "in_vorbereitung"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    blocked = client.post(
        f"/probenplanung/{termin_id}/status",
        data={"_csrf": csrf, "neuer_status": "vorbereitung_abgeschlossen"},
        follow_redirects=False,
    )
    assert blocked.status_code == 409
    forbidden = client.post(
        f"/probenplanung/{termin_id}/status",
        data={"_csrf": csrf, "neuer_status": "abgeschlossen"},
        follow_redirects=False,
    )
    assert forbidden.status_code == 409
    with patch("app.services.probe_checklist_service.write_audit") as audit:
        response = client.post(
            f"/probenplanung/{termin_id}/status",
            data={
                "_csrf": csrf,
                "neuer_status": "vorbereitung_abgeschlossen",
                "grund": "Einsatzleitung bestätigt Ausnahme",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        audit.assert_called_once()
        assert audit.call_args.args[1] == "probenplanung.vorbereitung.uebersteuert"
        assert audit.call_args.kwargs["payload"]["grund"] == "Einsatzleitung bestätigt Ausnahme"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        termin = db.get(Termin, termin_id)
        change = db.query(ProbeChange).filter_by(termin_id=termin_id, action="vorbereitung.uebersteuert").one()
        assert termin.status == "vorbereitung_abgeschlossen"
        assert termin.vorbereitung_uebersteuert_grund == "Einsatzleitung bestätigt Ausnahme"
        assert "Einsatzleitung bestätigt Ausnahme" in change.after_json
    finally:
        db.close()


def test_zeitzonen_rundlauf_und_lese_schreibrechte(client):
    _user("phase4_tz_editor", "probenverwalter")
    _user("phase4_tz_reader", "readonly")
    _flags()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(FireDept, ORG_ID).timezone = "Europe/Vienna"
        probeart = Probeart(org_id=ORG_ID, name="Phase4 TZ Art", kurz="TZ", farbe="#112233")
        db.add(probeart)
        db.commit()
        probeart_id = probeart.id
    finally:
        db.close()
    csrf = _login(client, "phase4_tz_editor")
    termin_id = _probe_anlegen(client, csrf, probeart_id, "Zeitzonenprobe")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Termin, termin_id).beginn.isoformat(timespec="minutes") == "2026-09-04T17:30"
    finally:
        db.close()
    detail = client.get(f"/probenplanung/{termin_id}")
    assert "04.09.2026 19:30" in detail.text
    _login(client, "phase4_tz_reader")
    assert client.get(f"/probenplanung/{termin_id}").status_code == 200
    assert client.get(f"/probenplanung/{termin_id}/bearbeiten").status_code == 403


def test_snapshot_faelligkeit_verwendet_lokalen_kalendertag_im_sommer(client):
    _user("phase4_due_date", "org_admin")
    _flags()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(FireDept, ORG_ID).timezone = "Europe/Vienna"
        db.commit()
    finally:
        db.close()
    csrf = _login(client, "phase4_due_date")
    template_id, _, _ = _vorlage_mit_punkt(client, csrf, "Phase 4 lokale Fälligkeit", faellig_tage_vorher=14)
    probeart_id = _probeart(template_id, "Phase4 lokale Fälligkeit Art")
    termin_id = _probe_anlegen(client, csrf, probeart_id, "Ganztägige Sommerprobe", "2026-07-01T00:00", ganztaegig=True)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        checkliste = db.query(ProbeCheckliste).filter_by(termin_id=termin_id).one()
        item = db.query(ProbeChecklistItem).filter_by(checkliste_id=checkliste.id).one()
        assert db.get(Termin, termin_id).beginn == datetime(2026, 6, 30, 22, 0)
        assert item.faellig_am == date(2026, 6, 17)
    finally:
        db.close()


def test_ueberfaelligkeit_verwendet_heute_der_org_im_sommer(client):
    _flags()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.get(FireDept, ORG_ID)
        org.timezone = "Europe/Vienna"
        termin = Termin(org_id=ORG_ID, typ="uebung", titel="Stichtag", beginn=datetime(2026, 7, 1, 10))
        db.add(termin)
        db.flush()
        checkliste = ProbeCheckliste(org_id=ORG_ID, termin_id=termin.id, template_name="Stichtag", template_version=1)
        db.add(checkliste)
        db.flush()
        db.add_all(
            [
                ProbeChecklistItem(
                    org_id=ORG_ID,
                    checkliste_id=checkliste.id,
                    quelle="individuell",
                    titel="Heute",
                    typ="checkbox",
                    faellig_am=date(2026, 7, 1),
                ),
                ProbeChecklistItem(
                    org_id=ORG_ID,
                    checkliste_id=checkliste.id,
                    quelle="individuell",
                    titel="Gestern",
                    typ="checkbox",
                    faellig_am=date(2026, 6, 30),
                ),
            ]
        )
        db.commit()
        db.refresh(checkliste)
        lokales_jetzt = datetime(2026, 7, 1, 0, 30, tzinfo=ZoneInfo("Europe/Vienna"))
        with patch("app.services.probe_checklist_service.now_local", return_value=lokales_jetzt):
            result = fortschritt(checkliste, org)
        assert result.ueberfaellig == 1
        heute_item = next(item for item in checkliste.items if item.titel == "Heute")
        gestern_item = next(item for item in checkliste.items if item.titel == "Gestern")
        gestern_item.zustand = "erledigt"
        with patch("app.services.probe_checklist_service.now_local", return_value=lokales_jetzt):
            assert fortschritt(checkliste, org).ueberfaellig == 0
        gestern_item.zustand = "offen"
        heute_item.zustand = "erledigt"
        with patch("app.services.probe_checklist_service.now_local", return_value=lokales_jetzt):
            assert fortschritt(checkliste, org).ueberfaellig == 1
    finally:
        db.close()
