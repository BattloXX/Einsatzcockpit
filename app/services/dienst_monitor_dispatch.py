"""Versand und Protokollierung von Stoerungen und Entwarnungen."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.timezones import format_local_datetime
from app.models.dienst_monitor import DIENST_LABELS, DienstMonitorLog


async def dispatch_meldung(*, db, org_id: int, org, org_settings, row, check, art: str, base_url: str = "") -> bool:
    label = DIENST_LABELS.get(check.key, check.key)
    prefix = "Störung" if art == "stoerung" else "Entwarnung"
    betreff = (
        f"[{prefix}] {label} – {org.name}"
        if art == "stoerung"
        else f"[Entwarnung] {label} wieder erreichbar – {org.name}"
    )
    seit = row.down_since or row.since
    zeilen = [f"Organisation: {org.name}", f"Dienst: {label}"]
    if art == "stoerung":
        zeilen.append(f"Ausfall seit: {format_local_datetime(seit, org)}")
    elif seit:
        dauer = max(0, int((datetime.now(UTC).replace(tzinfo=None) - seit).total_seconds() // 60))
        zeilen.append(f"Ausfalldauer: {dauer} Minuten")
    zeilen.extend([f"Status: {prefix}", f"Detail: {check.detail}"])
    url = f"{base_url.rstrip('/')}/admin/systemstatus" if base_url else None
    if url:
        zeilen.append(f"Systemstatus: {url}")
    text = "\n".join(zeilen)
    erfolge = 0

    def log(kanal: str, empfaenger: str | None, status: str, fehler: str | None = None) -> None:
        db.add(
            DienstMonitorLog(
                org_id=org_id,
                key=check.key,
                state=art,
                kanal=kanal,
                empfaenger=(empfaenger or "")[:255] or None,
                betreff=betreff[:255],
                status=status,
                fehlertext=(fehler or "")[:500] or None,
                payload_excerpt=check.detail[:1000],
                gesendet_am=datetime.now(UTC).replace(tzinfo=None),
            )
        )

    mail = (org_settings.dienst_monitor_mail or "").strip()
    if mail:
        try:
            from app.services.mail_service import _build_message, _org_smtp_cfg, deliver, get_smtp_cfg

            smtp = _org_smtp_cfg(db, org_id) or get_smtp_cfg(db)
            nachricht = _build_message(
                to=mail, subject=betreff, body_txt=text, body_html=f"<pre>{text}</pre>", smtp_cfg=smtp
            )
            await deliver(db, org_id, nachricht, smtp)
            erfolge += 1
            log("mail", mail, "gesendet")
        except Exception as exc:
            log("mail", mail, "fehler", str(exc))
    teams = (org_settings.dienst_monitor_teams_webhook_url or "").strip()
    if teams:
        from app.services.teams_service import post_teams_karte

        ok = await post_teams_karte(teams, betreff, text, url)
        erfolge += int(ok)
        log("teams", teams, "gesendet" if ok else "fehler", None if ok else "Teams-Versand fehlgeschlagen")
    nummern = [n.strip() for n in (org_settings.dienst_monitor_sms or "").split(",") if n.strip()]
    for nummer in nummern:
        if check.key == "sms_gateway":
            log("sms", nummer, "uebersprungen", "SMS-Gateway ist der betroffene Dienst")
            continue
        try:
            from app.services.sms_service import send_sms

            result = await send_sms(org_id, nummer, f"{betreff}\n{check.detail}")
            erfolge += int(result.success)
            log(
                "sms",
                nummer,
                "gesendet" if result.success else "fehler",
                None if result.success else "SMS-Versand fehlgeschlagen",
            )
        except Exception as exc:
            log("sms", nummer, "fehler", str(exc))
    db.commit()
    return erfolge > 0
