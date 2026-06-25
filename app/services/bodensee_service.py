"""Bodensee-Seewassertemperatur für Lake-Effekt-Regel.

Priorität:
  1. Manueller Override in OrgSettings (gültig 14 Tage)
  2. Optionaler externer Adapter (Feature-Flag BODENSEE_TEMP_FETCH_ENABLED, default False)
  3. Klimatologie-Fallback (Monatstabelle Bodensee-Oberfläche)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("einsatzleiter.bodensee")

# Monatliche Klimatologie-Mittelwerte Bodensee-Oberflächentemperatur (°C, Jan–Dez)
_KLIMATOLOGIE: dict[int, float] = {
    1: 5.0, 2: 4.5, 3: 5.5, 4: 9.0, 5: 14.0, 6: 19.0,
    7: 22.0, 8: 22.0, 9: 19.0, 10: 14.0, 11: 10.0, 12: 7.0,
}

_OVERRIDE_MAX_AGE_DAYS = 14


def get_surface_temp_c(org_settings, db=None) -> float:
    """Gibt die Bodensee-Oberflächentemperatur zurück.

    Reihenfolge: manueller Override → externer Adapter (deaktiviert) → Klimatologie.
    Wirft nie eine Exception – Klimatologie ist immer verfügbar.
    """
    # 1. Manueller Override
    if org_settings is not None:
        override_c = org_settings.bodensee_temp_override_c
        override_at = org_settings.bodensee_temp_override_at
        if override_c is not None and override_at is not None:
            age = datetime.now(UTC) - override_at.replace(tzinfo=UTC)
            if age <= timedelta(days=_OVERRIDE_MAX_AGE_DAYS):
                logger.debug("Bodensee: manueller Override %.1f °C (Alter %s)", override_c, age)
                return override_c

    # 2. Externer Adapter (Stub – nur wenn Flag gesetzt)
    from app.config import settings
    if settings.BODENSEE_TEMP_FETCH_ENABLED and settings.BODENSEE_TEMP_SOURCE_URL:
        temp = _fetch_external(settings.BODENSEE_TEMP_SOURCE_URL)
        if temp is not None:
            return temp

    # 3. Klimatologie-Fallback
    month = datetime.now(UTC).month
    temp = _KLIMATOLOGIE[month]
    logger.debug("Bodensee: Klimatologie-Fallback Monat %d → %.1f °C", month, temp)
    return temp


def _fetch_external(url: str) -> float | None:
    """Stub für externen Temperatur-Adapter (LUBW / pegelonline Bregenz/Konstanz)."""
    # TODO: bei Bedarf implementieren (Netzwerk-Egress freischalten)
    return None
