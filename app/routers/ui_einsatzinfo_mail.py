"""Admin-UI fuer die automatische Einsatzinfo-Mail."""
# ruff: noqa: E501
from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.core.timezones import to_org_tz
from app.db import get_db
from app.models.einsatzinfo_mail import MailEinsatzinfoLog, MailEinsatzinfoRecipient
from app.models.master import AlarmType, FireDept, Member, OrgSettings
from app.models.sms import SmsGroup
from app.routers.ui_sms import _require_org
from app.services import einsatzinfo_mail_service, mail_service

router = APIRouter(prefix="/admin")


def _selected_ids(form, name: str) -> set[int]:
    values: set[int] = set()
    for key, value in form.multi_items():
        if key != name:
            continue
        try:
            values.add(int(value))
        except (TypeError, ValueError):
            continue
    return values


def _external_emails(value: str) -> tuple[list[str], list[str]]:
    emails: list[str] = []
    invalid: list[str] = []
    for raw in value.replace("\r", "").replace(";", "\n").replace(",", "\n").split("\n"):
        email = raw.strip().lower()
        if not email:
            continue
        if not mail_service._looks_like_email(email):
            invalid.append(email)
        elif email not in emails:
            emails.append(email)
    return emails, invalid


def _replace_recipients(db: Session, org_id: int, alarm_type_id: int | None,
                        group_ids: set[int], member_ids: set[int], emails: list[str]) -> None:
    allowed_groups = {row.id for row in db.query(SmsGroup.id).filter(
        SmsGroup.org_id == org_id, SmsGroup.id.in_(group_ids or {-1})
    ).all()}
    allowed_members = {row.id for row in db.query(Member.id).filter(
        Member.org_id == org_id, Member.id.in_(member_ids or {-1})
    ).all()}
    existing = db.query(MailEinsatzinfoRecipient).filter(
        MailEinsatzinfoRecipient.org_id == org_id,
        # Kein .is_(id): "IS 5" ist in MariaDB ungueltig
        MailEinsatzinfoRecipient.alarm_type_id.is_(None) if alarm_type_id is None
        else MailEinsatzinfoRecipient.alarm_type_id == alarm_type_id,
    ).all()
    for row in existing:
        db.delete(row)
    for group_id in allowed_groups:
        db.add(MailEinsatzinfoRecipient(org_id=org_id, alarm_type_id=alarm_type_id, group_id=group_id))
    for member_id in allowed_members:
        db.add(MailEinsatzinfoRecipient(org_id=org_id, alarm_type_id=alarm_type_id, member_id=member_id))
    for email in emails:
        db.add(MailEinsatzinfoRecipient(org_id=org_id, alarm_type_id=alarm_type_id, email=email))


