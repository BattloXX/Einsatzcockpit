"""Teams-Bot-Erweiterung: proaktiver Kartenversand über einen echten Teams-Bot
(Zusagen/Absagen ohne Zwischenklick).

**Noch nicht implementiert** — dieser Teil braucht eine echte Azure-Bot-Registrierung und
muss laut Plan iterativ gegen einen echten Teams-Tenant verifiziert werden (JWKS/Issuer des
Bot Framework, Invoke-Response-Format für den Card-Refresh, ob E-Mail/UPN direkt im
Invoke-Payload steckt). Bis dahin bleibt `bot_enabled` in der Admin-UI zwar einschaltbar,
aber ohne eine über `POST /api/v1/teams/messages` eingefangene Kanalbindung
(`TeamsChannelBinding`) wird `teams_alarm_service.post_incident_card()` diesen Pfad nie
erreichen — jedes Ziel läuft transparent über den Webhook-Basis-Modus weiter.

Siehe Wiki "Administration-Teams-Alarmierung" Abschnitt "Erweiterter Modus" für den
geplanten Funktionsumfang.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.master import FireDept
from app.models.teams_bot import TeamsAlarmConfig, TeamsChannelBinding

logger = logging.getLogger("einsatzleiter.teams_bot_service")


async def post_incident_card_via_bot(
    db: Session, incident: Incident, cfg: TeamsAlarmConfig, binding: TeamsChannelBinding,
    *, base_url: str, org: FireDept | None,
) -> None:
    """Platzhalter — wird mit der Bot-Framework-Anbindung implementiert (Client-Credentials-
    Token gegen login.microsoftonline.com, Adaptive Card mit Action.Execute-Buttons, POST an
    `{binding.service_url}v3/conversations/{binding.conversation_id}/activities`)."""
    logger.warning(
        "Teams-Bot-Versand noch nicht implementiert (Einsatz %s, Org %s, Ziel %s) — "
        "kein Fallback ausgelöst, bitte Webhook-URL zusätzlich konfigurieren",
        incident.id, incident.primary_org_id, binding.target,
    )
