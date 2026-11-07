"""Lesender ELVis-Abgleich: Nummern, Quellen, Deltas und Tenant-Grenzen."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event as sql_event

from app.core.tenant import set_tenant_context
from app.models.gateway import AlarmIngest, Gateway
from app.models.incident import Incident
from app.models.master import FireDept
from app.services.elvis_event_service import vergleiche_elvis_events
from tests.test_alarm_apply_service import db as db


def _event(db, nr="f26000001", *, gw=None, test=False, seconds=0):
    gw = gw or db.info["gateway"]
    row = AlarmIngest(
        org_id=gw.org_id, gateway_id=gw.id, source="elvis", raw_hash=uuid4().hex,
        raw_text="ELVis", parsed={"elvis_args": {"Nr": nr}, "elvis_test": test},
        received_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=seconds),
        process_status="logged",
    )
    db.add(row)
    db.flush()
    return row


def _incident(db, nr="f26000001", *, org_id=None, seconds=0, **values):
    row = Incident(primary_org_id=org_id or db.info["gateway"].org_id,
                   lis_operation_number=nr,
                   created_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=seconds),
                   **values)
    db.add(row)
    db.flush()
    return row


def _ingest(db, inc, nr="f26000001", source="wut", *, gw=None):
    gw = gw or db.info["gateway"]
    row = AlarmIngest(org_id=gw.org_id, gateway_id=gw.id, source=source,
                      raw_hash=uuid4().hex, raw_text="Alarm", leitstellennummer=nr,
                      einsatz_id=inc.id)
    db.add(row)
    db.flush()
    return row


def _compare(db, **kwargs):
    gw = db.info["gateway"]
    return vergleiche_elvis_events(db, gw.org_id, gw.id, **kwargs)


@pytest.mark.parametrize("source", ["dibos", "lis"])
@pytest.mark.parametrize("delta", [-4, 7])
def test_zugeordnet_und_delta_vorzeichen(db, source, delta):
    row = _event(db, "F26000001")
    inc = _incident(db, lis_operation_id=uuid4().hex if source == "lis" else None)
    # Alarmzeit absichtlich abweichend: nur die Anlagezeit zaehlt.
    inc.created_at = row.received_at - timedelta(seconds=delta)
    inc.started_at = row.received_at - timedelta(hours=1)
    db.flush()
    result = _compare(db)
    match = result["elvis_abgleich"][row.id]
    assert match["status"] == "zugeordnet"
    assert match["quelle"] == source
    assert match["quelle_angenommen"] is (source == "dibos")
    assert match["delta_s"] == delta
    assert match["einsatz_id"] == inc.id and match["einsatz_link"] == f"/einsatz/{inc.id}"
    assert result["elvis_auswertung"]["median_s"] == delta


def test_nur_elvis_und_unplausible_nummern(db):
    valid = _event(db)
    invalid = [_event(db, nr) for nr in ("12345678", "f123", "f123456789", "ff26000001")]
    result = _compare(db)
    assert result["elvis_abgleich"][valid.id]["status"] == "nur_elvis"
    assert result["elvis_abgleich"][valid.id]["einsatz_link"] is None
    for row in invalid:
        assert result["elvis_abgleich"][row.id]["status"] == "nummer_unplausibel"
    assert result["elvis_auswertung"] == {
        "gesamt": 5, "zugeordnet": 0, "nur_elvis": 1, "nummer_unplausibel": 4, "median_s": None,
    }


def test_tests_ausgeschlossen_vor_auswertung_aber_sichtbar(db):
    rows = [_event(db, test=True), _event(db, "0"), _event(db, ""), _event(db, "dummy")]
    result = _compare(db)
    assert {r.id for r in result["elvis_events"]} == {r.id for r in rows}
    assert result["elvis_abgleich"] == {}
    assert result["elvis_auswertung"]["gesamt"] == 0


@pytest.mark.parametrize("source", ["wut", "serial", "lis", "dibos"])
def test_zuordnung_ueber_andere_ingest_leitstellennummer(db, source):
    row = _event(db)
    inc = _incident(db, nr=None)
    # Vergleich muss org-weit und nicht nur auf demselben Gateway arbeiten.
    gw = Gateway(org_id=inc.primary_org_id, name="Andere Quelle")
    db.add(gw)
    db.flush()
    _ingest(db, inc, source=source, gw=gw)
    result = _compare(db)["elvis_abgleich"][row.id]
    assert result["status"] == "zugeordnet" and result["einsatz_id"] == inc.id
    assert result["quelle"] == (source if source in ("lis", "dibos") else "seriell")
    assert result["quelle_angenommen"] is False


def test_kein_external_key_oder_elvis_ingest_matching(db):
    row = _event(db)
    inc = _incident(db, nr=None, external_key="f26000001", nummer=26000001)
    _ingest(db, inc, source="elvis")
    assert _compare(db)["elvis_abgleich"][row.id]["status"] == "nur_elvis"


def test_org_isolation_auf_beiden_join_seiten_und_gateway(db):
    row = _event(db)
    other = FireDept(slug="abgleich-" + uuid4().hex, name="Andere", city="Bregenz")
    db.add(other)
    db.flush()
    gw = Gateway(org_id=other.id, name="Fremd")
    db.add(gw)
    db.flush()
    _event(db, gw=gw)
    foreign = _incident(db, org_id=other.id)
    local = _incident(db, nr=None)
    _ingest(db, local, gw=gw)  # fremder Ingest -> lokaler Einsatz
    _ingest(db, foreign)  # lokaler Ingest -> fremder Einsatz
    for tenant in (None, other.id):
        set_tenant_context(db, tenant)
        result = _compare(db)
        assert [e.id for e in result["elvis_events"]] == [row.id]
        assert result["elvis_abgleich"][row.id]["status"] == "nur_elvis"
        assert vergleiche_elvis_events(db, row.org_id, gw.id)["elvis_events"] == []


def test_mehrfach_events_batch_und_rein_lesend(db):
    inc = _incident(db)
    rows = [_event(db, seconds=i) for i in range(12)]
    for i, row in enumerate(rows):
        row.received_at = inc.created_at + timedelta(seconds=i - 8)
    db.commit()
    # Vorab Identitaeten laden, damit deren Reload nicht als Service-Query zaehlt.
    ids = {r.id for r in rows}
    inc_id = inc.id
    gw = db.info["gateway"]
    org_id, gw_id = gw.org_id, gw.id
    queries = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    engine = db.get_bind()
    sql_event.listen(engine, "before_cursor_execute", capture)
    try:
        result = vergleiche_elvis_events(db, org_id, gw_id)
    finally:
        sql_event.remove(engine, "before_cursor_execute", capture)
    assert len(queries) == 3
    assert all(q.lstrip().upper().startswith("SELECT") for q in queries)
    assert set(result["elvis_abgleich"]) == ids
    assert {r["einsatz_id"] for r in result["elvis_abgleich"].values()} == {inc_id}
    assert result["elvis_auswertung"]["median_s"] == -2.5
    assert not db.dirty and not db.new and not db.deleted
    assert len(_compare(db, limit=5)["elvis_events"]) == 5
    assert _compare(db, limit=0)["elvis_events"] == []


def test_keine_impliziten_schreibzugriffe(db):
    row = _event(db)
    db.commit()
    row.raw_text = "Noch nicht gespeichert"
    _compare(db)
    assert row in db.dirty  # no_autoflush: Service schreibt auch fremde Aenderungen nicht.


@pytest.mark.parametrize("delta,word", [(-4, "früher"), (4, "später"), (0, "zeitgleich")])
def test_partial_mit_auswertung_badges_und_htmx(db, delta, word):
    from app.core.templating import templates
    from app.routers.ui_gateway import _elvis_context

    row = _event(db)
    inc = _incident(db)
    inc.created_at = row.received_at - timedelta(seconds=delta)
    _event(db, "f26000002")
    _event(db, "ungueltig")
    _event(db, test=True)
    db.flush()
    html = templates.env.get_template("gateway/_elvis_events.html").render(
        **_elvis_context(db, db.info["gateway"]),
    )
    assert "3 echte Events: 1 zugeordnet" in html
    assert "1 nur ELVis, 1 unplausibel" in html
    assert f'href="/einsatz/{inc.id}"' in html
    assert "DIBOS*" in html and "Test: kein Abgleich" in html
    assert word in html and f"{delta:+g} s" in html
    assert 'hx-trigger="every 15s"' in html and 'hx-swap="outerHTML"' in html
