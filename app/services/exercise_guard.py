"""Zentrale Freigabe externer Aktionen bei Übungseinsätzen."""
from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from app.models.master import OrgSettings

ExerciseChannel = Literal[
    "push",
    "ws_alarm",
    "nachbar",
    "lis_status",
    "wordpress",
    "geocoding",
    "sms",
    "teams",
    "autoprint",
    "objekt_kontakt",
]

_ORG_FLAGS = {
    "push": "uebung_push_erlaubt",
    "ws_alarm": "uebung_ws_alarm_erlaubt",
    "nachbar": "uebung_nachbar_einladung_erlaubt",
    "lis_status": "uebung_lis_status_erlaubt",
    "wordpress": "uebung_wordpress_bericht_erlaubt",
    "geocoding": "uebung_geocoding_erlaubt",
    "sms": "einsatzinfo_sms_send_exercise",
}


def darf_extern(
    kanal: ExerciseChannel,
    *,
    is_exercise: bool,
    org_id: int | None,
    db: Session,
) -> bool:
    """Gibt externe Aktionen für Realeinsätze unverändert frei.

    Fehlende Organisationen bzw. Einstellungen sind bei Übungen fail-closed. Die
    kontextabhängigen Bestandskanäle Teams, Autodruck und Objekt-Kontakte behalten
    ihre detaillierte Prüfung an der Versandstelle; der zentrale Guard bestätigt
    dort nur, dass keine neue globale Sperre hinzugekommen ist.
    """
    if not is_exercise:
        return True
    if org_id is None:
        return False
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if kanal in _ORG_FLAGS:
        if settings is None:
            return False
        return bool(getattr(settings, _ORG_FLAGS[kanal]))
    if kanal == "teams":
        from app.models.teams_bot import TeamsAlarmConfig

        config = db.query(TeamsAlarmConfig).filter(TeamsAlarmConfig.org_id == org_id).first()
        return bool(config and config.enabled and config.send_exercise)
    if kanal == "autoprint":
        from app.models.gateway import PrintRule

        rules = db.query(PrintRule).filter(
            PrintRule.org_id == org_id,
            PrintRule.aktiv == True,  # noqa: E712
        )
        return any((rule.filters or {}).get("uebung", "alle") != "nur_echt" for rule in rules)
    if kanal == "objekt_kontakt":
        from app.models.objekt import Objekt

        return db.query(Objekt.id).filter(
            Objekt.org_id == org_id,
            Objekt.kontakt_info_uebung == True,  # noqa: E712
        ).first() is not None
    raise ValueError(f"Unbekannter Exercise-Guard-Kanal: {kanal}")
