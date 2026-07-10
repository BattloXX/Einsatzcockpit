"""Lageführung-Modul Phase 3: Lage-Replay (angereicherte Event-Payloads), Momentaufnahme
("Lage einfrieren") und Windrichtung mit Wetterdienst-Vorbelegung.
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn
from app.models.lagefuehrung import LagefuehrungSnapshot
from app.models.master import OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole
from app.services.weather_service import CurrentWeather

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup(username, *, org_id=ORG_ID, rollen=("incident_leader",), lat=47.4652, lng=9.7503):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Lft4 Test", org_id=org_id, active=True)
        db.add(user)
        db.flush()
        for code in rollen:
            db.add(UserRole(user_id=user.id, role_id=_rolle(db, code).id))

        sys_row = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
        if sys_row is None:
            db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org_id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org_id)
            db.add(os_row)
        os_row.lagefuehrung_modul_aktiv = True

        incident = Incident(primary_org_id=org_id, alarm_type_code="T1", status="active",
                             lat=lat, lng=lng)
        db.add(incident)
        db.flush()
        db.add(IncidentColumn(
            incident_id=incident.id, code="active", title="Tatsächlich im Einsatz",
            column_kind="vehicles", is_fixed=True,
        ))
        db.commit()
        return user.id, incident.id
    finally:
        db.close()


# ── Lage-Replay: Event-Payloads tragen den vollen Feature-Zustand ───────────────

def test_feature_created_event_traegt_vollen_zustand(client):
    _, incident_id = _setup("lft4_created_user")
    _login(client, "lft4_created_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/features",
        json={
            "typ": "taktisches_zeichen", "zeichen_key": "einheit_trupp", "label": "Trupp 1",
            "geometry": {"type": "Point", "coordinates": [9.75, 47.46]},
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201, r.text[:300]

    events = client.get(f"/einsatz/{incident_id}/lagefuehrung/events.json").json()
    created = next(e for e in events if e["event_typ"] == "feature.created")
    payload = created["payload"]
    assert payload["typ"] == "taktisches_zeichen"
    assert payload["zeichen_key"] == "einheit_trupp"
    assert payload["label"] == "Trupp 1"
    assert payload["geometry"]["coordinates"] == [9.75, 47.46]
    assert payload["id"] == created["ref_id"]


def test_feature_updated_event_traegt_neuen_zustand(client):
    _, incident_id = _setup("lft4_updated_user")
    _login(client, "lft4_updated_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    created = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/features",
        json={"typ": "marker", "geometry": {"type": "Point", "coordinates": [9.75, 47.46]}},
        headers={"X-CSRF-Token": csrf},
    ).json()

    r = client.patch(
        f"/einsatz/{incident_id}/lagefuehrung/features/{created['id']}",
        json={"version": created["version"], "rotation": 90,
              "geometry": {"type": "Point", "coordinates": [9.76, 47.47]}},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200

    events = client.get(f"/einsatz/{incident_id}/lagefuehrung/events.json").json()
    updated = next(e for e in events if e["event_typ"] == "feature.updated")
    payload = updated["payload"]
    assert payload["rotation"] == 90
    assert payload["geometry"]["coordinates"] == [9.76, 47.47]
    assert payload["version"] == 2


def test_feature_deleted_event_traegt_snapshot_vor_dem_loeschen(client):
    _, incident_id = _setup("lft4_deleted_user")
    _login(client, "lft4_deleted_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    created = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/features",
        json={"typ": "marker", "label": "wird gelöscht",
              "geometry": {"type": "Point", "coordinates": [9.75, 47.46]}},
        headers={"X-CSRF-Token": csrf},
    ).json()

    r = client.delete(
        f"/einsatz/{incident_id}/lagefuehrung/features/{created['id']}?version={created['version']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 204

    events = client.get(f"/einsatz/{incident_id}/lagefuehrung/events.json").json()
    deleted = next(e for e in events if e["event_typ"] == "feature.deleted")
    assert deleted["payload"]["label"] == "wird gelöscht"
    assert deleted["ref_id"] == created["id"]


def test_replay_rekonstruktion_aus_events_ergibt_aktuellen_zustand(client):
    """Simuliert den JS-Replay-Algorithmus (lagefuehrung.js::replayStateAt) in Python:
    aus den (aufsteigend sortierten) Event-Payloads muss sich exakt der Endzustand
    ergeben, den features.json tatsächlich liefert."""
    _, incident_id = _setup("lft4_replay_user")
    _login(client, "lft4_replay_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    f1 = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/features",
        json={"typ": "marker", "geometry": {"type": "Point", "coordinates": [9.70, 47.40]}},
        headers={"X-CSRF-Token": csrf},
    ).json()
    f2 = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/features",
        json={"typ": "marker", "geometry": {"type": "Point", "coordinates": [9.71, 47.41]}},
        headers={"X-CSRF-Token": csrf},
    ).json()
    client.patch(
        f"/einsatz/{incident_id}/lagefuehrung/features/{f1['id']}",
        json={"version": f1["version"], "geometry": {"type": "Point", "coordinates": [9.72, 47.42]}},
        headers={"X-CSRF-Token": csrf},
    )
    client.delete(
        f"/einsatz/{incident_id}/lagefuehrung/features/{f2['id']}?version={f2['version']}",
        headers={"X-CSRF-Token": csrf},
    )

    events_desc = client.get(f"/einsatz/{incident_id}/lagefuehrung/events.json?limit=2000").json()
    events_asc = list(reversed(events_desc))

    state = {}
    for e in events_asc:
        if e.get("ref_typ") != "feature" or e.get("ref_id") is None:
            continue
        if e["event_typ"] in ("feature.created", "feature.updated") and e.get("payload"):
            state[e["ref_id"]] = e["payload"]
        elif e["event_typ"] == "feature.deleted":
            state.pop(e["ref_id"], None)

    live = {f["id"]: f for f in client.get(f"/einsatz/{incident_id}/lagefuehrung/features.json").json()}
    assert set(state.keys()) == set(live.keys())
    assert state[f1["id"]]["geometry"]["coordinates"] == [9.72, 47.42]


def test_events_json_limit_wird_begrenzt(client):
    _, incident_id = _setup("lft4_limit_user")
    _login(client, "lft4_limit_user", "Test1234!")
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/events.json?limit=1")
    assert r.status_code == 200
    assert len(r.json()) <= 1


# ── Momentaufnahme ────────────────────────────────────────────────────────────────

def test_momentaufnahme_erstellt_snapshot_und_liefert_bild(client, monkeypatch):
    _, incident_id = _setup("lft4_snap_user")

    # render_lagefuehrung_map_png() rendert per staticmap/requests eine echte OSM-Kachel
    # (Netzwerk-I/O) — im Test durch feste Bytes ersetzt, statt vom Netzwerk in CI abzuhängen.
    # Der lokale Import in lagefuehrung_momentaufnahme_erstellen() löst den Namen bei jedem
    # Aufruf frisch aus dem Quellmodul auf, daher dort patchen (nicht am Router-Modul).
    from app.services import lagefuehrung_pdf_service
    monkeypatch.setattr(
        lagefuehrung_pdf_service, "render_lagefuehrung_map_png",
        lambda *a, **kw: b"\x89PNG-fake-bytes",
    )

    _login(client, "lft4_snap_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/momentaufnahme",
        json={"label": "Erstlage"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201, r.text[:300]
    snap = r.json()
    assert snap["label"] == "Erstlage"

    bild = client.get(f"/einsatz/{incident_id}/lagefuehrung/momentaufnahme/{snap['id']}/bild")
    assert bild.status_code == 200
    assert bild.headers["content-type"] == "image/png"
    assert bild.content == b"\x89PNG-fake-bytes"

    events = client.get(f"/einsatz/{incident_id}/lagefuehrung/events.json").json()
    snap_event = next(e for e in events if e["event_typ"] == "snapshot.erstellt")
    assert snap_event["ref_id"] == snap["id"]
    assert snap_event["payload"]["label"] == "Erstlage"

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.get(LagefuehrungSnapshot, snap["id"])
        assert row is not None
        assert row.bytes == len(b"\x89PNG-fake-bytes")
    finally:
        db.close()


def test_momentaufnahme_ohne_koordinaten_liefert_400(client):
    _, incident_id = _setup("lft4_snap_nokoord_user", lat=None, lng=None)
    _login(client, "lft4_snap_nokoord_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/momentaufnahme",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_momentaufnahme_erfordert_editierrecht(client):
    _, incident_id = _setup("lft4_snap_viewer", rollen=("readonly",))
    _login(client, "lft4_snap_viewer", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(
        f"/einsatz/{incident_id}/lagefuehrung/momentaufnahme",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403


# ── Windrichtung ────────────────────────────────────────────────────────────────

def test_wind_json_liefert_richtung(client, monkeypatch):
    _, incident_id = _setup("lft4_wind_user")

    async def _fake_get_current(lat, lng, org_id=None):
        return CurrentWeather(wind_direction_deg=225.0, wind_speed_ms=4.2, source="openmeteo")

    from app.services import weather_service
    monkeypatch.setattr(weather_service, "get_current", _fake_get_current)

    _login(client, "lft4_wind_user", "Test1234!")
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/wind.json")
    assert r.status_code == 200
    data = r.json()
    assert data["wind_direction_deg"] == 225.0
    assert data["source"] == "openmeteo"


def test_wind_json_ohne_daten_liefert_leeres_objekt(client, monkeypatch):
    _, incident_id = _setup("lft4_wind_none_user")

    async def _fake_get_current(lat, lng, org_id=None):
        return None

    from app.services import weather_service
    monkeypatch.setattr(weather_service, "get_current", _fake_get_current)

    _login(client, "lft4_wind_none_user", "Test1234!")
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/wind.json")
    assert r.status_code == 200
    assert r.json() == {}


def test_wind_json_ohne_koordinaten_liefert_leeres_objekt(client):
    _, incident_id = _setup("lft4_wind_nokoord_user", lat=None, lng=None)
    _login(client, "lft4_wind_nokoord_user", "Test1234!")
    r = client.get(f"/einsatz/{incident_id}/lagefuehrung/wind.json")
    assert r.status_code == 200
    assert r.json() == {}


def test_windrichtung_symbol_im_manifest():
    from app.services.tz_service import load_tz_manifest
    ids = [s["id"] for s in load_tz_manifest().get("symbole", [])]
    assert "windrichtung" in ids
