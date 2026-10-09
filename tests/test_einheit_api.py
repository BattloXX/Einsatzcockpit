"""Integrationstests fuer die JSON-API des GSL-Einheitenmodus."""

import io
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from PIL import Image

from app.core.security import hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    MajorIncident,
    MajorIncidentStatus,
    SiteLogEntry,
    SiteMedia,
    SitePhase,
)
from app.models.master import FireDept, VehicleMaster
from app.models.user import AuditLog, DeviceToken, Role, User, UserRole
from app.services import resource_service


def _png():
    out = io.BytesIO()
    Image.new("RGB", (20, 20)).save(out, "PNG")
    return out.getvalue()


def _db():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def _daten(exercise=False):
    """Erzeugt zwei Organisationen, zwei Geraete und deren Auftraege."""
    s = uuid4().hex
    db = _db()
    try:
        org, fremd = FireDept(slug=f"ea-{s}", name="API"), FireDept(slug=f"ef-{s}", name="Fremd")
        db.add_all([org, fremd])
        db.flush()
        users = [
            User(
                username=f"u{i}-{s}",
                password_hash=hash_password("Test1234!"),
                display_name=f"U{i}",
                org_id=org.id,
                active=True,
                is_device=i < 2,
            )
            for i in range(4)
        ]
        db.add_all(users)
        db.flush()
        for user, role in ((users[2], "admin"), (users[3], "recorder")):
            db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code=role).one().id))
        v1, v2 = (
            VehicleMaster(dept_id=org.id, code="TLF", name="TLF", type="TLF"),
            VehicleMaster(dept_id=org.id, code="RLF", name="RLF", type="RLF"),
        )
        db.add_all([v1, v2])
        db.flush()
        t1, t2 = (
            DeviceToken(
                user_id=users[0].id,
                label="T1",
                token_hash=hash_api_key(s),
                vehicle_master_id=v1.id,
                gsl_profil="einheit",
            ),
            DeviceToken(
                user_id=users[1].id,
                label="T2",
                token_hash=hash_api_key(s + "2"),
                vehicle_master_id=v2.id,
                gsl_profil="einheit",
            ),
        )
        lage, flage = (
            MajorIncident(org_id=org.id, name="Lage", status=MajorIncidentStatus.active, is_exercise=exercise),
            MajorIncident(org_id=fremd.id, name="Fremd", status=MajorIncidentStatus.active),
        )
        db.add_all([t1, t2, lage, flage])
        db.flush()
        e1, e2, fe = (
            LageEinheit(lage_id=lage.id, vehicle_id=v1.id, label="TLF"),
            LageEinheit(lage_id=lage.id, vehicle_id=v2.id, label="RLF"),
            LageEinheit(lage_id=flage.id, label="Fremd"),
        )
        a, b, fs = (
            IncidentSite(
                major_incident_id=lage.id, org_id=org.id, bezeichnung="A", lat=47, lng=9, phase=SitePhase.disponiert
            ),
            IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="B", lat=48, lng=10),
            IncidentSite(major_incident_id=flage.id, org_id=fremd.id, bezeichnung="F"),
        )
        db.add_all([e1, e2, fe, a, b, fs])
        db.flush()
        d1, d2, dother, fd = (
            EinheitSiteDispatch(einheit_id=e1.id, site_id=a.id, dispatched_at=lage.started_at),
            EinheitSiteDispatch(einheit_id=e2.id, site_id=b.id, dispatched_at=lage.started_at),
            EinheitSiteDispatch(einheit_id=e2.id, site_id=a.id, dispatched_at=lage.started_at),
            EinheitSiteDispatch(einheit_id=fe.id, site_id=fs.id, dispatched_at=flage.started_at),
        )
        db.add_all([d1, d2, dother, fd])
        db.commit()
        return dict(
            u1=users[0].id,
            u2=users[1].id,
            admin=users[2].id,
            recorder=users[3].id,
            t1=t1.id,
            t2=t2.id,
            e1=e1.id,
            fe=fe.id,
            lage=lage.id,
            a=a.id,
            b=b.id,
            d1=d1.id,
            d2=d2.id,
            fd=fd.id,
        )
    finally:
        db.close()



def _als(client, session_cookie):
    """Benutzerwechsel: alte (ggf. vom Server erneuerte) Cookies verwerfen."""
    client.cookies.clear()
    client.cookies.set("session", session_cookie)

def _device(client, x, second=False):
    n = "2" if second else "1"
    _als(client, sign_session(x[f"u{n}"], device=True, device_token_id=x[f"t{n}"]))
    client.get("/einheit/api/zustand")


def _h(client, **kw):
    headers = dict(kw)
    if csrf := client.cookies.get("ec_csrf"):
        headers["X-CSRF-Token"] = csrf
    return headers