@router.get("/einsatzinfo-mail", response_class=HTMLResponse)
def einsatzinfo_mail_page(request: Request, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    user = request.state.user
    org_id = _require_org(user)
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    alarm_types = db.query(AlarmType).filter(AlarmType.org_id == org_id).order_by(AlarmType.category, AlarmType.code).all()
    groups = db.query(SmsGroup).filter(SmsGroup.org_id == org_id).order_by(SmsGroup.display_order, SmsGroup.name).all()
    members = db.query(Member).filter(Member.org_id == org_id, Member.active.is_(True)).order_by(Member.lastname, Member.firstname).all()
    entries = db.query(MailEinsatzinfoRecipient).filter(MailEinsatzinfoRecipient.org_id == org_id).all()
    distributions: dict[int | None, dict[str, object]] = {}
    for key in [None, *[alarm.id for alarm in alarm_types]]:
        recs = [row for row in entries if row.alarm_type_id == key]
        distributions[key] = {
            "group_ids": {row.group_id for row in recs if row.group_id},
            "member_ids": {row.member_id for row in recs if row.member_id},
            "emails": "\n".join(row.email for row in recs if row.email),
        }
    basis_group_ids = cast(set[int], distributions[None]["group_ids"])
    basis_member_ids = cast(set[int], distributions[None]["member_ids"])
    missing_ids = set(basis_member_ids)
    for group in groups:
        if group.id in basis_group_ids:
            missing_ids.update(gm.member_id for gm in group.members if gm.member)
    without_email = sum(1 for member in members if member.id in missing_ids and not (member.email or "").strip())
    logs = db.query(MailEinsatzinfoLog).filter(MailEinsatzinfoLog.org_id == org_id).order_by(MailEinsatzinfoLog.sent_at.desc()).limit(20).all()
    return templates.TemplateResponse(request, "admin/einsatzinfo_mail.html", {
        "user": user, "org_settings": settings, "alarm_types": alarm_types, "groups": groups, "members": members,
        "basis": distributions[None], "stichwort_recipients": distributions, "without_email": without_email,
        "mail_verfuegbar": mail_service.mail_available(db, org_id), "versandweg": mail_service.versandweg(db, org_id),
        "logs": logs, "default_subject": einsatzinfo_mail_service.default_einsatzinfo_mail_subject(),
        "default_template": einsatzinfo_mail_service.default_einsatzinfo_mail_template(), "saved": request.query_params.get("saved"),
        "testmail": request.query_params.get("testmail"), "fehler": request.query_params.get("fehler"),
    })


@router.post("/einsatzinfo-mail/einstellungen")
def einsatzinfo_mail_settings(request: Request, enabled: bool = Form(False), send_exercise: bool = Form(False),
                                    subject_template: str = Form(""), template: str = Form(""), db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    user = request.state.user
    org_id = _require_org(user)
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if not settings:
        raise HTTPException(status_code=404)
    settings.einsatzinfo_mail_enabled = enabled
    settings.einsatzinfo_mail_send_exercise = send_exercise
    settings.einsatzinfo_mail_subject_template = " ".join(subject_template.split())[:300] or None
    settings.einsatzinfo_mail_template = template.strip() or None
    write_audit(db, "admin.einsatzinfo_mail.settings_saved", org_id=org_id, user_id=user.id, payload={"enabled": enabled, "send_exercise": send_exercise})
    db.commit()
    return RedirectResponse("/admin/einsatzinfo-mail?saved=1", status_code=303)


async def _save_distribution(request: Request, db: Session, org_id: int, alarm_type_id: int | None) -> tuple[bool, str | None]:
    form = await request.form()
    emails, invalid = _external_emails(str(form.get("externe_emails", "")))
    if invalid:
        return False, ", ".join(invalid)
    _replace_recipients(db, org_id, alarm_type_id, _selected_ids(form, "group_id"), _selected_ids(form, "member_id"), emails)
    return True, None


@router.post("/einsatzinfo-mail/basis-verteiler")
async def einsatzinfo_mail_basis(request: Request, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    user = request.state.user
    org_id = _require_org(user)
    ok, invalid = await _save_distribution(request, db, org_id, None)
    if not ok:
        return RedirectResponse(f"/admin/einsatzinfo-mail?fehler={quote('Ungültige E-Mail-Adressen: ' + (invalid or ''))}", status_code=303)
    write_audit(db, "admin.einsatzinfo_mail.basis_saved", org_id=org_id, user_id=user.id)
    db.commit()
    return RedirectResponse("/admin/einsatzinfo-mail?saved=1", status_code=303)


@router.post("/einsatzinfo-mail/stichwort/{alarm_type_id}")
async def einsatzinfo_mail_stichwort(alarm_type_id: int, request: Request, subject_template_override: str = Form(""), template_override: str = Form(""), db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    user = request.state.user
    org_id = _require_org(user)
    alarm_type = db.query(AlarmType).filter(AlarmType.id == alarm_type_id, AlarmType.org_id == org_id).first()
    if not alarm_type:
        raise HTTPException(status_code=404)
    ok, invalid = await _save_distribution(request, db, org_id, alarm_type_id)
    if not ok:
        return RedirectResponse(f"/admin/einsatzinfo-mail?fehler={quote('Ungültige E-Mail-Adressen: ' + (invalid or ''))}#stichwort-{alarm_type_id}", status_code=303)
    alarm_type.einsatzinfo_mail_subject_template = " ".join(subject_template_override.split())[:300] or None
    alarm_type.einsatzinfo_mail_template = template_override.strip() or None
    write_audit(db, "admin.einsatzinfo_mail.stichwort_saved", org_id=org_id, user_id=user.id, entity_type="alarm_type", entity_id=alarm_type_id)
    db.commit()
    return RedirectResponse(f"/admin/einsatzinfo-mail?saved=1#stichwort-{alarm_type_id}", status_code=303)


def _sample_context(org: FireDept) -> dict[str, object]:
    """Legacy-Platzhalterkontext fuer die Textvorschau."""
    now = to_org_tz(datetime.now(UTC), org) or datetime.now(UTC)
    return {"stichwort": "B2", "adresse": "Musterstrasse 1, Wolfurt", "ort": "Wolfurt", "meldung": "Brand Wohnhaus", "einsatzgrund": "Brand Wohnhaus", "datum": now.strftime("%d.%m.%Y"), "zeit": now.strftime("%H:%M"), "link": "https://beispiel.at/alarm/beispiel", "leitstellennummer": "F26000001", "einsatznummer": 123, "kartenlink": "https://www.google.com/maps/search/?api=1&query=Musterstrasse+1%2C+Wolfurt"}


@router.post("/einsatzinfo-mail/vorschau", response_class=HTMLResponse)
def einsatzinfo_mail_preview(request: Request, subject_template: str = Form(""), template: str = Form(""), db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    org_id = _require_org(request.state.user)
    org = db.query(FireDept).filter(FireDept.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404)
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    context = _sample_context(org)
    subject = einsatzinfo_mail_service._render_mail_template(subject_template or einsatzinfo_mail_service.default_einsatzinfo_mail_subject(), context)
    body = einsatzinfo_mail_service._render_mail_template(template or einsatzinfo_mail_service.default_einsatzinfo_mail_template(), context)
    view = einsatzinfo_mail_service.sample_mail_view(org, org_settings)
    view["subject"] = subject
    view["hinweis"] = body if template.strip() else None
    rendered_html = einsatzinfo_mail_service.render_einsatzinfo_mail_html(view)
    return HTMLResponse(
        f'<div class="alert alert--info"><strong>{html.escape(subject)}</strong>'
        f'<pre class="sys-preview sys-mt-1">{html.escape(body)}</pre></div>'
        f'<h2>So sieht die Mail aus</h2>'
        f'<iframe sandbox="" srcdoc="{html.escape(rendered_html, quote=True)}" '
        f'class="sys-frame sys-frame--tall"></iframe>'
    )


@router.post("/einsatzinfo-mail/testmail")
async def einsatzinfo_mail_testmail(request: Request, db: Session = Depends(get_db), _=Depends(require_role("admin"))):
    user = request.state.user
    org_id = _require_org(user)
    if not user.email:
        return RedirectResponse("/admin/einsatzinfo-mail?fehler=Keine+E-Mail-Adresse+beim+Benutzer+hinterlegt", status_code=303)
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    subject_t = (settings.einsatzinfo_mail_subject_template if settings else None) or einsatzinfo_mail_service.default_einsatzinfo_mail_subject()
    body_t = (settings.einsatzinfo_mail_template if settings else None) or einsatzinfo_mail_service.default_einsatzinfo_mail_template()
    org = db.query(FireDept).filter(FireDept.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404)
    context = _sample_context(org)
    subject = "[TEST] " + einsatzinfo_mail_service._render_mail_template(subject_t, context)
    body = einsatzinfo_mail_service._render_mail_template(body_t, context)
    try:
        smtp_cfg = mail_service._org_smtp_cfg(db, org_id) or mail_service.get_smtp_cfg(db)
        view = einsatzinfo_mail_service.sample_mail_view(org, settings)
        view["subject"] = subject
        view["hinweis"] = body if (settings and settings.einsatzinfo_mail_template) else None
        msg = mail_service._build_message(
            to=user.email, subject=subject, body_txt=body,
            body_html=einsatzinfo_mail_service.render_einsatzinfo_mail_html(view), smtp_cfg=smtp_cfg,
        )
        await mail_service.deliver(db, org_id, msg, smtp_cfg)
    except Exception as exc:
        return RedirectResponse(f"/admin/einsatzinfo-mail?fehler={quote('Testmail fehlgeschlagen: ' + str(exc))}", status_code=303)
    return RedirectResponse("/admin/einsatzinfo-mail?testmail=1", status_code=303)
