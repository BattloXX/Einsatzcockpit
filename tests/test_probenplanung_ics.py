"""RFC-5545-Protokoll und Updates über echte Speicherrouten."""
from datetime import UTC, date, datetime
from icalendar import Calendar

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.teilnahme import Termin
from tests.test_probenplanung_checkliste import _login, _user
from tests.test_probenplanung_public import public_setup


def _next_calendar_year() -> int:
    """Die öffentlichen Feeds enthalten bewusst nur kommende Termine."""
    return datetime.now(UTC).year + 1


def _event(client, plain):
    r = client.get(f"/p/probenplan/{plain}.ics")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/calendar; charset=utf-8"
    assert r.headers["content-disposition"] == 'inline; filename="probenplan.ics"'
    return Calendar.from_ical(r.content).walk("VEVENT")[0], r.content


def test_uid_sequence_last_modified_und_absage(client):
    year = _next_calendar_year()
    plain, tid, _, art = public_setup()
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(Termin, tid).geaendert_am = datetime(2020, 1, 1)
        db.commit()
    first, _ = _event(client, plain)
    _user("ics_editor", "probenverwalter")
    csrf = _login(client, "ics_editor")
    values = {"_csrf": csrf, "titel": "Freigegebene Probe", "probeart_id": art,
              "beginn": f"{year}-07-10T21:00", "public_sichtbar": "1"}
    r = client.post(f"/probenplanung/{tid}/bearbeiten", data=values, follow_redirects=False)
    assert r.status_code == 303
    second, raw = _event(client, plain)
    assert first["UID"] == second["UID"]
    assert int(second["SEQUENCE"]) == int(first["SEQUENCE"]) + 1
    assert second.decoded("LAST-MODIFIED") > first.decoded("LAST-MODIFIED")
    assert f"DTSTART:{year}0710T190000Z".encode() in raw
    assert abs((second.decoded("DTSTAMP") - datetime.now(UTC)).total_seconds()) < 10
    # Identisches Speichern erzeugt keine unnötige Revision.
    assert client.post(f"/probenplanung/{tid}/bearbeiten", data=values, follow_redirects=False).status_code == 303
    unchanged, _ = _event(client, plain)
    assert unchanged["SEQUENCE"] == second["SEQUENCE"]
    assert client.post(f"/probenplanung/{tid}/status", data={"_csrf": csrf, "neuer_status": "abgesagt"},
                       follow_redirects=False).status_code == 303
    cancelled, _ = _event(client, plain)
    assert cancelled["STATUS"] == "CANCELLED"
    assert cancelled["UID"] == first["UID"]
    assert int(cancelled["SEQUENCE"]) == int(second["SEQUENCE"]) + 1


def test_sommer_winter_utc_und_ende(client):
    year = _next_calendar_year()
    for month, utc_hour in [(7, 18), (1, 19)]:
        plain, tid, _, _ = public_setup()
        with SessionLocal() as db:
            set_tenant_context(db, None)
            t = db.get(Termin, tid)
            t.beginn = datetime(year, month, 10, utc_hour)
            t.ende = datetime(year, month, 10, utc_hour + 1)
            db.commit()
        event, raw = _event(client, plain)
        assert event.decoded("DTSTART") == datetime(year, month, 10, utc_hour, tzinfo=UTC)
        assert event.decoded("DTEND") == datetime(year, month, 10, utc_hour + 1, tzinfo=UTC)
        assert f"DTSTART:{year}{month:02}10T{utc_hour:02}0000Z".encode() in raw
        assert "20:00" in client.get(f"/p/probenplan/{plain}").text
        assert event["STATUS"] == "CONFIRMED"


def test_ganztag_date_und_exklusives_ende(client):
    year = _next_calendar_year()
    plain, tid, _, _ = public_setup(ganztaegig=True)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(Termin, tid).beginn = datetime(year, 7, 9, 22)
        db.commit()
    event, raw = _event(client, plain)
    assert f"DTSTART;VALUE=DATE:{year}0710".encode() in raw
    assert f"DTEND;VALUE=DATE:{year}0711".encode() in raw
    assert event.decoded("DTSTART") == date(year, 7, 10)


def test_summary_escaping_und_utf8_zeilenfaltung(client):
    thema = "Öl, Wasser; Übung\nNächste Zeile " + "Ä" * 120
    plain, _, _, _ = public_setup(thema=thema, info="Info,mit;Zeichen\nZeile",
                                   public_info_sichtbar=True)
    event, raw = _event(client, plain)
    assert str(event["SUMMARY"]).endswith(" - " + thema)
    assert b"\\," in raw and b"\\;" in raw and b"\\n" in raw
    assert all(len(line) <= 75 for line in raw.split(b"\r\n"))
    assert str(event["DESCRIPTION"]) == "Info,mit;Zeichen\nZeile"