def test_zustand_liefert_etag_und_304(client, setup_db):
    x = _daten()
    _device(client, x)
    r = client.get("/einheit/api/zustand")
    assert r.status_code == 200 and r.json()["simulation"] is False
    assert client.get("/einheit/api/zustand", headers={"If-None-Match": r.headers["etag"]}).status_code == 304


def test_normaler_benutzer_erhaelt_keinen_einheitenkontext(client, setup_db):
    assert client.get("/einheit/api/zustand").status_code == 403


def test_detail_und_fremde_auftraege(client, setup_db):
    x = _daten()
    db = _db()
    db.add(SiteLogEntry(incident_site_id=x["a"], kind="note", text="Chronik"))
    db.commit()
    db.close()
    _device(client, x)
    r = client.get(f"/einheit/api/auftrag/{x['d1']}")
    assert r.status_code == 200
    assert {
        "auftrag",
        "stelle",
        "andere_einheiten",
        "chronik",
        "fotos",
        "objekte",
        "gefahren",
        "strassensperren",
        "navigation_hinweis",
    } <= r.json().keys()
    assert (
        r.json()["andere_einheiten"][0]["einheit_status_label"] == "Zugewiesen"
        and r.json()["chronik"][0]["kind_label"] == "Notiz"
    )
    assert (
        client.get(f"/einheit/api/auftrag/{x['d2']}").status_code
        == client.get(f"/einheit/api/auftrag/{x['fd']}").status_code
        == 404
    )


def test_status_broadcast_replay_und_uebergang(client, setup_db, monkeypatch):
    import app.routers.ui_einheit as router

    x = _daten()
    _device(client, x)
    events = []

    async def send(lage_id, event):
        events.append(event)

    monkeypatch.setattr(router, "broadcast_lage", send)
    p = {"status": "vor_ort", "client_uuid": str(uuid4())}
    r = client.post(f"/einheit/api/auftrag/{x['d1']}/status", json=p, headers=_h(client))
    again = client.post(f"/einheit/api/auftrag/{x['d1']}/status", json=p, headers=_h(client))
    assert r.status_code == again.status_code == 200 and r.json() == again.json()
    assert {e["type"] for e in events} >= {"einheit:changed", "site_phase_changed"}
    db = _db()
    assert db.get(IncidentSite, x["a"]).phase == SitePhase.in_arbeit
    assert db.query(SiteLogEntry).filter_by(incident_site_id=x["a"], kind="einheit").count() == 1
    db.close()
    bad = client.post(
        f"/einheit/api/auftrag/{x['d1']}/status",
        json={"status": "zugewiesen", "client_uuid": str(uuid4())},
        headers=_h(client),
    )
    assert bad.status_code == 409 and bad.json()["code"] == "ungueltiger_uebergang"


def test_rueckzug_meldung_zeit_und_validierung(client, setup_db):
    x = _daten()
    db = _db()
    resource_service.withdraw_from_site(db, x["e1"], x["lage"], x["a"])
    db.commit()
    db.close()
    _device(client, x)
    p = {"status": "vor_ort", "client_uuid": str(uuid4())}
    a = client.post(f"/einheit/api/auftrag/{x['d1']}/status", json=p, headers=_h(client))
    b = client.post(f"/einheit/api/auftrag/{x['d1']}/status", json=p, headers=_h(client))
    assert (
        a.status_code == b.status_code == 409 and a.json() == b.json() and a.json()["code"] == "auftrag_zurueckgezogen"
    )
    m = client.post(
        f"/einheit/api/auftrag/{x['d1']}/meldung",
        json={
            "art": "lagemeldung",
            "felder": {"lage": "Keller unter Wasser", "freitext": "Pumpe laeuft"},
            "erfasst_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
            "auftrag_version": 99,
            "client_uuid": str(uuid4()),
        },
        headers=_h(client),
    )
    assert m.status_code == 200 and m.json()["hinweis"] == "auftrag_geaendert"
    db = _db()
    log = db.query(SiteLogEntry).filter_by(incident_site_id=x["a"], kind="lagemeldung").one()
    assert (
        log.einheit_id == x["e1"]
        and log.erfasst_at
        and "Lage vor Ort: Keller unter Wasser" in log.text
        and log.text.endswith("(nach Rückzug eingegangen)")
    )
    db.close()
    old = client.post(
        f"/einheit/api/auftrag/{x['d1']}/meldung",
        json={
            "text": "alt",
            "erfasst_at": (datetime.now(UTC) - timedelta(days=3)).isoformat(),
            "client_uuid": str(uuid4()),
        },
        headers=_h(client),
    )
    assert old.json()["hinweis"] == "erfasst_at_korrigiert"
    assert (
        client.post(
            f"/einheit/api/auftrag/{x['d1']}/meldung",
            json={"text": "", "client_uuid": str(uuid4())},
            headers=_h(client),
        ).status_code
        == 400
    )


