"""Tests fuer die strukturierte HTML-Einsatzinfo-Mail."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


def _data(db, suffix="html"):
    from app.models.einsatzinfo_mail import MailEinsatzinfoRecipient
    from app.models.incident import Incident
    from app.models.master import AlarmType, FireDept, OrgSettings
    from app.models.objekt import GefahrenKatalog, Objekt, ObjektEinsatz, ObjektGefahr, OBJEKT_EINSATZ_BESTAETIGT

    org = FireDept(slug=f"mail-{suffix}", name="Feuerwehr Musterort", is_home_org=False)
    db.add(org); db.flush()
    org_settings = OrgSettings(org_id=org.id, primary_color="#123456", einsatzinfo_mail_enabled=True)
    alarm = AlarmType(org_id=org.id, code="F2", category="F", label="Zimmerbrand")
    incident = Incident(
        primary_org_id=org.id, alarm_type_code="F2", address_street="Hauptstrasse",
        address_no="7", address_city="Musterort", report_text="Rauchentwicklung",
        reason="Brand in Wohnung", lat=47.5, lng=9.7, alarm_token="abc123",
        dibos_bma_no="1253", dibos_diagnose="Melder ausgelöst", lis_operation_number="F26000001",
        started_at=datetime(2026, 1, 2, 10, 30, tzinfo=UTC),
    )
    objekt = Objekt(org_id=org.id, nummer=42, name="Wohnanlage Musterstraße")
    gefahr = GefahrenKatalog(org_id=org.id, name="Gasanschluss", piktogramm_typ="gas")
    gefahr2 = GefahrenKatalog(org_id=org.id, name="Photovoltaik", piktogramm_typ="pv")
    db.add_all([org_settings, alarm, incident, objekt, gefahr, gefahr2]); db.flush()
    db.add_all([
        ObjektEinsatz(org_id=org.id, incident_id=incident.id, objekt_id=objekt.id, quelle="manuell", status=OBJEKT_EINSATZ_BESTAETIGT),
        ObjektGefahr(org_id=org.id, objekt_id=objekt.id, gefahr_id=gefahr.id),
        ObjektGefahr(org_id=org.id, objekt_id=objekt.id, gefahr_id=gefahr2.id),
        MailEinsatzinfoRecipient(org_id=org.id, email="mail-html@example.test"),
    ])
    db.commit()
    return org, org_settings, alarm, incident


def test_build_view_details_links_and_html_escaping(setup_db):
    from app.config import settings
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.services.einsatzinfo_mail_service import build_mail_view, render_einsatzinfo_mail_html

    old_base = settings.PUBLIC_BASE_URL
    settings.PUBLIC_BASE_URL = "https://mail.example.test"
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        org, org_settings, alarm, incident = _data(db, "view")
        view = build_mail_view(db, incident, org, alarm, org_settings, "Alarm", None)
        assert [label for label, _ in view["details"]] == [
            "Einsatzgrund", "Brandmeldeanlage", "Objekt", "Gefahren", "Leitstellennummer", "Einsatznummer",
        ]
        assert view["info_link"] == "https://mail.example.test/alarm/abc123"
        assert view["map_image_url"] == "https://mail.example.test/api/v1/teams/map/abc123.png"
        assert "destination=47.5,9.7" in view["navi_link"]
        view["meldung"] = "Zeile 1\n<script>alert(1)</script>"
        rendered = render_einsatzinfo_mail_html(view)
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
        assert "Zeile 1<br>" in rendered
        view["is_exercise"] = True
        assert "ÜBUNG – kein Realeinsatz" in render_einsatzinfo_mail_html(view)
    finally:
        settings.PUBLIC_BASE_URL = old_base
        db.close()


def test_hinweis_only_for_custom_template(setup_db):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.services.einsatzinfo_mail_service import build_mail_view

    db = SessionLocal(); set_tenant_context(db, None)
    try:
        org, org_settings, alarm, incident = _data(db, "hinweis")
        assert build_mail_view(db, incident, org, alarm, org_settings, "Alarm", "Eigener Hinweis")["hinweis"] is None
        org_settings.einsatzinfo_mail_template = "Hinweis {meldung}"
        assert build_mail_view(db, incident, org, alarm, org_settings, "Alarm", "Eigener Hinweis")["hinweis"] == "Eigener Hinweis"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_dispatch_html_and_render_fallback(setup_db, monkeypatch):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.services import einsatzinfo_mail_service as service

    db = SessionLocal(); set_tenant_context(db, None)
    try:
        org, _org_settings, _alarm, incident = _data(db, "dispatch")
        org_id, incident_id = org.id, incident.id
    finally:
        db.close()
    sent = []

    async def deliver(_db, _org_id, msg, smtp_cfg=None):
        sent.append(msg)

    monkeypatch.setattr("app.services.mail_service.mail_available", lambda *_args: True)
    monkeypatch.setattr("app.services.mail_service.deliver", deliver)
    await service.dispatch_einsatzinfo_mail(org_id, incident_id)
    message = sent[0]
    assert "Einsatzinformation öffnen" in message.get_body(preferencelist=("html",)).get_content()
    assert "Hauptstrasse 7" in message.get_body(preferencelist=("html",)).get_content()
    assert "Rauchentwicklung" in message.get_body(preferencelist=("plain",)).get_content()

    monkeypatch.setattr(service, "render_einsatzinfo_mail_html", lambda _view: (_ for _ in ()).throw(RuntimeError("kaputt")))
    from app.models.einsatzinfo_mail import MailEinsatzinfoLog, MailEinsatzinfoLogRecipient

    db = SessionLocal(); set_tenant_context(db, None)
    try:
        log = db.query(MailEinsatzinfoLog).filter_by(incident_id=incident_id).one()
        db.query(MailEinsatzinfoLogRecipient).filter_by(log_id=log.id).update({"success": False})
        log.completed_at = None
        db.commit()
    finally:
        db.close()
    await service.dispatch_einsatzinfo_mail(org_id, incident_id)
    assert "Rauchentwicklung" in sent[-1].get_body(preferencelist=("html",)).get_content()


@pytest.mark.parametrize("pin,token", [(None, "abc123"), ("hash", "abc123"), ("hash", None)])
def test_mail_pin_anmeldung_nur_mit_pin_und_token(setup_db, monkeypatch, pin, token):
    from app.config import settings
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.services.einsatzinfo_mail_service import (
        build_einsatzinfo_mail_context, build_mail_view, render_einsatzinfo_mail_html,
    )

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://mail.example.test")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org, org_settings, alarm, incident = _data(db, f"pin-{pin}-{token}")
        incident.access_pin_hash, incident.alarm_token = pin, token
        view = build_mail_view(db, incident, org, alarm, org_settings, "Alarm", None)
        context = build_einsatzinfo_mail_context(incident, org, datetime.now(UTC))
        rendered = render_einsatzinfo_mail_html(view)
        expected = "https://mail.example.test/alarm/abc123/anmelden" if pin and token else ""
        assert (view["anmeldelink"] or "") == expected
        assert context["anmeldelink"] == expected
        assert ("Mit PIN anmelden" in rendered) == bool(expected)
        if expected:
            assert f'href="{expected}"' in rendered
