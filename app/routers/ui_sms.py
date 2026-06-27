"""Admin-UI: SMS-Gruppen, Einsatzinfo-SMS-Konfiguration und manueller SMS-Versand."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import has_role, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.master import AlarmType, Member, OrgSettings
from app.models.sms import SmsEinsatzinfoRecipient, SmsGroup, SmsGroupMember, SmsLog

router = APIRouter(prefix="/admin")
logger = logging.getLogger("einsatzleiter.ui_sms")


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _require_org(user) -> int:
    """Gibt org_id des Users zurueck. Wirft 403 wenn kein Org-Kontext vorhanden."""
    if not user.org_id:
        raise HTTPException(status_code=403, detail="Kein Org-Kontext")
    return user.org_id


def _sms_groups_for_org(db: Session, org_id: int) -> list[SmsGroup]:
    return (
        db.query(SmsGroup)
        .filter(SmsGroup.org_id == org_id)
        .order_by(SmsGroup.display_order, SmsGroup.name)
        .all()
    )


def _active_members(db: Session, org_id: int) -> list[Member]:
    return (
        db.query(Member)
        .filter(Member.org_id == org_id, Member.active.is_(True))
        .order_by(Member.lastname, Member.firstname)
        .all()
    )


# ── SMS-Gruppen ───────────────────────────────────────────────────────────────

@router.get("/gruppen", response_class=HTMLResponse)
async def sms_groups_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    user = request.state.user
    org_id = _require_org(user)
    groups = _sms_groups_for_org(db, org_id)
    members = _active_members(db, org_id)
    return templates.TemplateResponse(request, "admin/sms_groups.html", {
        "user": user,
        "groups": groups,
        "members": members,
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
    })


@router.post("/gruppen/neu")
async def sms_group_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    user = request.state.user
    org_id = _require_org(user)
    name = name.strip()
    if not name:
        return RedirectResponse("/admin/sms-gruppen?error=empty", status_code=303)
    grp = SmsGroup(
        org_id=org_id,
        name=name,
        description=description.strip() or None,
        display_order=0,
        created_at=datetime.now(UTC),
    )
    db.add(grp)
    write_audit(db, "admin.sms_group.created", org_id=org_id, user_id=user.id,
                entity_type="sms_group", payload={"name": name})
    db.commit()
    return RedirectResponse(f"/admin/gruppen?saved=1#gruppe-{grp.id}", status_code=303)


@router.post("/gruppen/{group_id}/edit")
async def sms_group_edit(
    group_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    user = request.state.user
    org_id = _require_org(user)
    grp = db.get(SmsGroup, group_id)
    if not grp or grp.org_id != org_id:
        raise HTTPException(status_code=404)
    grp.name = name.strip() or grp.name
    grp.description = description.strip() or None
    write_audit(db, "admin.sms_group.edited", org_id=org_id, user_id=user.id,
                entity_type="sms_group", entity_id=group_id)
    db.commit()
    return RedirectResponse(f"/admin/gruppen?saved=1#gruppe-{group_id}", status_code=303)


@router.post("/gruppen/{group_id}/loeschen")
async def sms_group_delete(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    user = request.state.user
    org_id = _require_org(user)
    grp = db.get(SmsGroup, group_id)
    if not grp or grp.org_id != org_id:
        raise HTTPException(status_code=404)
    db.delete(grp)
    write_audit(db, "admin.sms_group.deleted", org_id=org_id, user_id=user.id,
                entity_type="sms_group", entity_id=group_id)
    db.commit()
    return RedirectResponse("/admin/gruppen?saved=1", status_code=303)


@router.post("/gruppen/{group_id}/mitglieder")
async def sms_group_set_members(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    """Setzt die Mitglieder einer Gruppe (vollstaendiger Ersatz via HTMX-Formular)."""
    user = request.state.user
    org_id = _require_org(user)
    grp = db.get(SmsGroup, group_id)
    if not grp or grp.org_id != org_id:
        raise HTTPException(status_code=404)

    form = await request.form()
    selected_ids = {int(v) for k, v in form.multi_items() if k == "member_id"}

    # Alle bestehenden Eintraege loeschen und neu anlegen
    db.query(SmsGroupMember).filter(SmsGroupMember.sms_group_id == group_id).delete()
    for mid in selected_ids:
        db.add(SmsGroupMember(sms_group_id=group_id, member_id=mid))

    write_audit(db, "admin.sms_group.members_updated", org_id=org_id, user_id=user.id,
                entity_type="sms_group", entity_id=group_id,
                payload={"count": len(selected_ids)})
    db.commit()

    # HTMX-Partial: Gruppen-Liste neu rendern
    groups = _sms_groups_for_org(db, org_id)
    members = _active_members(db, org_id)
    return templates.TemplateResponse(request, "admin/sms_groups.html", {
        "user": user,
        "groups": groups,
        "members": members,
        "saved": "1",
        "error": None,
    })


# ── Einsatzinfo-SMS-Konfiguration ─────────────────────────────────────────────

@router.get("/einsatzinfo-sms", response_class=HTMLResponse)
async def einsatzinfo_sms_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    user = request.state.user
    org_id = _require_org(user)

    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    alarm_types = (
        db.query(AlarmType)
        .filter(AlarmType.org_id == org_id)
        .order_by(AlarmType.category, AlarmType.code)
        .all()
    )
    groups = _sms_groups_for_org(db, org_id)
    members = _active_members(db, org_id)

    # Basis-Verteiler (alarm_type_id IS NULL)
    basis_recipients = (
        db.query(SmsEinsatzinfoRecipient)
        .filter(
            SmsEinsatzinfoRecipient.org_id == org_id,
            SmsEinsatzinfoRecipient.alarm_type_id.is_(None),
        )
        .all()
    )
    basis_group_ids = {r.group_id for r in basis_recipients if r.group_id}
    basis_member_ids = {r.member_id for r in basis_recipients if r.member_id}

    # Verteiler je Stichwort
    stichwort_recipients: dict[int, dict] = {}
    for at in alarm_types:
        recs = (
            db.query(SmsEinsatzinfoRecipient)
            .filter(
                SmsEinsatzinfoRecipient.org_id == org_id,
                SmsEinsatzinfoRecipient.alarm_type_id == at.id,
            )
            .all()
        )
        stichwort_recipients[at.id] = {
            "group_ids": {r.group_id for r in recs if r.group_id},
            "member_ids": {r.member_id for r in recs if r.member_id},
        }

    from app.services.sms_dispatch_service import default_einsatzinfo_template
    return templates.TemplateResponse(request, "admin/einsatzinfo_sms.html", {
        "user": user,
        "org_settings": org_settings,
        "alarm_types": alarm_types,
        "groups": groups,
        "members": members,
        "basis_group_ids": basis_group_ids,
        "basis_member_ids": basis_member_ids,
        "stichwort_recipients": stichwort_recipients,
        "default_template": default_einsatzinfo_template(),
        "saved": request.query_params.get("saved"),
    })


@router.post("/einsatzinfo-sms/einstellungen")
async def einsatzinfo_sms_save_settings(
    request: Request,
    enabled: bool = Form(False),
    send_exercise: bool = Form(False),
    template: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    """Speichert Aktivierungsschalter und Org-Standard-Vorlage."""
    user = request.state.user
    org_id = _require_org(user)
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if not org_settings:
        raise HTTPException(status_code=404)
    org_settings.einsatzinfo_sms_enabled = enabled
    org_settings.einsatzinfo_sms_send_exercise = send_exercise
    org_settings.einsatzinfo_sms_template = template.strip() or None
    write_audit(db, "admin.einsatzinfo_sms.settings_saved", org_id=org_id, user_id=user.id,
                payload={"enabled": enabled, "send_exercise": send_exercise})
    db.commit()
    return RedirectResponse("/admin/einsatzinfo-sms?saved=1", status_code=303)


@router.post("/einsatzinfo-sms/basis-verteiler")
async def einsatzinfo_sms_save_basis(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    """Speichert den Basis-Verteiler (gilt fuer alle Stichworte, alarm_type_id=NULL)."""
    user = request.state.user
    org_id = _require_org(user)

    form = await request.form()
    group_ids = {int(v) for k, v in form.multi_items() if k == "group_id"}
    member_ids = {int(v) for k, v in form.multi_items() if k == "member_id"}

    # Basis-Eintraege loeschen und neu anlegen
    db.query(SmsEinsatzinfoRecipient).filter(
        SmsEinsatzinfoRecipient.org_id == org_id,
        SmsEinsatzinfoRecipient.alarm_type_id.is_(None),
    ).delete()

    for gid in group_ids:
        db.add(SmsEinsatzinfoRecipient(org_id=org_id, alarm_type_id=None, group_id=gid))
    for mid in member_ids:
        db.add(SmsEinsatzinfoRecipient(org_id=org_id, alarm_type_id=None, member_id=mid))

    write_audit(db, "admin.einsatzinfo_sms.basis_saved", org_id=org_id, user_id=user.id,
                payload={"groups": len(group_ids), "members": len(member_ids)})
    db.commit()
    return RedirectResponse("/admin/einsatzinfo-sms?saved=1", status_code=303)


@router.post("/einsatzinfo-sms/stichwort/{alarm_type_id}")
async def einsatzinfo_sms_save_stichwort(
    alarm_type_id: int,
    request: Request,
    template_override: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    """Speichert Vorlagen-Override und Verteiler fuer ein einzelnes Stichwort."""
    user = request.state.user
    org_id = _require_org(user)

    at = db.get(AlarmType, alarm_type_id)
    if not at or at.org_id != org_id:
        raise HTTPException(status_code=404)

    # Vorlage am AlarmType speichern
    at.einsatzinfo_sms_template = template_override.strip() or None

    # Empfaenger-Eintraege ersetzen
    form = await request.form()
    group_ids = {int(v) for k, v in form.multi_items() if k == "group_id"}
    member_ids = {int(v) for k, v in form.multi_items() if k == "member_id"}

    db.query(SmsEinsatzinfoRecipient).filter(
        SmsEinsatzinfoRecipient.org_id == org_id,
        SmsEinsatzinfoRecipient.alarm_type_id == alarm_type_id,
    ).delete()

    for gid in group_ids:
        db.add(SmsEinsatzinfoRecipient(org_id=org_id, alarm_type_id=alarm_type_id, group_id=gid))
    for mid in member_ids:
        db.add(SmsEinsatzinfoRecipient(org_id=org_id, alarm_type_id=alarm_type_id, member_id=mid))

    write_audit(db, "admin.einsatzinfo_sms.stichwort_saved", org_id=org_id, user_id=user.id,
                entity_type="alarm_type", entity_id=alarm_type_id,
                payload={"groups": len(group_ids), "members": len(member_ids)})
    db.commit()
    return RedirectResponse(f"/admin/einsatzinfo-sms?saved=1#stichwort-{alarm_type_id}", status_code=303)


# ── Manueller SMS-Versand ─────────────────────────────────────────────────────

@router.get("/sms-senden", response_class=HTMLResponse)
async def sms_send_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    user = request.state.user
    org_id = _require_org(user)

    from app.routers.ws import is_sms_gateway_connected
    groups = _sms_groups_for_org(db, org_id)
    members = _active_members(db, org_id)
    sms_logs = (
        db.query(SmsLog)
        .filter(SmsLog.org_id == org_id)
        .order_by(SmsLog.sent_at.desc())
        .limit(30)
        .all()
    )
    return templates.TemplateResponse(request, "admin/sms_send.html", {
        "user": user,
        "groups": groups,
        "members": members,
        "sms_logs": sms_logs,
        "gateway_connected": is_sms_gateway_connected(org_id),
        "sent": request.query_params.get("sent"),
        "error": request.query_params.get("error"),
    })


@router.post("/sms-senden/senden")
async def sms_send_execute(
    request: Request,
    text: str = Form(...),
    target_type: str = Form("group"),  # "group" | "member" | "adhoc"
    db: Session = Depends(get_db),
    _=Depends(require_role("admin")),
):
    """Sendet eine manuelle SMS an Gruppen, Mitglieder oder Ad-hoc-Nummer."""
    user = request.state.user
    org_id = _require_org(user)
    text = text.strip()
    if not text:
        return RedirectResponse("/admin/sms-senden?error=empty", status_code=303)

    from app.routers.ws import is_sms_gateway_connected
    if not is_sms_gateway_connected(org_id):
        return RedirectResponse("/admin/sms-senden?error=no_gateway", status_code=303)

    form = await request.form()

    # Empfaenger aus Formular zusammenstellen
    import re as _re
    _strip_re = _re.compile(r"[\s\-\(\)]")

    phones: dict[str, str] = {}  # normalisierte Nummer → Anzeigename

    if target_type == "adhoc":
        adhoc_raw = (form.get("adhoc_number") or "").strip()
        if adhoc_raw:
            norm = _strip_re.sub("", adhoc_raw)
            phones[norm] = adhoc_raw
    else:
        group_ids = [int(v) for k, v in form.multi_items() if k == "group_id"]
        member_ids = [int(v) for k, v in form.multi_items() if k == "member_id"]

        # Gruppen expandieren
        if group_ids:
            groups = db.query(SmsGroup).filter(
                SmsGroup.id.in_(group_ids), SmsGroup.org_id == org_id
            ).all()
            for grp in groups:
                for gm in grp.members:
                    m = gm.member
                    if m and m.active and m.phone:
                        norm = _strip_re.sub("", m.phone.strip())
                        if norm:
                            phones[norm] = m.full_name

        # Einzelne Mitglieder
        if member_ids:
            mems = db.query(Member).filter(
                Member.id.in_(member_ids), Member.org_id == org_id, Member.active.is_(True)
            ).all()
            for m in mems:
                if m.phone:
                    norm = _strip_re.sub("", m.phone.strip())
                    if norm:
                        phones[norm] = m.full_name

    if not phones:
        return RedirectResponse("/admin/sms-senden?error=no_recipients", status_code=303)

    from app.services.sms_dispatch_service import send_bulk
    from app.core.audit import write_audit

    jobs = [(phone, text) for phone in phones]
    total, success = await send_bulk(org_id, jobs)

    # Protokollieren
    log_entry = SmsLog(
        org_id=org_id,
        sent_at=datetime.now(UTC),
        source="manual",
        alarm_type_code=None,
        text=text,
        recipient_count=total,
        success_count=success,
        triggered_by_user_id=user.id,
    )
    db.add(log_entry)
    write_audit(db, "admin.sms.manual_send", org_id=org_id, user_id=user.id,
                payload={"recipient_count": total, "success_count": success, "target_type": target_type})
    db.commit()

    return RedirectResponse(f"/admin/sms-senden?sent={success}", status_code=303)
