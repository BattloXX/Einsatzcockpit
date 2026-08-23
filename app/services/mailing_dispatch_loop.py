"""Asynchrone Verarbeitung der Mailing-Warteschlange."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from types import SimpleNamespace

import httpx
from sqlalchemy import func, or_

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.mailing import MailingCampaign, MailingConfig, MailingQueueItem, MailingSuppressionEntry
from app.services.mail_service import _org_smtp_cfg, _send
from app.services.mailing_render import render_template
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
    msg = EmailMessage()
    msg["To"] = item.email
    msg["Subject"] = subject
    reply_to = campaign.reply_to_override or cfg.reply_to_default
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text or "")
    msg.add_alternative(html, subtype="html")
    return msg


async def dispatch_once(db=None, *, batch_size=BATCH_SIZE):
    owns = db is None
    if owns:
        db = SessionLocal()
        set_tenant_context(db, None)
    try:
        attachment_cache = {}
        processed = 0
        campaign_ids: set[int] = set()
        for _ in range(batch_size):
            now = _now()
            # Every Gunicorn worker runs a dispatch loop. Claim exactly one row
            # under a DB lock and commit the "sending" state before doing any
            # network I/O, so another worker cannot process the same recipient.
            item = (
                db.query(MailingQueueItem)
                .filter(
                    MailingQueueItem.status == "queued",
                    or_(MailingQueueItem.next_attempt_at.is_(None), MailingQueueItem.next_attempt_at <= now),
                )
                .execution_options(include_all_tenants=True)
                .order_by(MailingQueueItem.id)
                .with_for_update(skip_locked=True)
                .first()
            )
            if item is None:
                break
            processed += 1
            campaign_ids.add(item.campaign_id)

            # Sperrlisten-Check zuerst, noch unter der Zeilensperre aus dem
            # SELECT ... FOR UPDATE oben, aber VOR status="sending"/attempt_count+=1 -
            # ein unterdrückter Empfänger darf nie wie ein Versandversuch aussehen.
            suppression = (
                db.query(MailingSuppressionEntry)
                .filter(
                    MailingSuppressionEntry.org_id == item.org_id, MailingSuppressionEntry.email == item.email.lower()
                )
                .first()
            )
            if suppression:
                item.status = "suppressed"
                item.error_message = f"Unterdrückt (Sperrliste, Grund: {suppression.reason})"
                _update_campaign_counts(db, item.campaign)
                db.commit()
                continue

            item.status = "sending"
            item.attempt_count += 1
            db.commit()
            cfg = db.query(MailingConfig).filter(MailingConfig.org_id == item.org_id).first()
            try:
                smtp_cfg = _org_smtp_cfg(db, item.org_id)
                effective_cfg = cfg or SimpleNamespace(reply_to_default=None)
                msg = _message(item, effective_cfg)
                resend_error = None
                if cfg and cfg.is_fully_configured:
                    from_addr = cfg.from_addr
                    if cfg.sender_display_name:
                        from_addr = f"{cfg.sender_display_name} <{cfg.from_addr}>"
                    from app.services.mailing_attachments import resend_attachments

                    try:
                        item.resend_message_id = await send_via_resend(
                            msg, mailing_api_key(cfg), from_addr, resend_attachments(item.campaign, attachment_cache)
                        )
                    except (ResendMailError, httpx.HTTPError) as exc:
                        resend_error = exc
                        if not smtp_cfg:
                            raise
                        msg["From"] = smtp_cfg["from_addr"]
                        try:
                            await _send(msg, smtp_cfg)
                        except Exception as smtp_exc:
                            raise RuntimeError(
                                f"Resend fehlgeschlagen: {exc}; SMTP-Fallback fehlgeschlagen: {smtp_exc}"
                            ) from smtp_exc
                elif smtp_cfg:
                    msg["From"] = smtp_cfg["from_addr"]
                    await _send(msg, smtp_cfg)
                else:
                    raise RuntimeError("Mailing-Konfiguration unvollstaendig")
                item.status = "sent"
                item.sent_at = _now()
                item.error_message = (
                    f"Resend fehlgeschlagen ({resend_error}); SMTP-Fallback verwendet" if resend_error else None
                )
            except Exception as exc:
                item.error_message = str(exc)[:2000]
                if item.attempt_count >= item.max_attempts:
                    item.status = "failed"
                else:
                    item.status = "queued"
                    item.next_attempt_at = _now() + timedelta(minutes=min(2**item.attempt_count, 60))
            _update_campaign_counts(db, item.campaign)
            db.commit()

        # Per-item aggregate updates can observe 29/30 while another worker is
        # committing the final recipient. The worker that finishes last always
        # performs this serialized, fresh-session reconciliation and therefore
        # leaves the campaign counters/status at the terminal DB state.
        for campaign_id in campaign_ids:
            reconcile_db = SessionLocal()
            set_tenant_context(reconcile_db, None)
            try:
                campaign = (
                    reconcile_db.query(MailingCampaign)
                    .execution_options(include_all_tenants=True)
                    .filter(MailingCampaign.id == campaign_id)
                    .with_for_update()
                    .one()
                )
                _update_campaign_counts(reconcile_db, campaign)
                reconcile_db.commit()
            finally:
                reconcile_db.close()
        return processed
    finally:
        if owns:
            db.close()


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
