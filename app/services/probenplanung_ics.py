"""RFC-5545-Ausgabe ausschließlich aus freigegebenen Wertobjekten."""
from datetime import UTC, datetime, timedelta

from icalendar import Calendar, Event

from app.services.probenplanung_public import OeffentlicherKalendereintrag


def probenplan_ics(eintraege: tuple[OeffentlicherKalendereintrag, ...], host: str) -> bytes:
    kalender = Calendar()
    kalender.add("prodid", "-//Einsatzcockpit//Probenplan//DE")
    kalender.add("version", "2.0")
    for eintrag in eintraege:
        p = eintrag.probe
        event = Event()
        event.add("uid", f"probe-{eintrag.uid}@{host}")
        event.add("dtstamp", datetime.now(UTC))
        event.add("last-modified", eintrag.last_modified)
        event.add("sequence", eintrag.sequence)
        if p.ganztaegig:
            event.add("dtstart", p.datum)
            # DATE-Ende ist exklusiv; mindestens einen ganzen Tag ausliefern.
            event.add("dtend", max(p.ende.date() if p.ende else p.datum, p.datum + timedelta(days=1)))
        else:
            event.add("dtstart", p.beginn.astimezone(UTC))
            if p.ende:
                event.add("dtend", p.ende.astimezone(UTC))
        event.add("status", "CANCELLED" if p.status_abgesagt else "CONFIRMED")
        event.add("summary", f"{p.probeart_name} - {p.thema or ''}")
        if p.info:
            event.add("description", p.info)
        if p.ort:
            event.add("location", p.ort)
        kalender.add_component(event)
    return kalender.to_ical()
