"""Board-Erfassung von an der Einsatzstelle belassenem Material."""

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import IncidentSite
from app.models.master import OrgSettings
from app.models.verleih import VerleihAusleihe
from app.services import verleih_service as svc
from tests.test_gsl_sektoren import _login, _user_lage


def _material_lage(username="material_board", role="incident_leader"):
    lage_id, org_id = _user_lage(username, username, role)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        settings = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if settings is None:
            settings = OrgSettings(org_id=org_id)
            db.add(settings)
        settings.mi_feature_geraeteverleih = True
        site = IncidentSite(major_incident_id=lage_id, org_id=org_id, bezeichnung="Keller", strasse="Hauptstraße", hausnr="4", ort="Hard")
        db.add(site)
        db.commit()
        return lage_id, org_id, site.id
    finally:
        db.close()


def test_board_material_form_post_and_card_badge(client, setup_db):
    lage_id, org_id, site_id = _material_lage()
    _login(client, "material_board")
    form = client.get(f"/lage/{lage_id}/stellen/{site_id}/material/neu")
    assert form.status_code == 200
    assert "Hauptstraße 4, Hard" in form.text

    created = client.post(
        f"/lage/{lage_id}/stellen/{site_id}/material",
        data={"_csrf": client.cookies.get("ec_csrf"), "pos_bezeichnung[]": "Tauchpumpe", "pos_menge[]": "2"},
        headers={"HX-Request": "true"},
    )
    assert created.status_code == 200
    assert "Material vor Ort" in created.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        ausleihe = db.query(VerleihAusleihe).filter_by(site_id=site_id).one()
        assert ausleihe.quelle == "board"
    finally:
        db.close()
    assert "📦 1" in client.get(f"/lage/{lage_id}/stellen/{site_id}/card").text


def test_board_material_readonly_cannot_post(client, setup_db):
    lage_id, _, site_id = _material_lage("material_readonly", "readonly")
    _login(client, "material_readonly")
    response = client.post(f"/lage/{lage_id}/stellen/{site_id}/material", data={"pos_bezeichnung[]": "Pumpe"})
    assert response.status_code == 403


def _png_bytes():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    return buf.getvalue()


def test_board_material_vorbelegung_sms_foto_und_chronik(client, setup_db, monkeypatch):
    from app.models.major_incident import CitizenReport, SiteLogEntry

    lage_id, org_id, site_id = _material_lage("material_board_sms")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(CitizenReport(
            major_incident_id=lage_id, site_id=site_id, reporter_name="Erika Muster",
            reporter_contact="+43 664 1234567", description="Keller voll",
        ))
        db.commit()
    finally:
        db.close()
    gesendet = []

    async def fake_send_sms(org, nummer, text):
        gesendet.append((org, nummer, text))
        return True

    monkeypatch.setattr("app.services.sms_service.send_sms", fake_send_sms)
    _login(client, "material_board_sms")
    form = client.get(f"/lage/{lage_id}/stellen/{site_id}/material/neu")
    assert "Erika Muster" in form.text
    assert "+43 664 1234567" in form.text

    created = client.post(
        f"/lage/{lage_id}/stellen/{site_id}/material",
        data={
            "_csrf": client.cookies.get("ec_csrf"), "pos_bezeichnung[]": "Sandsack", "pos_menge[]": "20",
            "name": "Erika Muster", "telefon": "+43 664 1234567", "sms_senden": "1",
        },
        files={"fotos": ("pumpe.png", _png_bytes(), "image/png")},
        headers={"HX-Request": "true"},
    )
    assert created.status_code == 200
    assert "siteMaterialChanged" in created.headers["HX-Trigger"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        ausleihe = db.query(VerleihAusleihe).filter_by(site_id=site_id).one()
        assert len(ausleihe.fotos) == 1
        assert ausleihe.sms_ausleih_gesendet is True
        texte = [e.text for e in db.query(SiteLogEntry).filter_by(incident_site_id=site_id).all()]
        assert any(t.startswith("Material belassen: 20× Sandsack") for t in texte)
        assert "SMS an Empfänger gesendet" in texte
    finally:
        db.close()
    assert gesendet and gesendet[0][1] == "+43 664 1234567"


def test_board_material_feature_aus_und_fremde_lage(client, setup_db):
    lage_id, org_id, site_id = _material_lage("material_aus")
    other_lage_id, _, other_site_id = _material_lage("material_aus_fremd")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSettings).filter_by(org_id=org_id).one().mi_feature_geraeteverleih = False
        db.commit()
    finally:
        db.close()
    _login(client, "material_aus")
    assert client.get(f"/lage/{lage_id}/stellen/{site_id}/material/neu").status_code == 403
    detail = client.get(f"/lage/{lage_id}/stellen/{site_id}")
    assert detail.status_code == 200
    assert "Material vor Ort" not in detail.text
    # Stelle einer anderen Lage ist nie erreichbar
    assert client.get(f"/lage/{lage_id}/stellen/{other_site_id}/material/neu").status_code in (403, 404)


def test_board_material_zuruecknehmen(client, setup_db):
    lage_id, org_id, site_id = _material_lage("material_zurueck")
    _login(client, "material_zurueck")
    csrf = client.cookies.get("ec_csrf")
    client.post(
        f"/lage/{lage_id}/stellen/{site_id}/material",
        data={"_csrf": csrf, "pos_bezeichnung[]": "Tauchpumpe", "pos_menge[]": "1"},
        headers={"HX-Request": "true"},
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        ausleihe_id = db.query(VerleihAusleihe).filter_by(site_id=site_id).one().id
    finally:
        db.close()
    response = client.post(
        f"/lage/{lage_id}/stellen/{site_id}/material/{ausleihe_id}/zurueck",
        data={"_csrf": csrf}, headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Zurückgegeben" in response.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(VerleihAusleihe, ausleihe_id).status.value == "zurueckgegeben"
    finally:
        db.close()
    assert "📦" not in client.get(f"/lage/{lage_id}/stellen/{site_id}/card").text
