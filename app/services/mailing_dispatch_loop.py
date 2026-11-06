"""Asynchrone Verarbeitung der Mailing-Warteschlange."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from types import SimpleNamespace

import httpx
from sqlalchemy import func, or_

from app.config import settings
from app.core.security import sign_mailing_track_token
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.mailing import MailingCampaign, MailingConfig, MailingQueueItem, MailingSuppressionEntry
from app.services.loop_thread_io import im_db_thread
from app.services.mail_service import _org_smtp_cfg, _send
from app.services.mailing_render import append_unsubscribe_footer, render_template
from app.services.mailing_service import mailing_api_key
from app.services.resend_mail_service import ResendMailError, send_via_resend

logger = logging.getLogger("einsatzleiter.mailing")
INTERVAL_SECONDS = 15
BATCH_SIZE = 20


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


def _message(item, cfg):
    campaign = item.campaign
    tpl = campaign.template
    parts = (item.display_name or "").split(" ", 1)
    ctx = {
        "vorname": parts[0] if parts else "",
        "nachname": parts[1] if len(parts) > 1 else "",
        "email": item.email,
        "empfaenger_name": item.display_name or item.email,
        "org_name": "",
    }
    subject, html, text = render_template(campaign.subject_override or tpl.subject, tpl.body_html, tpl.body_text, ctx)
    from app.services.mailing_tracking import rewrite_links

    html = rewrite_links(
        html, item.id, item.org_id, track_opens=campaign.track_opens, track_clicks=campaign.track_clicks
    )
    unsub_url = (
        f"{settings.effective_public_base_url.rstrip('/')}/mailing/u/"
        f"{sign_mailing_track_token(item.id, item.org_id)}"
    )
    html, text = append_unsubscribe_footer(html, text, unsub_url)
    msg = EmailMessage()
    msg["To"] = item.email
    msg["Subject"] = subject
    reply_to = campaign.reply_to_override or cfg.reply_to_default
    if reply_to:
        msg["Reply-To"] = reply_to
    contact = cfg.reply_to_default or getattr(cfg, "from_addr", None)
    msg["List-Unsubscribe"] = (
        f"<mailto:{contact}?subject=unsubscribe>, <{unsub_url}>" if contact else f"<{unsub_url}>"
    )
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text or "")
    msg.add_alternative(html, subtype="html")
    return msg


def _prepare_item_sync(db, attachment_cache):
    now = _now()
    item = (
        db.query(MailingQueueItem)
        .filter(MailingQueueItem.status == "queued",
                or_(MailingQueueItem.next_attempt_at.is_(None), MailingQueueItem.next_attempt_at <= now))
        .execution_options(include_all_tenants=True)
        .order_by(MailingQueueItem.id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if item is None:
        return None
    campaign_id, item_id = item.campaign_id, item.id
    suppression = db.query(MailingSuppressionEntry).filter(
        MailingSuppressionEntry.org_id == item.org_id, MailingSuppressionEntry.email == item.email.lower()
    ).first()
    if suppression:
        item.status = "suppressed"
        item.error_message = f"Unterdrückt (Sperrliste, Grund: {suppression.reason})"
        _update_campaign_counts(db, item.campaign)
        db.commit()
        return item_id, campaign_id, None
    item.status = "sending"
    item.attempt_count += 1
    db.commit()
    cfg = db.query(MailingConfig).filter(MailingConfig.org_id == item.org_id).first()
    try:
        smtp_cfg = _org_smtp_cfg(db, item.org_id)
        msg = _message(item, cfg or SimpleNamespace(reply_to_default=None))
        resend = None
        if cfg and cfg.is_fully_configured:
            from app.services.mailing_attachments import resend_attachments

            from_addr = cfg.from_addr
            if cfg.sender_display_name:
                from_addr = f"{cfg.sender_display_name} <{cfg.from_addr}>"
            resend = (mailing_api_key(cfg), from_addr, resend_attachments(item.campaign, attachment_cache))
        return item_id, campaign_id, (msg, smtp_cfg, resend)
    except Exception as exc:
        _finish_item_sync(db, item_id, None, str(exc)[:2000], None)
        return item_id, campaign_id, None


def _finish_item_sync(db, item_id, resend_id, error, fallback):
    item = db.get(MailingQueueItem, item_id)
    if resend_id is not None:
        item.resend_message_id = resend_id
    if error is None:
        item.status = "sent"
        item.sent_at = _now()
        item.error_message = fallback
    else:
        item.error_message = error
        if item.attempt_count >= item.max_attempts:
            item.status = "failed"
        else:
            item.status = "queued"
            item.next_attempt_at = _now() + timedelta(minutes=min(2**item.attempt_count, 60))
    _update_campaign_counts(db, item.campaign)
    db.commit()


def _in_session_sync(funktion, *args):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return funktion(db, *args)
    finally:
        db.close()


def _reconcile_sync(db, campaign_id):
    campaign = (db.query(MailingCampaign).execution_options(include_all_tenants=True)
                .filter(MailingCampaign.id == campaign_id).with_for_update().one())
    _update_campaign_counts(db, campaign)
    db.commit()


async def dispatch_once(db=None, *, batch_size=BATCH_SIZE):
    async def db_schritt(funktion, *args):
        # Explizite Aufrufer-Sessions bleiben bei ihrem Besitzer (z. B. Routen/Tests).
        if db is not None:
            return funktion(db, *args)
        return await im_db_thread(_in_session_sync, funktion, *args)

    attachment_cache = {}
    processed = 0
    campaign_ids: set[int] = set()
    for _ in range(batch_size):
        prepared = await db_schritt(_prepare_item_sync, attachment_cache)
        if prepared is None:
            break
        item_id, campaign_id, transport = prepared
        processed += 1
        campaign_ids.add(campaign_id)
        if transport is None:
            continue
        msg, smtp_cfg, resend = transport
        resend_id = error = fallback = None
        try:
            if resend:
                try:
                    resend_id = await send_via_resend(msg, *resend)
                except (ResendMailError, httpx.HTTPError) as exc:
                    if not smtp_cfg:
                        raise
                    msg["From"] = smtp_cfg["from_addr"]
                    try:
                        await _send(msg, smtp_cfg)
                    except Exception as smtp_exc:
                        raise RuntimeError(
                            f"Resend fehlgeschlagen: {exc}; SMTP-Fallback fehlgeschlagen: {smtp_exc}"
                        ) from smtp_exc
                    fallback = f"Resend fehlgeschlagen ({exc}); SMTP-Fallback verwendet"
            elif smtp_cfg:
                msg["From"] = smtp_cfg["from_addr"]
                await _send(msg, smtp_cfg)
            else:
                raise RuntimeError("Mailing-Konfiguration unvollstaendig")
        except Exception as exc:
            error = str(exc)[:2000]
        await db_schritt(_finish_item_sync, item_id, resend_id, error, fallback)
    # Frische Session und Zeilensperre: letzter Worker gleicht alle Zaehler ab.
    for campaign_id in campaign_ids:
        await im_db_thread(_in_session_sync, _reconcile_sync, campaign_id)
    return processed


def _update_campaign_counts(db, campaign):
    db.flush()
    counts = dict(
        db.query(MailingQueueItem.status, func.count(MailingQueueItem.id))
        .filter(MailingQueueItem.campaign_id == campaign.id)
        .group_by(MailingQueueItem.status)
        .all()
    )
    campaign.sent_count = counts.get("sent", 0) + counts.get("delivered", 0)
    campaign.failed_count = counts.get("failed", 0) + counts.get("bounced", 0)
    campaign.suppressed_count = counts.get("suppressed", 0)
    if campaign.sent_count + campaign.failed_count + campaign.suppressed_count >= campaign.total_count:
        campaign.status = "sent" if campaign.failed_count == 0 else "failed"
        campaign.sent_at = _now()
    else:
        campaign.status = "sending"


async def mailing_dispatch_loop():
    while True:
        try:
            await dispatch_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mailing-Dispatch fehlgeschlagen")
        await asyncio.sleep(INTERVAL_SECONDS)