def test_foto_uuid_medien_und_karte(client, setup_db, monkeypatch, tmp_path):
    from app.services import lage_media_service

    x = _daten()
    monkeypatch.setattr(lage_media_service, "_LAGE_MEDIA_DIR", str(tmp_path / "media"))
    _device(client, x)
    uid = str(uuid4())
    data = {"_csrf": client.cookies.get("ec_csrf"), "client_uuid": uid, "kommentar": "Keller"}
    r = client.post(f"/einheit/api/auftrag/{x['d1']}/foto", data=data, files={"file": ("f.png", _png(), "image/png")})
    again = client.post(
        f"/einheit/api/auftrag/{x['d1']}/foto", data=data, files={"file": ("f.png", _png(), "image/png")}
    )
    assert (
        r.status_code == again.status_code == 200
        and r.json()["aktion_id"] == again.json()["aktion_id"]
        and client.get(f"/einheit/medien/{r.json()['entity_id']}").status_code == 200
    )
    db = _db()
    media = db.query(SiteMedia).one()
    assert media.einheit_id == x["e1"] and media.kommentar == "Keller"
    other = SiteMedia(
        incident_site_id=x["b"], stored_filename="x.jpg", original_filename="x", media_type="image", bytes=1
    )
    db.add(other)
    db.commit()
    oid = other.id
    db.close()
    assert client.get(f"/einheit/medien/{oid}").status_code == 404
    assert [f["properties"]["site_id"] for f in client.get("/einheit/api/karte").json()["features"]] == [x["a"]]
    _device(client, x, True)
    used = client.post(
        f"/einheit/api/auftrag/{x['d2']}/meldung", json={"text": "x", "client_uuid": uid}, headers=_h(client)
    )
    assert used.status_code == 409 and used.json()["code"] == "client_uuid_vergeben"
    assert (
        client.post(
            f"/einheit/api/auftrag/{x['d2']}/meldung", json={"text": "x", "client_uuid": "bad"}, headers=_h(client)
        ).status_code
        == 422
    )


def test_simulation_und_geraete_header(client, setup_db):
    x = _daten(exercise=True)
    _als(client, sign_session(x["admin"]))
    h = _h(client, **{"X-EC-Einheit-Sim": str(x["e1"])})
    assert client.get("/einheit/api/zustand", headers=h).json()["schreibbar"] is True
    # Der GET setzt das CSRF-Cookie dieser Session; Header danach neu bilden.
    h = _h(client, **{"X-EC-Einheit-Sim": str(x["e1"])})
    assert (
        client.post(
            f"/einheit/api/auftrag/{x['d1']}/status", json={"status": "vor_ort", "client_uuid": str(uuid4())}, headers=h
        ).status_code
        == 200
    )
    db = _db()
    assert "(Simulation)" in db.query(SiteLogEntry).filter_by(incident_site_id=x["a"], kind="einheit").one().text
    db.close()
    x = _daten()
    _als(client, sign_session(x["admin"]))
    h = _h(client, **{"X-EC-Einheit-Sim": str(x["e1"])})
    r_echt = client.get("/einheit/api/zustand", headers=h)
    assert r_echt.status_code == 200, r_echt.text
    assert r_echt.json()["schreibbar"] is False
    h = _h(client, **{"X-EC-Einheit-Sim": str(x["e1"])})
    q = client.post(
        f"/einheit/api/auftrag/{x['d1']}/status", json={"status": "vor_ort", "client_uuid": str(uuid4())}, headers=h
    )
    assert q.status_code == 403, (q.text, list(client.cookies.keys()))
    assert q.json()["code"] == "simulation_nur_lesend"
    _als(client, sign_session(x["recorder"]))
    assert client.get("/einheit/api/zustand", headers=h).status_code == 403
    db = _db()
    assert db.query(AuditLog).filter_by(action="gsl.einheit.simulation_abgelehnt", user_id=x["recorder"]).one()
    db.close()
    _als(client, sign_session(x["admin"]))
    assert (
        client.get("/einheit/api/zustand", headers=_h(client, **{"X-EC-Einheit-Sim": str(x["fe"])})).status_code == 404
    )
    _device(client, x)
    assert (
        client.get("/einheit/api/zustand", headers={"X-EC-Einheit-Sim": str(x["fe"])}).json()["kopf"]["einheit_id"]
        == x["e1"]
    )
    _als(client, sign_session(x["admin"]))
    assert client.get("/einheit/api/zustand").json()["code"] == "kein_einheitenkontext"
