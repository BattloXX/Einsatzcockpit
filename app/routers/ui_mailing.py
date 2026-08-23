"""HTML-/HTMX-Routen des Mailing-Moduls."""
import io
import json
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from openpyxl import Workbook
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident
from app.models.mailing import MailingCampaign, MailingCampaignAttachment, MailingCampaignRecipientList, MailingLinkClick, MailingQueueItem, MailingRecipientList, MailingRecipientListEntry, MailingRecipientListEntryTag, MailingTemplate, MailingSuppressionEntry
from app.models.master import MemberTag, MemberTagAssignment, Qualification
from app.services import mailing_service as service

router = APIRouter(prefix="/mailing", tags=["mailing"])


def require_mailing_enabled(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user or not service.mailing_effective_enabled(user.org_id, db):
        raise HTTPException(404, "Nicht gefunden")


def ctx(request, user, **kw):
    return {"request": request, "user": user, **kw}


PAGE_SIZE = 25


def _queue_page(db: Session, campaign_id: int, q: str, page: int):
    query = db.query(MailingQueueItem).filter(MailingQueueItem.campaign_id == campaign_id)
    if q:
        query = query.filter(MailingQueueItem.email.ilike(f"%{q}%"))
    total = query.count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 1), total_pages)
    rows = query.order_by(MailingQueueItem.email).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return rows, page, total, total_pages


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request, user=Depends(require_role("mailing_admin", "mailing_sender")), _g=Depends(require_mailing_enabled)
):
    return templates.TemplateResponse(request, "mailing/index.html", ctx(request, user))


@router.get("/templates", response_class=HTMLResponse)
def template_list(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
):
    return templates.TemplateResponse(
        request,
        "mailing/templates_list.html",
        ctx(request, user, items=db.query(MailingTemplate).order_by(MailingTemplate.name).all()),
    )


@router.get("/templates/new", response_class=HTMLResponse)
def template_new(request: Request, user=Depends(require_role("mailing_admin")), _g=Depends(require_mailing_enabled)):
    return templates.TemplateResponse(request, "mailing/template_form.html", ctx(request, user, item=None))


@router.post("/templates/save")
def template_save(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
    template_id: int | None = Form(None),
    name: str = Form(...),
    subject: str = Form(...),
    body_html: str = Form(...),
    body_text: str = Form(""),
    description: str = Form(""),
):
    item = db.get(MailingTemplate, template_id) if template_id else None
    if not item:
        item = service.create_template(
            db, user.org_id, name=name, subject=subject, body_html=body_html, created_by_user_id=user.id
        )
    item.name = name
    item.subject = subject
    item.body_html = body_html
    item.body_text = body_text or None
    item.description = description or None
    write_audit(db,"mailing.template.saved",org_id=user.org_id,user_id=user.id,entity_type="mailing_template",entity_id=item.id)
    db.commit()
    return RedirectResponse("/mailing/templates", 303)


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def template_edit(
    template_id: int,
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
):
    item = db.get(MailingTemplate, template_id)
    if not item:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "mailing/template_form.html", ctx(request, user, item=item))


@router.post("/templates/preview", response_class=HTMLResponse)
def preview(
    request: Request,
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
):
    from app.services.mailing_render import render_template

    rendered = render_template(
        subject,
        body_html,
        body_text,
        {
            "vorname": "Max",
            "nachname": "Mustermann",
            "email": "max@example.at",
            "empfaenger_name": "Max Mustermann",
            "org_name": getattr(user.org, "name", ""),
        },
    )
    return templates.TemplateResponse(
        request,
        "mailing/template_preview.html",
        ctx(request, user, subject=rendered[0], body_html=rendered[1], body_text=rendered[2]),
    )


@router.get("/lists", response_class=HTMLResponse)
def lists(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
    q: str = "",
    kind: str = "",
):
    service.ensure_auto_lists(db, user.org_id)
    db.commit()
    query = db.query(MailingRecipientList)
    if q:
        query = query.filter(MailingRecipientList.name.ilike(f"%{q.strip()}%"))
    if kind in {"static", "dynamic", "auto"}:
        query = query.filter(MailingRecipientList.kind == kind)
    items = query.order_by(MailingRecipientList.name).all()
    return templates.TemplateResponse(
        request,
        "mailing/lists_index.html",
        ctx(request, user, items=items, incidents=db.query(Incident).order_by(Incident.id.desc()).limit(100).all(), q=q, kind=kind),
    )

