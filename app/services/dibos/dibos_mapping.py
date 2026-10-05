"""Abbildung bestaetigter DIBOS-Wachenstatus auf interne Werte."""

WACHE_STATUS_VALUES = ["einsatzbereit", "alarmiert", "übernommen", "ausgefahren", "am_einsatzort"]

_DIBOS_WACHE_STATUS_MAP = {
    "AL": "alarmiert",
    "UEB": "übernommen",
    "S2": "einsatzbereit",
    "S4": "ausgefahren",
    "S5": "am_einsatzort",
}

_DIBOS_UNIT_STATUS_MAP = {"S4": "Einsatz übernommen", "S5": "Am Einsatzort"}


def map_dibos_unit_status(status_text: str | None) -> str | None:
    """DIBOS kennt fuer Fahrzeugkarten nur die belastbaren S4/S5-Werte."""
    return _DIBOS_UNIT_STATUS_MAP.get(status_text.strip().upper()) if status_text else None


def map_wache_status(status_text: str | None) -> str | None:
    """Mappt nur im echten DIBOS-Katalog bestaetigte Wachenstatus."""
    if not status_text:
        return None
    return _DIBOS_WACHE_STATUS_MAP.get(status_text.strip().upper())
