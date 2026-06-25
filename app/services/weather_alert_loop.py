"""Background-Loop für Wetterwarnungen (alle 5 Minuten je Org).

Muster: task_reminder.py
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

logger = logging.getLogger("einsatzleiter.weather_alert_loop")


async def weather_alert_loop() -> None:
    from app.config import settings
    if not settings.WEATHER_ALERTS_ENABLED:
        logger.info("weather_alert_loop: deaktiviert (WEATHER_ALERTS_ENABLED=False)")
        return

    logger.info("weather_alert_loop gestartet (Intervall %ds)", settings.WEATHER_ALERT_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(settings.WEATHER_ALERT_INTERVAL_S)
            await _run_all_orgs()
        except asyncio.CancelledError:
            logger.info("weather_alert_loop beendet")
            break
        except Exception:
            logger.exception("weather_alert_loop: Iteration fehlgeschlagen")


async def _run_all_orgs() -> None:
    from app.config import settings
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import OrgSettings

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        # Alle Orgs mit Wetter-Integration aktiv (NULL = global True)
        orgs = [
            o for o in db.query(OrgSettings).all()
            if o.weather_enabled is not False
            and settings.WEATHER_ENABLED
        ]
    finally:
        db.close()

    base_url = (settings.PUBLIC_BASE_URL or settings.APP_BASE_URL or "").rstrip("/")
    for org_settings in orgs:
        try:
            await _process_org(org_settings, base_url)
        except Exception:
            logger.exception("weather_alert_loop: Org %s fehlgeschlagen", org_settings.org_id)


async def _process_org(org_settings, base_url: str) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.master import FireDept
    from app.models.weather_alert import WeatherAlertRule, WeatherAlertState
    from app.services.weather_alert_dispatch import dispatch_alert
    from app.services.weather_alert_service import (
        apply_state_machine,
        build_weather_picture,
        ensure_rules,
        evaluate_rule,
    )

    db = SessionLocal()
    set_tenant_context(db, None)
    org_id = org_settings.org_id
    try:
        # Default-Regeln anlegen (idempotent, nur wenn noch nicht vorhanden)
        ensure_rules(org_id, db)

        rules = (
            db.query(WeatherAlertRule)
            .filter(WeatherAlertRule.org_id == org_id, WeatherAlertRule.enabled == True)  # noqa: E712
            .execution_options(include_all_tenants=True)
            .all()
        )
        if not rules:
            return

        pic = await build_weather_picture(org_settings, db)

        org = db.query(FireDept).filter(FireDept.id == org_id).first()
        org_name = org.name if org else f"Org {org_id}"

        for rule in rules:
            try:
                result = evaluate_rule(rule, pic)

                state_row = (
                    db.query(WeatherAlertState)
                    .filter(
                        WeatherAlertState.org_id == org_id,
                        WeatherAlertState.key == rule.key,
                    )
                    .execution_options(include_all_tenants=True)
                    .first()
                )
                if state_row is None:
                    state_row = WeatherAlertState(
                        org_id=org_id, key=rule.key, state="none"
                    )
                    db.add(state_row)

                decision = apply_state_machine(rule, result, state_row)

                # Zustand aktualisieren
                if state_row.state != decision.new_state:
                    state_row.since = datetime.now(UTC)
                state_row.state = decision.new_state
                if result.payload_hash:
                    state_row.last_payload_hash = result.payload_hash
                if result.values:
                    try:
                        first_val = next(
                            v for v in result.values.values()
                            if isinstance(v, (int, float))
                        )
                        state_row.last_value = float(first_val)
                    except StopIteration:
                        pass

                db.commit()

                if decision.notify:
                    state_row.last_notified_at = datetime.now(UTC)
                    state_row.last_payload_hash = decision.payload_hash
                    db.commit()
                    await dispatch_alert(
                        rule=rule,
                        result=result,
                        decision=decision,
                        org_settings=org_settings,
                        org_name=org_name,
                        db=db,
                        base_url=base_url,
                    )
            except Exception:
                logger.exception("weather_alert_loop: Regel %s Org %s fehlgeschlagen",
                                 rule.key, org_id)
    finally:
        db.close()