@router.get("/lists/new",response_class=HTMLResponse)
def list_new(request:Request,db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    return templates.TemplateResponse(request,"mailing/list_form.html",ctx(request,user,qualifications=db.query(Qualification).order_by(Qualification.label).all(),tags=db.query(MemberTag).order_by(MemberTag.name).all()))


@router.post("/lists/create")
def list_create(
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
    name: str = Form(...),
    description: str = Form(""),
    kind: str = Form("static"),
    active: str = Form(""),
    qualification_codes: list[str] = Form([]),
    member_since_after: str = Form(""),
    member_since_before: str = Form(""),
    tag_ids: list[int] = Form([]),
):
    if kind not in {"static", "dynamic"}: raise HTTPException(400)
    filters = {"qualification_codes": qualification_codes, "tag_ids": tag_ids}
    if active in {"true", "false"}: filters["active"] = active == "true"
    if member_since_after: filters["member_since_after"] = member_since_after
    if member_since_before: filters["member_since_before"] = member_since_before
    obj = service.create_recipient_list(db, user.org_id, name=name, kind=kind, description=description or None, filter_json=json.dumps(filters) if kind=="dynamic" else None)
    write_audit(db, f"mailing.list.{kind}_created", org_id=user.org_id, user_id=user.id, entity_type="mailing_recipient_list", entity_id=obj.id)
    db.commit()
    return RedirectResponse("/mailing/lists", 303)


@router.get("/lists/import-vorlage.xlsx")
def list_import_template(
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Empfaenger"
    sheet.append(["E-Mail", "Name", "Gruppe"])
    sheet.append(["max.mustermann@example.at", "Max Mustermann", "Einsatzleitung, Presse"])
    output = io.BytesIO()
    workbook.save(output)
    return Response(
        output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="mailing-import-vorlage.xlsx"'},
    )


@router.get("/lists/{list_id}", response_class=HTMLResponse)
def list_detail(
    list_id: int,
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
    q: str = "",
    page: int = 1,
):
    item = db.get(MailingRecipientList, list_id)
    if not item:
        raise HTTPException(404)
    entries = []
    total = resolved_count = 0
    total_pages = 1
    filter_summary = ""
    group_stats = []
    if item.kind == "static":
        query = db.query(MailingRecipientListEntry).filter(MailingRecipientListEntry.list_id == item.id)
        if q:
            query = query.filter(MailingRecipientListEntry.email.ilike(f"%{q.strip()}%"))
        total = query.count()
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(page, 1), total_pages)
        entries = query.order_by(MailingRecipientListEntry.email).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
        group_stats = (
            db.query(MemberTag.name, func.count(MailingRecipientListEntryTag.entry_id))
            .join(MailingRecipientListEntryTag, MailingRecipientListEntryTag.tag_id == MemberTag.id)
            .join(MailingRecipientListEntry, MailingRecipientListEntry.id == MailingRecipientListEntryTag.entry_id)
            .filter(
                MemberTag.org_id == user.org_id,
                MailingRecipientListEntry.org_id == user.org_id,
                MailingRecipientListEntry.list_id == item.id,
            )
            .group_by(MemberTag.id, MemberTag.name)
            .order_by(MemberTag.name)
            .all()
        )
    elif item.kind == "dynamic":
        from app.services.mailing_recipients import resolve_dynamic_list

        resolved_count = len(resolve_dynamic_list(db, user.org_id, item.filter_json))
        total = resolved_count
        filter_summary = service.summarize_filter_json(item.filter_json, db, user.org_id)
    else:
        from app.services.mailing_recipients import resolve_recipient_list

        resolved_count = len(resolve_recipient_list(db, item))
        total = resolved_count
    return templates.TemplateResponse(request, "mailing/list_detail.html", ctx(request, user, item=item, entries=entries, total=total, resolved_count=resolved_count, filter_summary=filter_summary, group_stats=group_stats, import_error=request.query_params.get("import_error"), q=q, page=page, total_pages=total_pages))


@router.post("/lists/{list_id}/csv")
async def csv_import(
    list_id: int,
    file: UploadFile = File(...),
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
):
    item = db.get(MailingRecipientList, list_id)
    if not item or item.kind != "static":
        raise HTTPException(404)
    service.import_csv(db, item, await file.read())
    db.commit()
    return RedirectResponse(f"/mailing/lists/{item.id}", 303)


@router.post("/lists/{list_id}/xlsx")
async def xlsx_import(
    list_id: int,
    file: UploadFile = File(...),
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
):
    item = db.get(MailingRecipientList, list_id)
    if not item or item.kind != "static":
        raise HTTPException(404)
    try:
        service.import_xlsx(db, item, await file.read())
        db.commit()
    except Exception as exc:
        db.rollback()
        return RedirectResponse(
            f"/mailing/lists/{item.id}?import_error={quote(str(exc))}",
            303,
        )
    return RedirectResponse(f"/mailing/lists/{item.id}", 303)


@router.get("/campaigns", response_class=HTMLResponse)
def campaigns(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin", "mailing_sender")),
    _g=Depends(require_mailing_enabled),
    status: str = "",
):
    query = db.query(MailingCampaign)
    if status in {"draft", "scheduled", "queued", "sending", "sent", "failed", "cancelled"}:
        query = query.filter(MailingCampaign.status == status)
    return templates.TemplateResponse(
        request,
        "mailing/campaigns_list.html",
        ctx(request, user, items=query.order_by(MailingCampaign.created_at.desc()).all(), status=status),
    )


@router.get("/campaigns/new", response_class=HTMLResponse)
def campaign_new(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin", "mailing_sender")),
    _g=Depends(require_mailing_enabled),
):
    return templates.TemplateResponse(
        request,
        "mailing/campaign_form.html",
        ctx(
            request,
            user,
            templates_list=db.query(MailingTemplate).filter(MailingTemplate.is_active.is_(True)).all(),
            recipient_lists=db.query(MailingRecipientList).filter(MailingRecipientList.is_active.is_(True)).all(),
            incidents=db.query(Incident).order_by(Incident.id.desc()).limit(100).all(),
        ),
    )


@router.post("/campaigns/create")
def campaign_create(
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin", "mailing_sender")),
    _g=Depends(require_mailing_enabled),
    template_id: int = Form(...),
    recipient_list_ids: list[int] = Form([]),
    recipient_list_id: int | None = Form(None),
    source_incident_id: int | None = Form(None),
    subject_override: str = Form(""),
    reply_to_override: str = Form(""),
    track_opens: str = Form(""), track_clicks: str = Form(""), max_attempts_override: int | None = Form(None),
):
    ids = recipient_list_ids or ([recipient_list_id] if recipient_list_id else [])
    if not ids: raise HTTPException(422,"Mindestens eine Empfängerliste ist erforderlich.")
    obj = service.create_campaign(
        db,
        user.org_id,
        template_id=template_id,
        recipient_list_id=None,
        source_incident_id=source_incident_id,
        subject_override=subject_override or None,
        reply_to_override=reply_to_override or None,
        created_by_user_id=user.id,
        track_opens=track_opens == "1", track_clicks=track_clicks == "1", max_attempts_override=max_attempts_override,
    )
    valid=db.query(MailingRecipientList).filter(MailingRecipientList.id.in_(ids)).all()
    if len(valid)!=len(set(ids)): raise HTTPException(404)
    for lst in valid: db.add(MailingCampaignRecipientList(campaign_id=obj.id,recipient_list_id=lst.id))
    write_audit(db,"mailing.campaign.created",org_id=user.org_id,user_id=user.id,entity_type="mailing_campaign",entity_id=obj.id)
    db.commit()
    return RedirectResponse(f"/mailing/campaigns/{obj.id}", 303)


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_detail(
    campaign_id: int,
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin", "mailing_sender")),
    _g=Depends(require_mailing_enabled),
    q: str = "",
    page: int = 1,
):
    item = db.get(MailingCampaign, campaign_id)
    if not item:
        raise HTTPException(404)
    sent = item.sent_count or 0
    open_rate = round(100 * (item.open_count or 0) / sent, 1) if sent else 0
    click_rate = round(100 * (item.click_count or 0) / sent, 1) if sent else 0
    start = item.sent_at or item.queued_at or item.created_at
    opened = db.query(MailingQueueItem.opened_at).filter(MailingQueueItem.campaign_id == item.id, MailingQueueItem.opened_at.isnot(None)).all()
    clicked = db.query(MailingLinkClick.clicked_at).join(MailingQueueItem, MailingQueueItem.id == MailingLinkClick.queue_item_id).filter(MailingQueueItem.campaign_id == item.id).all()
    max_hour = 0
    open_map: dict[int, int] = {}
    click_map: dict[int, int] = {}
    for target, rows in ((open_map, opened), (click_map, clicked)):
        for (stamp,) in rows:
            hour = max(0, int((stamp - start).total_seconds() // 3600))
            target[hour] = target.get(hour, 0) + 1
            max_hour = max(max_hour, hour)
    labels = [f"+{hour} h" for hour in range(max_hour + 1)]
    queue_items, page, queue_total, total_pages = _queue_page(db, item.id, q.strip(), page)
    return templates.TemplateResponse(request, "mailing/campaign_detail.html", ctx(request, user, item=item, open_rate=open_rate, click_rate=click_rate, reaction_labels=labels, reaction_opens=[open_map.get(hour, 0) for hour in range(max_hour + 1)], reaction_clicks=[click_map.get(hour, 0) for hour in range(max_hour + 1)], queue_items=queue_items, q=q, page=page, queue_total=queue_total, total_pages=total_pages, test_sent=request.query_params.get("test_sent") == "1"))


@router.post("/campaigns/{campaign_id}/test-send")
async def campaign_test_send(
    campaign_id: int,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin", "mailing_sender")),
    _g=Depends(require_mailing_enabled),
):
    item = db.get(MailingCampaign, campaign_id)
    if not item:
        raise HTTPException(404)
    if not user.email:
        raise HTTPException(422, "Für den aktuellen Benutzer ist keine E-Mail-Adresse hinterlegt.")
    config = service.get_mailing_config(db, user.org_id)
    api_key = service.mailing_api_key(config)
    if not config or not config.enabled or not api_key or not config.from_addr:
        raise HTTPException(409, "Mailing ist nicht vollständig konfiguriert.")
    from app.services.mailing_render import render_template
    from app.services.resend_mail_service import send_via_resend

    subject, html, plain = render_template(item.subject_override or item.template.subject, item.template.body_html, item.template.body_text, {"vorname": user.display_name.split(" ", 1)[0], "nachname": user.display_name.split(" ", 1)[1] if " " in user.display_name else "", "email": user.email, "empfaenger_name": user.display_name, "org_name": getattr(user.org, "name", "")})
    message = EmailMessage()
    message["To"] = user.email
    message["Subject"] = "[TEST] " + subject
    reply_to = item.reply_to_override or config.reply_to_default
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(plain or "")
    message.add_alternative(html, subtype="html")
    await send_via_resend(message, api_key, config.from_addr)
    write_audit(db, "mailing.campaign.test_sent", org_id=user.org_id, user_id=user.id, entity_type="mailing_campaign", entity_id=item.id)
    db.commit()
    return RedirectResponse(f"/mailing/campaigns/{campaign_id}?test_sent=1", 303)


@router.post("/campaigns/{campaign_id}/send")
async def campaign_send(
    campaign_id: int,
    immediate: str = Form(""),
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin", "mailing_sender")),
    _g=Depends(require_mailing_enabled),
):
    item = db.get(MailingCampaign, campaign_id)
    if not item:
        raise HTTPException(404)
    service.queue_campaign(db, item)
    write_audit(db,"mailing.campaign.sent_immediate" if immediate else "mailing.campaign.queued",org_id=user.org_id,user_id=user.id,entity_type="mailing_campaign",entity_id=item.id)
    db.commit()
    if immediate:
        from app.services.mailing_dispatch_loop import dispatch_once

        await dispatch_once(db)
    return RedirectResponse(f"/mailing/campaigns/{campaign_id}", 303)


@router.get("/campaigns/{campaign_id}/status", response_class=HTMLResponse)
def queue_status(
    campaign_id: int,
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin", "mailing_sender")),
    _g=Depends(require_mailing_enabled),
    q: str = "",
    page: int = 1,
):
    item = db.get(MailingCampaign, campaign_id)
    if not item:
        raise HTTPException(404)
    queue_items, page, queue_total, total_pages = _queue_page(db, item.id, q.strip(), page)
    return templates.TemplateResponse(request, "mailing/_queue_status_partial.html", ctx(request, user, item=item, queue_items=queue_items, q=q, page=page, queue_total=queue_total, total_pages=total_pages))


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
):
    from app.core.security import sign_mailing_webhook_org
    webhook_url = str(request.base_url).rstrip("/") + "/mailing/webhook/resend/" + sign_mailing_webhook_org(user.org_id)
    return templates.TemplateResponse(request, "mailing/settings.html", ctx(request, user,
        config=service.get_mailing_config(db, user.org_id), webhook_url=webhook_url))


@router.post("/settings")
def settings_save(
    db=Depends(get_db),
    user=Depends(require_role("mailing_admin")),
    _g=Depends(require_mailing_enabled),
    enabled: str = Form(""),
    resend_api_key: str = Form(""),
    resend_webhook_secret: str = Form(""),
    from_addr: str = Form(""),
    reply_to_default: str = Form(""),
    sender_display_name: str = Form(""),
):
    service.save_mailing_config(
        db,
        user.org_id,
        enabled=enabled == "1",
        resend_api_key=resend_api_key or None,
        resend_webhook_secret=resend_webhook_secret or None,
        from_addr=from_addr,
        reply_to_default=reply_to_default,
        sender_display_name=sender_display_name,
    )
    write_audit(db,"mailing.config.updated",org_id=user.org_id,user_id=user.id)
    db.commit()
    return RedirectResponse("/mailing/settings", 303)

@router.post("/campaigns/{campaign_id}/schedule")
def campaign_schedule(campaign_id:int, scheduled_at:str=Form(...), db=Depends(get_db), user=Depends(require_role("mailing_admin","mailing_sender")), _g=Depends(require_mailing_enabled)):
    item=db.get(MailingCampaign,campaign_id)
    if not item or item.status!="draft": raise HTTPException(404)
    from app.core.timezones import local_input_to_utc
    value=local_input_to_utc(scheduled_at,user.org)
    if value is None: raise HTTPException(422,"Ungültiger Zeitpunkt")
    item.scheduled_at=value; item.status="scheduled"
    write_audit(db,"mailing.campaign.scheduled",org_id=user.org_id,user_id=user.id,entity_type="mailing_campaign",entity_id=item.id); db.commit()
    return RedirectResponse(f"/mailing/campaigns/{campaign_id}",303)

@router.post("/campaigns/{campaign_id}/cancel")
def campaign_cancel(campaign_id:int,db=Depends(get_db),user=Depends(require_role("mailing_admin","mailing_sender")),_g=Depends(require_mailing_enabled)):
    item=db.get(MailingCampaign,campaign_id)
    if not item or item.status not in {"draft","scheduled"}: raise HTTPException(409)
    item.status="cancelled"; item.cancelled_at=datetime.now(UTC).replace(tzinfo=None)
    write_audit(db,"mailing.campaign.cancelled",org_id=user.org_id,user_id=user.id,entity_type="mailing_campaign",entity_id=item.id); db.commit()
    return RedirectResponse(f"/mailing/campaigns/{campaign_id}",303)

@router.post("/campaigns/{campaign_id}/retry-failed")
def campaign_retry(campaign_id:int,db=Depends(get_db),user=Depends(require_role("mailing_admin","mailing_sender")),_g=Depends(require_mailing_enabled)):
    item=db.get(MailingCampaign,campaign_id)
    if not item: raise HTTPException(404)
    count=service.retry_failed_items(db,item)
    write_audit(db,"mailing.campaign.retry_failed",org_id=user.org_id,user_id=user.id,entity_type="mailing_campaign",entity_id=item.id,payload={"count":count}); db.commit()
    return RedirectResponse(f"/mailing/campaigns/{campaign_id}",303)

@router.post("/campaigns/{campaign_id}/attachments")
async def attachment_upload(campaign_id:int,file:UploadFile=File(...),db=Depends(get_db),user=Depends(require_role("mailing_admin","mailing_sender")),_g=Depends(require_mailing_enabled)):
    from app.services.mailing_attachments import store_attachment
    item=db.get(MailingCampaign,campaign_id)
    if not item: raise HTTPException(404)
    obj=store_attachment(db,item,file.filename or "attachment",await file.read(),user.id)
    write_audit(db,"mailing.attachment.uploaded",org_id=user.org_id,user_id=user.id,entity_type="mailing_campaign_attachment",entity_id=obj.id); db.commit()
    return RedirectResponse(f"/mailing/campaigns/{campaign_id}",303)

@router.post("/campaigns/{campaign_id}/attachments/{attachment_id}/delete")
def attachment_delete(campaign_id:int,attachment_id:int,db=Depends(get_db),user=Depends(require_role("mailing_admin","mailing_sender")),_g=Depends(require_mailing_enabled)):
    from app.services.mailing_attachments import delete_attachment
    campaign=db.get(MailingCampaign,campaign_id); obj=db.get(MailingCampaignAttachment,attachment_id)
    if not campaign or campaign.status!="draft" or not obj or obj.campaign_id!=campaign.id: raise HTTPException(404)
    delete_attachment(db,obj); write_audit(db,"mailing.attachment.deleted",org_id=user.org_id,user_id=user.id,entity_type="mailing_campaign_attachment",entity_id=attachment_id); db.commit()
    return RedirectResponse(f"/mailing/campaigns/{campaign_id}",303)

@router.get("/tags",response_class=HTMLResponse)
def tags(request:Request,db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    return templates.TemplateResponse(request,"mailing/tags_list.html",ctx(request,user,items=db.query(MemberTag).order_by(MemberTag.name).all()))
@router.post("/tags")
def tag_create(name:str=Form(...),color:str=Form(""),db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    obj=MemberTag(org_id=user.org_id,name=name.strip(),color=color or None); db.add(obj); db.commit(); return RedirectResponse("/mailing/tags",303)
@router.post("/tags/{tag_id}/delete")
def tag_delete(tag_id:int,db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    obj=db.get(MemberTag,tag_id)
    if not obj: raise HTTPException(404)
    for x in db.query(MemberTagAssignment).filter(MemberTagAssignment.tag_id==obj.id).all(): db.delete(x)
    db.delete(obj); db.commit(); return RedirectResponse("/mailing/tags",303)

@router.post("/members/{member_id}/tags",response_class=HTMLResponse)
def member_tags(member_id:int,request:Request,tag_ids:list[int]=Form([]),db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    from app.models.master import Member
    member=db.query(Member).filter(Member.id==member_id,Member.org_id==user.org_id).first()
    if not member: raise HTTPException(404)
    for row in db.query(MemberTagAssignment).filter(MemberTagAssignment.member_id==member.id).all(): db.delete(row)
    valid=db.query(MemberTag).filter(MemberTag.id.in_(tag_ids)).all() if tag_ids else []
    for tag in valid: db.add(MemberTagAssignment(member_id=member.id,tag_id=tag.id))
    db.commit()
    return templates.TemplateResponse(request,"mailing/_member_tags.html",ctx(request,user,member=member,tags=db.query(MemberTag).order_by(MemberTag.name).all(),assigned_ids=set(tag_ids)))

@router.post("/lists/import-from-incident")
def import_incident(incident_id:int=Form(...),list_name:str=Form(...),db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    from app.services.mailing_recipients import resolve_incident_participants
    incident=db.query(Incident).filter(Incident.id==incident_id).first()
    if not incident: raise HTTPException(404)
    obj=service.create_recipient_list(db,user.org_id,name=list_name,kind="static")
    for row in resolve_incident_participants(db,user.org_id,incident_id): db.add(MailingRecipientListEntry(org_id=user.org_id,list_id=obj.id,email=row["email"],display_name=row["display_name"]))
    write_audit(db,"mailing.list.incident_imported",org_id=user.org_id,user_id=user.id,entity_type="mailing_recipient_list",entity_id=obj.id); db.commit()
    return RedirectResponse("/mailing/lists",303)

@router.get("/dashboard",response_class=HTMLResponse)
def dashboard(request:Request,db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    return templates.TemplateResponse(request,"mailing/dashboard.html",ctx(request,user,**service.build_mailing_dashboard_data(db)))

@router.get("/lists/{list_id}/export.csv")
def list_export(list_id:int,db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    import csv, io
    from app.services.mailing_recipients import resolve_recipient_list
    item=db.get(MailingRecipientList,list_id)
    if not item: raise HTTPException(404)
    buf=io.StringIO(); writer=csv.writer(buf,delimiter=";"); writer.writerow(["E-Mail","Name"])
    for row in resolve_recipient_list(db,item,incident_id=None): writer.writerow([row["email"],row.get("display_name") or ""])
    return Response(content=("﻿"+buf.getvalue()).encode("utf-8"),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":f"attachment; filename=empfaengerliste_{list_id}.csv"})

@router.get("/campaigns/{campaign_id}/export.csv")
def campaign_export(campaign_id:int,db=Depends(get_db),user=Depends(require_role("mailing_admin","mailing_sender")),_g=Depends(require_mailing_enabled)):
    import csv, io
    from app.core.timezones import format_local_datetime
    item=db.get(MailingCampaign,campaign_id)
    if not item: raise HTTPException(404)
    buf=io.StringIO(); writer=csv.writer(buf,delimiter=";"); writer.writerow(["E-Mail","Name","Status","Versendet am","Geöffnet am","Öffnungen","Erster Klick am","Klicks","Fehler"])
    for x in db.query(MailingQueueItem).filter(MailingQueueItem.campaign_id==item.id).order_by(MailingQueueItem.id).all():
        fmt=lambda v: format_local_datetime(v,user.org) if v else ""
        writer.writerow([x.email,x.display_name or "",x.status,fmt(x.sent_at),fmt(x.opened_at),x.open_count,fmt(x.first_clicked_at),x.click_count,x.error_message or ""])
    return Response(content=("﻿"+buf.getvalue()).encode("utf-8"),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":f"attachment; filename=mailing_kampagne_{campaign_id}.csv"})

@router.get("/dashboard/report",response_class=HTMLResponse)
def dashboard_report(request:Request,db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    return templates.TemplateResponse(request,"mailing/dashboard_report.html",ctx(request,user,org=user.org,**service.build_mailing_dashboard_data(db)))

@router.get("/dashboard/report.pdf")
def dashboard_report_pdf(request:Request,db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    from app.services.pdf_service import render_mailing_report_pdf
    content=render_mailing_report_pdf(service.build_mailing_dashboard_data(db),user.org,str(request.base_url))
    return Response(content=content,media_type="application/pdf",headers={"Content-Disposition":"attachment; filename=mailing_bericht.pdf"})

@router.get("/suppression",response_class=HTMLResponse)
def suppression_list(request:Request,db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled),q:str="",page:int=1):
    query=db.query(MailingSuppressionEntry)
    if q: query=query.filter(MailingSuppressionEntry.email.ilike(f"%{q.strip()}%"))
    total=query.count(); pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE); page=min(max(page,1),pages)
    items=query.order_by(MailingSuppressionEntry.created_at.desc()).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE).all()
    return templates.TemplateResponse(request,"mailing/suppression_list.html",ctx(request,user,items=items,q=q,page=page,total_pages=pages,total=total))

@router.post("/suppression/add")
def suppression_add(email:str=Form(...),note:str=Form(""),db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    clean=email.strip().lower(); hit=db.query(MailingSuppressionEntry).filter(MailingSuppressionEntry.email==clean).first()
    if hit: hit.reason="manual"; hit.note=note or None; hit.created_by_user_id=user.id; hit.created_at=datetime.now(UTC).replace(tzinfo=None)
    else: db.add(MailingSuppressionEntry(org_id=user.org_id,email=clean,reason="manual",note=note or None,created_by_user_id=user.id))
    db.commit(); return RedirectResponse("/mailing/suppression",303)

@router.post("/suppression/{entry_id}/delete")
def suppression_delete(entry_id:int,db=Depends(get_db),user=Depends(require_role("mailing_admin")),_g=Depends(require_mailing_enabled)):
    item=db.get(MailingSuppressionEntry,entry_id)
    if not item: raise HTTPException(404)
    db.delete(item); db.commit(); return RedirectResponse("/mailing/suppression",303)
