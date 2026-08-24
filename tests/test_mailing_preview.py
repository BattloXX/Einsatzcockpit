from types import SimpleNamespace

from markupsafe import escape
from starlette.requests import Request

from app.core.templating import templates
from app.models.mailing import MailingTemplate
from app.routers.ui_mailing import campaign_preview, preview, template_save
from tests.mailing_phase2_helpers import campaign as build_campaign, db_session


def test_preview_srcdoc_attribute_escapes_script_and_quotes():
    body_html = '<script>alert("preview-xss")</script><p title="quoted">Hallo</p>'
    subject = '<script>alert("subject-xss")</script> "Betreff"'

    rendered = templates.env.get_template("mailing/template_preview.html").render(
        subject=subject,
        body_html=body_html,
        body_text=None,
    )

    assert f'srcdoc="{escape(body_html)}"' in rendered
    assert str(escape(subject)) in rendered
    assert 'srcdoc="<script>' not in rendered
    assert 'sandbox="allow-same-origin"' in rendered
    assert "allow-scripts" not in rendered


def test_template_save_persists_and_updates_preheader():
    db = db_session()
    user = SimpleNamespace(id=None, org_id=1)
    response = template_save(
        request=None,
        db=db,
        user=user,
        _g=None,
        template_id=None,
        name="Alarmierung",
        subject="Erster Betreff",
        preheader="Erster Preheader",
        body_html="<p>Hallo</p>",
        body_text="Hallo",
        description="Test",
    )
    assert response.status_code == 303
    item = db.query(MailingTemplate).filter(MailingTemplate.name == "Alarmierung").one()
    assert item.preheader == "Erster Preheader"

    template_save(
        request=None,
        db=db,
        user=user,
        _g=None,
        template_id=item.id,
        name=item.name,
        subject="Bearbeiteter Betreff",
        preheader="Bearbeiteter Preheader",
        body_html=item.body_html,
        body_text=item.body_text or "",
        description=item.description or "",
    )
    db.refresh(item)
    assert item.subject == "Bearbeiteter Betreff"
    assert item.preheader == "Bearbeiteter Preheader"
    db.rollback()
    db.close()


def test_preview_context_contains_plain_text_preheader():
    request = Request({"type": "http", "method": "POST", "path": "/mailing/templates/preview", "headers": []})
    user = SimpleNamespace(org=SimpleNamespace(name="Feuerwehr Test"))
    response = preview(
        request=request,
        user=user,
        _g=None,
        subject="Hallo {{ vorname }}",
        preheader="Vorschau für {{ vorname }}",
        body_html="<p>{{ vorname }}</p>",
        body_text="{{ vorname }}",
    )

    assert response.context["subject"] == "Hallo Max"
    assert response.context["preheader"] == "Vorschau für {{ vorname }}"
    assert "Vorschau für {{ vorname }}" in response.body.decode()
    assert "26 Zeichen" in response.body.decode()
    assert "Desktop zeigt ca. 80" in response.body.decode()
    assert "Mobil zeigt ca. 40" in response.body.decode()
    assert '<a href="#">Jetzt abmelden</a>' in response.context["body_html"]
    assert "Jetzt abmelden: #" in response.context["body_text"]


def test_campaign_preview_contains_unsubscribe_footer():
    db = db_session()
    item, _ = build_campaign(db)
    request = Request({"type": "http", "method": "GET", "path": f"/mailing/campaigns/{item.id}/preview", "headers": []})
    user = SimpleNamespace(org=SimpleNamespace(name="Feuerwehr Test"))
    response = campaign_preview(item.id, request, db=db, user=user, _g=None)
    assert '<a href="#">Jetzt abmelden</a>' in response.context["body_html"]
    assert "Jetzt abmelden: #" in response.context["body_text"]
    db.close()
