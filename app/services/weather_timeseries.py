"""Reine Auswertung stündlicher Zeitreihen mit UTC-Zeitstempeln.

Intervallwerte gelten für [t-1h, t]. Insbesondere Open-Meteo precipitation
(timeformat=iso8601, timezone=UTC) ist die Summe der VORANGEGANGENEN Stunde:
https://open-meteo.com/en/docs . Naive Zeitangaben sind hier nicht zulässig.
"""
from datetime import UTC, datetime, timedelta

HourlySeries = list[tuple[datetime, float | None]]
_HOUR = timedelta(hours=1)


def utc(value: datetime) -> datetime:
    """Normalisiert einen bewussten Zeitpunkt nach UTC; naive Zeiten ablehnen."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Zeitpunkt muss eine Zeitzone enthalten")
    return value.astimezone(UTC)


def _window(series: HourlySeries, ref: datetime, hours: float) -> tuple[HourlySeries, bool]:
    start = utc(ref)
    if hours < 0:
        raise ValueError("Fenster darf nicht negativ sein")
    if hours == 0:
        return [], True
    end = start + timedelta(hours=hours)
    rows = sorted(((utc(t), v) for t, v in series), key=lambda row: row[0])
    selected = [(t, v) for t, v in rows if t > start and t - _HOUR < end]
    cursor = start
    complete = True
    for t, value in selected:
        left, right = max(start, t - _HOUR), min(end, t)
        if left != cursor or value is None:
            complete = False
        cursor = right
    return selected, complete and cursor == end


def window_sum(series: HourlySeries, ref: datetime, hours: float) -> tuple[float | None, bool]:
    """Summe in [ref, ref+hours], Teilstunden proportional (gleichmässige Rate).

    Jede Stunde muss lückenlos und genau einmal vorliegen. None, Lücken,
    Überlappungen und Duplikate ergeben (None, False), niemals eine Teilsumme.
    Ein leeres Fenster liefert (0, True).
    """
    rows, complete = _window(series, ref, hours)
    if not complete:
        return None, False
    start = utc(ref)
    end = start + timedelta(hours=hours)
    total = sum(v * ((min(t, end) - max(t - _HOUR, start)) / _HOUR)
                for t, v in rows if v is not None)
    return total, True


def window_max(series: HourlySeries, ref: datetime, hours: float) -> tuple[float | None, datetime | None, bool]:
    """Maximum über überlappende Stunden; Zeitpunkt = Intervallende in UTC.

    Teilstunden werden ungewichtet berücksichtigt. Bei Lücken bleibt das bekannte
    Maximum mit complete=False sichtbar; bei Gleichstand gewinnt der früheste Wert.
    """
    rows, complete = _window(series, ref, hours)
    valid = [(t, v) for t, v in rows if v is not None]
    if not valid:
        return None, None, complete
    at, value = max(valid, key=lambda row: row[1])
    return value, at, complete


def accumulated_to_interval(series: HourlySeries) -> HourlySeries:
    """Differenziert rr_acc; erster Wert, Reset, Lücke oder None -> None.

    Ein Reset wird nicht als Niederschlag interpretiert. Erst die nächste
    stündliche Differenz ist wieder nutzbar; keine Verteilung über Datenlücken.
    """
    result: HourlySeries = []
    previous_t: datetime | None = None
    previous: float | None = None
    for raw_t, value in sorted(series, key=lambda row: utc(row[0])):
        t = utc(raw_t)
        delta = None
        if (previous_t is not None and t - previous_t == _HOUR
                and previous is not None and value is not None and value >= previous >= 0):
            delta = value - previous
        result.append((t, delta))
        previous_t, previous = t, value
    return result


def value_at(series: HourlySeries, at: datetime) -> float | None:
    """Punktwert am nächsten Stundenzeitpunkt ab at, maximal eine Stunde entfernt."""
    target = utc(at)
    rows = [(utc(t), v) for t, v in series if target <= utc(t) < target + _HOUR]
    return min(rows, key=lambda row: row[0])[1] if rows else None
