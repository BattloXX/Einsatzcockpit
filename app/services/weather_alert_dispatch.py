"""Wetterwarnung-Dispatch: Mail + MS Teams, mit Protokollierung in WeatherAlertLog."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.services.weather_alert_service import RULE_LABELS, Decision, RuleResult

logger = logging.getLogger("einsatzleiter.weather_alert_dispatch")

# GeoSphere-Pflichtattribution (CC BY 4.0)
_ATTRIBUTION = "Wetterdaten: GeoSphere Austria (CC BY 4.0)"

# Teams-Farben je Zustand
_THEME_COLORS = {
    "vorwarnung": "f59e0b",   # Orange
    "akut":       "dc2626",   # Rot
}


def _render_body(rule, result: RuleResult, org_name: str) -> tuple[str, str]:
    """Gibt (body_text, body_html) zurück."""
    label = RULE_LABELS.get(rule.key, rule.key)
    state_label = "VORWARNUNG" if result.state == "vorwarnung" else "WARNUNG AKTIV"

    lines = [
        f"Organisation: {org_name}",
        f"Regeltyp:     {label}",
        f"Stufe:        {state_label}",
        "",
        result.detail_de,
    ]
    if result.values:
        lines.append("")
        lines.append("Messwerte:")
        for k, v in result.values.items():
            if v is not None:
                lines.append(f"  {k}: {v}")
    lines += ["", _ATTRIBUTION]

    body_text = "\n".join(lines)
    body_html = (
        "<pre style='font-family:monospace;white-space:pre-wrap'>"
        + "\n".join(lines)
        + "</pre>"
    )
    return body_text, body_html


async def dispatch_alert(
    rule,
    result: RuleResult,
    decision: Decision,
    org_settings,
    org_name: str,
    db,
    base_url: str = "",
) -> None:
    """Versendet Wetterwarnung per Mail und/oder Teams und protokolliert das Ergebnis."""
    mail_to   = rule.mail_override   or (org_settings.weather_alert_mail   if org_settings else None)
    teams_url = rule.teams_webhook_override or (
        org_settings.weather_alert_teams_webhook_url if org_settings else None
    )

    if not mail_to and not teams_url:
        logger.debug("dispatch_alert: Regel %s – kein Empfänger konfiguriert", rule.key)
        return

    label   = RULE_LABELS.get(rule.key, rule.key)
    betreff = f"[Wetterwarnung] {label} – {org_name}"
    body_text, body_html = _render_body(rule, result, org_name)
    detail_url = f"{base_url}/wetter" if base_url else ""

    from app.models.weather_alert import WeatherAlertLog

    # ── Mail ──────────────────────────────────────────────────────────────────
    if rule.channel_mail and mail_to:
        try:
            from app.services.mail_service import _build_message, _send, get_smtp_cfg
            smtp_cfg = get_smtp_cfg()
            if detail_url:
                body_html += f'<p><a href="{detail_url}">Wetter-Panel öffnen</a></p>'
            msg = _build_message(
                to=mail_to,
                subject=betreff,
                body_txt=body_text,
                body_html=body_html,
                smtp_cfg=smtp_cfg,
            )
            await _send(msg, smtp_cfg)
            ok, err = True, None
        except Exception as exc:
            logger.error("dispatch_alert Mail-Fehler Regel %s: %s", rule.key, exc)
            ok, err = False, str(exc)[:500]
        db.add(WeatherAlertLog(
            org_id=rule.org_id,
            key=rule.key,
            state=result.state,
            kanal="mail",
            empfaenger=mail_to[:255],
            betreff=betreff[:255],
            status="gesendet" if ok else "fehler",
            fehlertext=err,
            payload_excerpt=result.detail_de[:1000],
            gesendet_am=datetime.now(UTC),
        ))

    # ── Teams ─────────────────────────────────────────────────────────────────
    if rule.channel_teams and teams_url:
        theme = _THEME_COLORS.get(result.state, "d42225")
        ok = _post_teams(teams_url, betreff, body_text, detail_url or None, theme)
        db.add(WeatherAlertLog(
            org_id=rule.org_id,
            key=rule.key,
            state=result.state,
            kanal="teams",
            empfaenger=teams_url[:255],
            betreff=betreff[:255],
            status="gesendet" if ok else "fehler",
            fehlertext=None if ok else "Teams-Post fehlgeschlagen",
            payload_excerpt=result.detail_de[:1000],
            gesendet_am=datetime.now(UTC),
        ))

    db.commit()


def _post_teams(webhook_url: str, titel: str, text: str, url: str | None, theme: str) -> bool:
    """Sendet Teams-Karte mit konfigurierter Farbe."""
    import httpx

    if not webhook_url or not webhook_url.startswith("https://"):
        logger.warning("Teams-Webhook-URL ungültig: %s", webhook_url)
        return False

    payload: dict = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": titel,
        "themeColor": theme,
        "sections": [{"activityTitle": titel, "activityText": text}],
    }
    if url:
        payload["potentialAction"] = [{
            "@type": "OpenUri",
            "name": "Öffnen",
            "targets": [{"os": "default", "uri": url}],
        }]
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=10.0)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Teams-Webhook-Fehler: %s", exc)
        return False
