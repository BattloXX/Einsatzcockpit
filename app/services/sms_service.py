"""Providerneutraler SMS-Dispatcher mit optionalem Fallback."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.services.eus_sms_service import EusConfig, EusSmsError, send_via_eus

logger = logging.getLogger("einsatzleiter.sms")


@dataclass
class SmsContext:
    org_id: int
    chain: list[str]
    eus: EusConfig | None
    providers_used: set[str] = field(default_factory=set)


def resolve_sms_config(org_id: int, db=None) -> SmsContext:
    from app.core.crypto import decrypt_secret
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.org_sms import OrgSmsConfig

    own_db = db is None
    if own_db:
        db = SessionLocal()
        set_tenant_context(db, None)
    try:
        cfg = db.query(OrgSmsConfig).filter(OrgSmsConfig.org_id == org_id).first()
        if not isinstance(cfg, OrgSmsConfig):
            return SmsContext(org_id, ["gateway"], None)
        chain = [cfg.primary_provider]
        if cfg.fallback_provider and cfg.fallback_provider != cfg.primary_provider:
            chain.append(cfg.fallback_provider)
        eus = None
        if settings.EUS_SMS_ENABLED and cfg.eus_enabled and cfg.is_eus_fully_configured:
            try:
                eus = EusConfig(
                    org_id=org_id, base_url=cfg.eus_base_url or "",
                    auth_mode=cfg.eus_auth_mode, client_id=cfg.eus_client_id or "",
                    client_secret=decrypt_secret(cfg.eus_client_secret_enc or ""),
                    timeout=cfg.eus_timeout,
                )
            except Exception:
                logger.exception("EUS-Secret fuer Org %s konnte nicht entschluesselt werden", org_id)
        if eus is None:
            chain = [name for name in chain if name != "eus"]
        chain = [name for name in chain if name in {"gateway", "eus"}]
        return SmsContext(org_id, chain or ["gateway"], eus)
    except Exception:
        logger.exception("SMS-Konfiguration fuer Org %s konnte nicht geladen werden", org_id)
        return SmsContext(org_id, ["gateway"], None)
    finally:
        if own_db:
            db.close()


def sms_available(org_id: int, db=None) -> bool:
    from app.routers.ws import is_sms_gateway_connected
    ctx = resolve_sms_config(org_id, db)
    return any(name == "eus" or (name == "gateway" and is_sms_gateway_connected(org_id))
               for name in ctx.chain)


async def send_sms(
    org_id: int, to: str, text: str, timeout: float = 15.0,
    ctx: SmsContext | None = None,
    preferred_gateway_token_id: int | None = None,
) -> bool:
    from app.routers.ws import dispatch_sms
    ctx = ctx or resolve_sms_config(org_id)
    for provider in ctx.chain:
        ctx.providers_used.add(provider)
        try:
            if provider == "gateway":
                if preferred_gateway_token_id is None:
                    result = await dispatch_sms(org_id, str(uuid.uuid4()), to, text, timeout=timeout)
                else:
                    result = await dispatch_sms(
                        org_id,
                        str(uuid.uuid4()),
                        to,
                        text,
                        timeout=timeout,
                        preferred_gateway_token_id=preferred_gateway_token_id,
                    )
                if not result.get("ok", False):
                    raise RuntimeError(str(result.get("error") or "Gateway-Fehler"))
            elif provider == "eus" and ctx.eus:
                await send_via_eus(ctx.eus, to, text)
            else:
                continue
            return True
        except (RuntimeError, EusSmsError, httpx.HTTPError) as exc:
            logger.warning("SMS-Provider %s fuer %s fehlgeschlagen: %s",
                           provider, _mask(to), exc)
    return False


def _mask(number: str) -> str:
    if len(number) >= 5:
        return number[:-4] + "****"
    return "****"
