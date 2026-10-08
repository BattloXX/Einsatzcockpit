"""Öffentliche Einzelansicht, Zugangstokens und Freigabelink der Straßensperren."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.security import hash_api_key
from app.core.tenant import _TENANT_TABLE_NAMES, set_tenant_context
from app.db import SessionLocal
from app.models.invitation import OrgPartner
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.road_closure import (
    RoadClosure,
    RoadClosureAccessToken,
    RoadClosureNotification,
    RoadClosureShare,
    RoadClosureTeamsConfig,
)
from app.routers.auth import _safe_next
from app.services import road_closure_token_service as tokens
from app.services.road_closure_public_service import public_closure_dict, public_closures_q
from tests.test_strassensperren_ui import _closure, _login, _payload, _setup_user

GEHEIM = "GEHEIM-INTERN-4711"
_LINE = json.dumps({"type": "LineString", "coordinates": [[9.74, 47.468], [9.748, 47.4665]]})


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _modul_an(db, org_id: int, an: bool = True) -> None:
    system = db.get(SystemSettings, "strassensperren_module_enabled")
    if system is None:
        db.add(SystemSettings(key="strassensperren_module_enabled", value="true"))
    else:
        system.value = "true"
    settings = db.query(OrgSettings).filter_by(org_id=org_id).first()
    if settings is None:
        settings = OrgSettings(org_id=org_id)
        db.add(settings)
    settings.strassensperren_modul_aktiv = an


def _sperre(org_id: int = 1, **werte) -> RoadClosure:
    values = {
        "title": f"Öffentlich {uuid4().hex[:8]}", "description": GEHEIM, "source": GEHEIM,
        "source_url": f"https://intern.example/{GEHEIM}", "reason": "Kanalbau", "exceptions": "Anrainer frei",
        "street": "Bregenzer Straße", "from_text": "Nr. 12", "to_text": "Nr. 38", "max_weight_t": 7.5,
        "geometry_geojson": _LINE, "geometry_status": "ok",
    }
    values.update(werte)
    return _closure(org_id=org_id, **values)


def _token(closure: RoadClosure, art: str = "detail", **kwargs) -> tuple[int, str]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        _modul_an(db, closure.org_id)
        token, raw = tokens.create_token(
            db, closure.org_id, art, None, road_closure_id=closure.id if art == "detail" else None, **kwargs
        )
        db.commit()
        return token.id, raw
    finally:
        db.close()


def _set_token(token_id: int, **werte) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        token = db.get(RoadClosureAccessToken, token_id)
        for key, value in werte.items():
            setattr(token, key, value)
        db.commit()
    finally:
        db.close()


def _assert_404_ohne_login(response) -> None:
    assert response.status_code == 404
    assert "/login" not in response.headers.get("location", "")


# --- Schema ---------------------------------------------------------------------------------------------------


def test_neue_tabellen_sind_tenant_tabellen_und_teams_melden_default_aus() -> None:
    for name in ("road_closure_access_token", "road_closure_teams_config", "road_closure_notification"):
        assert name in _TENANT_TABLE_NAMES
    assert RoadClosure.__table__.c.reason.type.length == 300
    assert not RoadClosure.__table__.c.teams_melden.nullable
    assert RoadClosureAccessToken.__table__.c.token_hash.unique
    assert RoadClosureTeamsConfig.__table__.c.webhook_url_enc is not None
    constraints = {tuple(c.columns.keys()) for c in RoadClosureNotification.__table__.constraints if c.columns}
    assert ("road_closure_id", "dedup_key") in constraints


def test_migration_0262_haengt_an_0261() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0262_strassensperren_freigaben_teams.py"
    spec = importlib.util.spec_from_file_location("mig0262", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0262" and module.down_revision == "0261"


# --- Token-Service --------------------------------------------------------------------------------------------


def test_token_wird_nur_als_hash_und_verschluesselt_gespeichert(setup_db) -> None:
    closure = _sperre()
    token_id, raw = _token(closure)
    assert raw.startswith("rcd_") and len(raw) > 40
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        token = db.get(RoadClosureAccessToken, token_id)
        assert token.token_hash == hash_api_key(raw)
        assert raw not in (token.token_enc or "")
        assert tokens.token_plain(token) == raw
        json.loads(token.berechtigungen_json)
        assert tokens.public_url(raw, "detail").endswith(f"/oeffentlich/strassensperre/{raw}")
    finally:
        db.close()


def test_token_fuer_fremde_sperre_wird_abgelehnt(setup_db) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        fremd = FireDept(slug=f"fremd-{uuid4().hex}", name="Fremd-Org", timezone="Europe/Vienna")
        db.add(fremd)
        db.flush()
        closure = RoadClosure(org_id=fremd.id, title="Fremd", valid_from=_now(), restriction_type="closed")
        db.add(closure)
        db.flush()
        with pytest.raises(ValueError):
            tokens.create_token(db, 1, "detail", None, road_closure_id=closure.id)
        with pytest.raises(ValueError):
            tokens.create_token(db, 1, "detail", None)
        with pytest.raises(ValueError):
            tokens.create_token(db, 1, "unbekannt", None)
    finally:
        db.rollback()
        db.close()


# --- Öffentliche Einzelansicht --------------------------------------------------------------------------------


def test_oeffentliche_detailansicht_zeigt_nur_whitelist(client) -> None:
    closure = _sperre()
    _token_id, raw = _token(closure)
    client.cookies.clear()
    response = client.get(f"/oeffentlich/strassensperre/{raw}")
    assert response.status_code == 200
    text = response.text
    assert closure.title in text and "Kanalbau" in text and "Anrainer frei" in text
    assert "Vollsperre" in text and "max. 7.5 t" in text
    assert GEHEIM not in text
    assert 'href="/login?next=/strassensperren"' in text
    assert response.headers["x-robots-tag"].startswith("noindex")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"

    geo = client.get(f"/oeffentlich/strassensperre/{raw}/geometrie.json")
    assert geo.status_code == 200
    payload = geo.json()
    assert payload["type"] == "FeatureCollection" and len(payload["features"]) == 1
    properties = payload["features"][0]["properties"]
    assert "description" not in properties and "source" not in properties and "source_url" not in properties
    assert GEHEIM not in geo.text


def test_token_wird_als_genutzt_markiert(client) -> None:
    closure = _sperre()
    token_id, raw = _token(closure)
    client.cookies.clear()
    assert client.get(f"/oeffentlich/strassensperre/{raw}").status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(RoadClosureAccessToken, token_id).last_used_at is not None
    finally:
        db.close()


@pytest.mark.parametrize("fall", ["widerrufen", "abgelaufen", "falsche_art", "muell", "modul_aus"])
def test_ungueltige_tokens_liefern_404_ohne_login_redirect(client, fall) -> None:
    closure = _sperre()
    token_id, raw = _token(closure, "status" if fall == "falsche_art" else "detail")
    if fall == "widerrufen":
        _set_token(token_id, revoked_at=_now())
    elif fall == "abgelaufen":
        _set_token(token_id, expires_at=_now() - timedelta(minutes=1))
    elif fall == "muell":
        raw = "rcd_" + uuid4().hex
    elif fall == "modul_aus":
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            _modul_an(db, closure.org_id, an=False)
            db.commit()
        finally:
            db.close()
    client.cookies.clear()
    try:
        _assert_404_ohne_login(client.get(f"/oeffentlich/strassensperre/{raw}", follow_redirects=False))
        _assert_404_ohne_login(client.get(f"/oeffentlich/strassensperre/{raw}/geometrie.json"))
    finally:
        if fall == "modul_aus":
            db = SessionLocal()
            set_tenant_context(db, None)
            try:
                _modul_an(db, closure.org_id, an=True)
                db.commit()
            finally:
                db.close()


def test_deaktivierte_und_lange_beendete_sperren_sind_nicht_oeffentlich(client) -> None:
    deaktiviert = _sperre(cancelled_at=_now())
    _id, raw_deaktiviert = _token(deaktiviert)
    kurz = _sperre(valid_from=_now() - timedelta(days=10), valid_until=_now() - timedelta(days=5))
    _id, raw_kurz = _token(kurz)
    lang = _sperre(valid_from=_now() - timedelta(days=60), valid_until=_now() - timedelta(days=40))
    _id, raw_lang = _token(lang)
    client.cookies.clear()
    _assert_404_ohne_login(client.get(f"/oeffentlich/strassensperre/{raw_deaktiviert}"))
    beendet = client.get(f"/oeffentlich/strassensperre/{raw_kurz}")
    assert beendet.status_code == 200 and "Diese Sperre ist beendet." in beendet.text
    _assert_404_ohne_login(client.get(f"/oeffentlich/strassensperre/{raw_lang}"))


def test_detail_token_zeigt_keine_sperre_einer_fremden_org(client) -> None:
    """Cross-Org: Token-Zeile von Org A, die (manipuliert) auf eine Sperre von Org B zeigt."""
    eigene = _sperre()
    token_id, raw = _token(eigene)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_b = FireDept(slug=f"fremd-{uuid4().hex}", name="Org B", timezone="Europe/Vienna")
        db.add(org_b)
        db.flush()
        _modul_an(db, org_b.id)
        fremde = RoadClosure(org_id=org_b.id, title=f"Org B {GEHEIM}", valid_from=_now(), restriction_type="closed",
                             geometry_geojson=_LINE)
        db.add(fremde)
        db.flush()
        db.get(RoadClosureAccessToken, token_id).road_closure_id = fremde.id
        db.commit()
    finally:
        db.close()
    client.cookies.clear()
    response = client.get(f"/oeffentlich/strassensperre/{raw}")
    _assert_404_ohne_login(response)
    assert GEHEIM not in response.text
    _assert_404_ohne_login(client.get(f"/oeffentlich/strassensperre/{raw}/geometrie.json"))


# --- Öffentliche Datenbasis -----------------------------------------------------------------------------------


def test_public_closures_nur_eigene_aktuelle_und_whitelist_felder(setup_db) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"pub-{uuid4().hex}", name="Pub-Org", timezone="Europe/Vienna")
        partner = FireDept(slug=f"pubp-{uuid4().hex}", name="Partner", timezone="Europe/Vienna")
        db.add_all([org, partner])
        db.flush()
        db.add(OrgPartner(org_id=partner.id, partner_org_id=org.id))
        now = _now()

        def add(org_id, title, **werte):
            values = {"org_id": org_id, "title": title, "valid_from": now - timedelta(hours=1),
                      "restriction_type": "closed", "description": GEHEIM}
            values.update(werte)
            closure = RoadClosure(**values)
            db.add(closure)
            db.flush()
            return closure

        aktiv = add(org.id, "aktiv", city="Wolfurt")
        add(org.id, "geplant", valid_from=now + timedelta(days=2))
        add(org.id, "deaktiviert", cancelled_at=now)
        add(org.id, "abgelaufen", valid_until=now - timedelta(hours=1))
        geteilt = add(partner.id, "partner")
        db.add(RoadClosureShare(road_closure_id=geteilt.id, org_id=org.id))
        db.flush()

        titles = [closure.title for closure in public_closures_q(db, org.id, now=now)]
        assert titles == ["aktiv", "geplant"]
        assert [c.title for c in public_closures_q(db, org.id, now=now, include_planned=False)] == ["aktiv"]

        data = public_closure_dict(aktiv, org, now=now)
        assert GEHEIM not in json.dumps(data, default=str)
        verboten = {"description", "source", "source_url", "version", "geometry_meta_json", "geometry_quality",
                    "created_by_user_id", "url", "org_id"}
        assert not verboten & set(data)
        assert data["einsatzgebiet"] == "Pub-Org – Wolfurt"
        assert data["status"] == "active" and not data["valid_until_local"]
    finally:
        db.rollback()
        db.close()


# --- Freigabelink in der Verwaltung ---------------------------------------------------------------------------


def test_verwalter_erzeugt_wiederverwendet_und_widerruft_link(client) -> None:
    closure = _sperre()
    _login(client, _setup_user("objekt_verwalter"))
    csrf = client.cookies.get("ec_csrf")
    erste = client.post(f"/strassensperren/{closure.id}/freigabelink", data={"_csrf": csrf}, follow_redirects=False)
    assert erste.status_code == 303
    detail = client.get(f"/strassensperren/{closure.id}").text
    assert 'id="freigabelink"' in detail
    start = detail.index("/oeffentlich/strassensperre/rcd_")
    url = detail[start:detail.index('"', start)]
    client.post(f"/strassensperren/{closure.id}/freigabelink", data={"_csrf": csrf})
    assert url in client.get(f"/strassensperren/{closure.id}").text  # wiederverwendet

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        token = db.query(RoadClosureAccessToken).filter_by(road_closure_id=closure.id, revoked_at=None).one()
        token_id = token.id
    finally:
        db.close()
    assert client.get(url).status_code == 200
    widerruf = client.post(f"/strassensperren/{closure.id}/freigabelink/{token_id}/widerrufen", data={"_csrf": csrf},
                           follow_redirects=False)
    assert widerruf.status_code == 303
    _assert_404_ohne_login(client.get(url))


def test_link_mit_ablaufdatum_ersetzt_bestehenden(client) -> None:
    closure = _sperre()
    _login(client, _setup_user("objekt_verwalter"))
    csrf = client.cookies.get("ec_csrf")
    client.post(f"/strassensperren/{closure.id}/freigabelink", data={"_csrf": csrf})
    datum = (_now() + timedelta(days=3)).date().isoformat()
    client.post(f"/strassensperren/{closure.id}/freigabelink", data={"_csrf": csrf, "gueltig_bis": datum})
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(RoadClosureAccessToken).filter_by(road_closure_id=closure.id).all()
        aktiv = [row for row in rows if row.revoked_at is None]
        assert len(rows) == 2 and len(aktiv) == 1 and aktiv[0].expires_at is not None
    finally:
        db.close()


def test_leser_darf_keinen_link_erzeugen_und_csrf_ist_pflicht(client) -> None:
    closure = _sperre()
    _login(client, _setup_user("readonly"))
    assert client.post(f"/strassensperren/{closure.id}/freigabelink",
                       data={"_csrf": client.cookies.get("ec_csrf")}).status_code == 403
    assert 'id="freigabelink"' not in client.get(f"/strassensperren/{closure.id}").text
    _login(client, _setup_user("objekt_verwalter"))
    assert client.post(f"/strassensperren/{closure.id}/freigabelink", data={}).status_code in {400, 403}


def test_kein_link_fuer_partnersperre(client) -> None:
    verwalter = _setup_user("objekt_verwalter")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_b = FireDept(slug=f"nb-{uuid4().hex}", name="Nachbar", timezone="Europe/Vienna")
        db.add(org_b)
        db.flush()
        db.add(OrgPartner(org_id=org_b.id, partner_org_id=verwalter.org_id))
        geteilt = RoadClosure(org_id=org_b.id, title="Geteilt", valid_from=_now(), restriction_type="closed")
        db.add(geteilt)
        db.flush()
        db.add(RoadClosureShare(road_closure_id=geteilt.id, org_id=verwalter.org_id))
        db.commit()
        geteilt_id = geteilt.id
    finally:
        db.close()
    _login(client, verwalter)
    response = client.post(f"/strassensperren/{geteilt_id}/freigabelink", data={"_csrf": client.cookies.get("ec_csrf")})
    assert response.status_code == 404


def test_grund_wird_gespeichert_und_angezeigt(client) -> None:
    _login(client, _setup_user("objekt_verwalter"))
    response = client.post("/strassensperren/neu", data=_payload(client, reason="Brückensanierung"),
                           follow_redirects=False)
    assert response.status_code == 303
    detail = client.get(response.headers["location"]).text
    assert "Grund (öffentlich)" in detail and "Brückensanierung" in detail


# --- Login-Übergang -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ziel", "erwartet"),
    [
        ("/strassensperren", "/strassensperren"),
        ("/strassensperren?status=planned", "/strassensperren?status=planned"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("\\\\evil.example", "/"),
        ("https://evil.example", "/"),
        ("/ok\x00", "/"),
        ("/ok\n", "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_safe_next(ziel, erwartet) -> None:
    assert _safe_next(ziel) == erwartet
