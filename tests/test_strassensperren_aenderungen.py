from datetime import datetime
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.road_closure import RoadClosureChange
from app.services import road_closure_service as service


def _data(title, street, starts, ends):
    return {
        "title": title,
        "street": street,
        "valid_from": starts,
        "valid_until": ends,
        "restriction_type": "closed",
        "priority": "normal",
    }


def test_related_extension_and_supersede(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        suffix = uuid4().hex[:8]
        org = FireDept(slug=f"aenderung-{suffix}", name="Änderung")
        db.add(org)
        db.flush()
        old = service.create_closure(
            db,
            org.id,
            None,
            _data("Alt", f"Teststraße {suffix}", datetime(2026, 10, 1), datetime(2026, 10, 10)),
        )
        related = service.find_related(
            db,
            org.id,
            street=old.street,
            reference_number=None,
            from_text=None,
            to_text=None,
            valid_from=datetime(2026, 10, 6),
            valid_until=datetime(2026, 10, 24),
            geometry=None,
        )
        assert related[0].beziehung == "verlaengerung"
        assert related[0].vorgeschlagene_aenderung == {"valid_until": datetime(2026, 10, 24)}
        new = service.create_closure(
            db,
            org.id,
            None,
            _data("Neu", old.street, datetime(2026, 10, 6), datetime(2026, 10, 24)),
        )
        service.supersede_closure(db, old, new, None, "ui")
        db.flush()
        assert old.superseded_by_id == new.id
        assert old.cancelled_at is not None
        actions = {row.action for row in db.query(RoadClosureChange).all()}
        assert {"superseded", "supersedes"} <= actions
    finally:
        db.rollback()
        db.close()


def _org(db, prefix: str) -> FireDept:
    org = FireDept(slug=f"{prefix}-{uuid4().hex[:8]}", name=prefix)
    db.add(org)
    db.flush()
    return org


def _related(db, org_id, street, starts, ends, **more):
    return service.find_related(
        db, org_id, street=street, reference_number=more.pop("reference_number", None),
        valid_from=starts, valid_until=ends, geometry=None, **more,
    )


def test_zeitfenster_aktenzeichen_und_ausschluesse(setup_db):
    from app.models.road_closure import RoadClosureShare

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = _org(db, "aend-a")
        partner = _org(db, "aend-b")
        street = f"Teststraße {uuid4().hex[:8]}"
        old = service.create_closure(
            db, org.id, None,
            _data("Alt", street, datetime(2026, 10, 1), datetime(2026, 10, 10)) | {"reference_number": "BH-77"},
        )
        assert [r.closure.id for r in _related(db, org.id, street, datetime(2026, 10, 20), datetime(2026, 10, 30))] == [
            old.id
        ]
        assert _related(db, org.id, street, datetime(2026, 10, 31), datetime(2026, 11, 5)) == []
        ersatz = _related(
            db, org.id, f"Andere {uuid4().hex[:6]}", datetime(2027, 1, 1), None, reference_number=" bh-77 "
        )
        assert [(r.closure.id, r.beziehung) for r in ersatz] == [(old.id, "ersatz")]
        assert _related(db, org.id, f"Andere {uuid4().hex[:6]}", datetime(2026, 10, 5), None) == []
        dublette = _related(db, org.id, street, datetime(2026, 10, 1), datetime(2026, 10, 10))
        assert dublette[0].beziehung == "dublette" and dublette[0].vorgeschlagene_aenderung == {}

        shared = service.create_closure(
            db, partner.id, None, _data("Partner", street, datetime(2026, 10, 1), datetime(2026, 10, 10))
        )
        db.add(RoadClosureShare(road_closure_id=shared.id, org_id=org.id))
        db.flush()
        ids = {r.closure.id for r in _related(db, org.id, street, datetime(2026, 10, 2), datetime(2026, 10, 9))}
        assert shared.id not in ids
        service.deactivate_closure(db, old, None, "Test")
        db.flush()
        assert _related(db, org.id, street, datetime(2026, 10, 2), datetime(2026, 10, 9)) == []
    finally:
        db.rollback()
        db.close()


def test_find_duplicates_findet_weiter_unbefristete_sperren(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = _org(db, "aend-dup")
        street = f"Teststraße {uuid4().hex[:8]}"
        open_ended = service.create_closure(db, org.id, None, _data("Offen", street, datetime(2026, 9, 1), None))
        found = service.find_duplicates(
            db, org.id, street=street, from_text=None, to_text=None,
            valid_from=datetime(2026, 10, 1), valid_until=datetime(2026, 10, 5),
        )
        assert [item.id for item in found] == [open_ended.id]
    finally:
        db.rollback()
        db.close()


def test_mcp_verlaengerung_ersetzen_und_fremde_sperre(client):
    from tests.test_mcp_objekte import _token
    from tests.test_mcp_strassensperren import _bereit, _db, _rufe, _sperre

    seed = _bereit("mcp-aenderung", {"obj": "objekt_verwalter"})
    fremd = _bereit("mcp-aenderung-fremd")
    token = _token(client, seed, "obj")
    street = f"Rebberg {uuid4().hex[:6]}"
    geometry = {"type": "LineString", "coordinates": [[9.75, 47.467], [9.752, 47.4675]]}
    base = {"restriction_type": "closed", "street": street, "geometry_geojson": geometry}
    first = _rufe(client, token, "strassensperre_anlegen", title="Sperre Rebberg",
                  valid_from="2026-10-01T07:00", valid_until="2026-10-10T18:00", **base)
    first_id = first["strassensperre"]["id"]
    update = _rufe(client, token, "strassensperre_anlegen", title="Verlängerung Straßensperre Rebberg",
                   valid_from="2026-10-06T07:00", valid_until="2026-10-24T18:00", **base)
    assert update["status"] == "possible_update" and update["existing_road_closure_id"] == first_id
    kandidat = update["kandidaten"][0]
    assert kandidat["beziehung"] == "verlaengerung"
    assert kandidat["vorgeschlagene_aenderung"] == {"valid_until": "2026-10-24T18:00:00+02:00"}
    applied = _rufe(client, token, "strassensperre_aktualisieren", road_closure_id=first_id,
                    felder=kandidat["vorgeschlagene_aenderung"])
    assert applied["strassensperre"]["valid_until"].startswith("2026-10-24T18:00")

    foreign_id = _sperre(fremd["org_id"], "Fremd", geometry)
    db = _db()
    try:
        before = db.query(service.RoadClosure).filter_by(org_id=seed["org_id"]).count()
    finally:
        db.close()
    denied = _rufe(client, token, "strassensperre_anlegen", title="Neue Verordnung Rebberg",
                   valid_from="2026-11-01T07:00", ersetzt_road_closure_id=foreign_id, **base)
    assert "__fehler__" in denied
    db = _db()
    try:
        assert db.query(service.RoadClosure).filter_by(org_id=seed["org_id"]).count() == before
    finally:
        db.close()

    replaced = _rufe(client, token, "strassensperre_anlegen", title="Neufassung Rebberg",
                     valid_from="2026-10-20T07:00", valid_until="2026-12-01T18:00",
                     ersetzt_road_closure_id=first_id, **base)
    assert replaced["status"] == "created" and replaced["ersetzt"] == first_id
    db = _db()
    try:
        old = db.get(service.RoadClosure, first_id)
        assert old.cancelled_at is not None and old.superseded_by_id == replaced["strassensperre"]["id"]
        assert "Ersetzt durch Sperre" in old.cancel_reason
    finally:
        db.close()
    created = _rufe(client, token, "strassensperre_anlegen", title="Zusätzliche Sperre Rebberg",
                    valid_from="2026-10-21T07:00", valid_until="2026-11-30T18:00", duplikat_bestaetigt=True, **base)
    assert created["status"] == "created"


def test_ui_neuanlage_mit_kandidat_drei_wege(client):
    from tests.test_strassensperren_ui import _closure, _login, _payload, _setup_user

    street = f"Unterhub {uuid4().hex[:6]}"
    existing = _closure(
        org_id=1, title=f"Alt {uuid4().hex[:6]}", street=street,
        valid_from=datetime(2026, 7, 1, 5), valid_until=datetime(2026, 7, 10, 16),
    )
    user = _setup_user("objekt_verwalter")
    _login(client, user)
    form = _payload(client, street=street, valid_from="2026-07-08T07:00", valid_until="2026-07-24T18:00")
    hint = client.post("/strassensperren/neu", data=form)
    assert hint.status_code == 409
    assert "Bestehende Sperre bearbeiten" in hint.text and "Ersetzt bestehende Sperre" in hint.text
    assert "Trotzdem neu anlegen" in hint.text
    assert f"/strassensperren/{existing.id}/bearbeiten?valid_until=2026-07-24T18:00" in hint.text

    prefilled = client.get(f"/strassensperren/{existing.id}/bearbeiten?valid_until=2026-07-24T18:00")
    assert 'value="2026-07-24T18:00"' in prefilled.text

    created = client.post("/strassensperren/neu", data=form | {"als_neu_bestaetigt": "1"}, follow_redirects=False)
    assert created.status_code == 303
    replaced = client.post("/strassensperren/neu", data=form | {"ersetzt_id": str(existing.id)}, follow_redirects=False)
    assert replaced.status_code == 303
    new_id = int(replaced.headers["location"].rsplit("/", 1)[1])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        old = db.get(service.RoadClosure, existing.id)
        assert old.cancelled_at is not None and old.superseded_by_id == new_id
    finally:
        db.close()
    detail = client.get(f"/strassensperren/{new_id}").text
    assert f"#{existing.id}" in detail and "Ersetzt" in detail
