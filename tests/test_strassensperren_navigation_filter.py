"""Focused regression checks for road-closure navigation helpers."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.invitation import OrgPartner
from app.models.master import FireDept, OrgSettings
from app.models.road_closure import RoadClosure, RoadClosureShare
from app.routers.ui_road_closure import _filter_query
from app.services.road_closure_stats_service import kennzahlen
from tests.test_strassensperren_ui import _closure, _login, _setup_user


def test_filter_query_omits_defaults_and_drops_custom_dates() -> None:
    filters = {
        "status": "planned", "zeitraum": "eigen", "scope": "all", "q": "", "restriction_type": "",
        "geometrie": "", "von": "2026-10-01", "bis": "2026-10-02",
    }
    assert _filter_query(filters) == "status=planned&zeitraum=eigen&von=2026-10-01&bis=2026-10-02"
    assert _filter_query(filters, "zeitraum") == "status=planned"


# --- Integration: Navigation, Filter, Konsole, Kennzahlen -------------------------------------------------



_POINT = json.dumps({"type": "Point", "coordinates": [9.75, 47.47]})


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _drei_sperren(tag: str) -> dict[str, RoadClosure]:
    now = _now()
    return {
        "bald": _closure(
            title=f"{tag} bald", valid_from=now + timedelta(days=10), valid_until=now + timedelta(days=12),
            geometry_geojson=_POINT,
        ),
        "aktiv": _closure(title=f"{tag} aktiv", valid_from=now - timedelta(days=1), geometry_geojson=_POINT),
        "spaet": _closure(title=f"{tag} spaet", valid_from=now + timedelta(days=60), geometry_geojson=_POINT),
    }


def _titel_in_karte(client, query: str) -> set[str]:
    data = client.get(f"/strassensperren/karte.json?{query}").json()
    return {feature["properties"]["title"] for feature in data["features"]}


def test_seite_hat_zurueck_und_untertabs_und_hx_liefert_partial(client):
    _login(client, _setup_user("readonly"))
    page = client.get("/strassensperren?reset=1")
    assert page.status_code == 200
    assert 'href="/" aria-label="Konsole"' in page.text
    assert "/strassensperren/status" in page.text and "geometrie=pruefen" in page.text
    partial = client.get("/strassensperren?status=planned", headers={"HX-Request": "true"})
    assert partial.status_code == 200
    assert 'id="sperren-inhalt"' in partial.text
    assert 'hx-swap-oob="true"' in partial.text
    assert "<html" not in partial.text
    assert 'id="sperren-geojson" data-url="/strassensperren/karte.json?status=planned"' in partial.text


def test_filterkombination_liste_und_karte_identisch(client):
    tag = f"Kombi{uuid4().hex[:8]}"
    sperren = _drei_sperren(tag)
    _login(client, _setup_user("readonly"))
    query = f"status=planned&scope=own&zeitraum=30tage&q={tag}"
    text = client.get(f"/strassensperren?{query}").text
    assert sperren["bald"].title in text
    assert sperren["aktiv"].title not in text
    assert sperren["spaet"].title not in text
    assert _titel_in_karte(client, query) == {sperren["bald"].title}
    # Gruppen werden alle als Radio-Werte gerendert und markiert.
    assert 'name="status" value="planned" checked' in text
    assert 'name="scope" value="own" checked' in text
    assert 'name="zeitraum" value="30tage" checked' in text
    # Aktive-Filter-Pills: jede entfernt genau ihren Filter.
    assert 'href="/strassensperren?zeitraum=30tage&amp;scope=own&amp;q=' in text  # Status entfernt
    assert "Zurücksetzen" in text


def test_eigener_zeitraum(client):
    tag = f"Eigen{uuid4().hex[:8]}"
    sperren = _drei_sperren(tag)
    _login(client, _setup_user("readonly"))
    start = (_now() + timedelta(days=50)).date().isoformat()
    ende = (_now() + timedelta(days=70)).date().isoformat()
    query = f"status=all&zeitraum=eigen&von={start}&bis={ende}&q={tag}"
    text = client.get(f"/strassensperren?{query}").text
    assert sperren["spaet"].title in text
    assert sperren["bald"].title not in text
    assert f'name="von" value="{start}"' in text
    # "aktiv" hat kein Ende -> überschneidet sich mit jedem Zeitraum
    assert _titel_in_karte(client, query) == {sperren["spaet"].title, sperren["aktiv"].title}


def test_filter_werden_gemerkt_und_zurueckgesetzt(client):
    tag = f"Merk{uuid4().hex[:8]}"
    sperren = _drei_sperren(tag)
    _login(client, _setup_user("readonly"))
    response = client.get(f"/strassensperren?status=planned&q={tag}")
    assert "sperren_filter" in response.headers.get("set-cookie", "")
    ohne_query = client.get("/strassensperren").text
    assert sperren["bald"].title in ohne_query
    assert sperren["aktiv"].title not in ohne_query
    detail = client.get(f"/strassensperren/{sperren['aktiv'].id}").text
    assert f'href="/strassensperren?status=planned&amp;q={tag}"' in detail
    status = client.get(f"/strassensperren/status?status=planned&q={tag}").text
    assert f'href="/strassensperren?status=planned&amp;q={tag}"' in status
    reset = client.get("/strassensperren?reset=1")
    gesetzt = reset.headers.get("set-cookie", "")
    assert 'sperren_filter=""' in gesetzt or "Max-Age=0" in gesetzt
    client.cookies.delete("sperren_filter", path="/strassensperren")
    nach_reset = client.get(f"/strassensperren?q={tag}").text
    assert sperren["aktiv"].title in nach_reset


def test_manipuliertes_cookie_wird_normalisiert(client):
    _login(client, _setup_user("readonly"))
    client.cookies.set("sperren_filter", 'status=<script>&scope=evil&q="x', path="/strassensperren")
    response = client.get("/strassensperren")
    assert response.status_code == 200
    assert "<script>" not in response.text.split("<body", 1)[-1].split("</form>", 1)[0]
    assert 'name="status" value="current" checked' in response.text
    assert 'name="scope" value="all" checked' in response.text


def test_bearbeiten_hat_zurueck_zum_detail(client):
    closure = _closure(title=f"Edit{uuid4().hex[:8]}")
    _login(client, _setup_user("objekt_verwalter"))
    text = client.get(f"/strassensperren/{closure.id}/bearbeiten").text
    link = f'href="/strassensperren/{closure.id}">'
    assert link + "&larr; Detail" in text or link + "← Detail" in text


def test_liste_alias_bleibt_kompatibel(client):
    tag = f"Alias{uuid4().hex[:8]}"
    sperren = _drei_sperren(tag)
    _login(client, _setup_user("readonly"))
    text = client.get(f"/strassensperren/liste?status=active&q={tag}").text
    assert sperren["aktiv"].title in text and sperren["bald"].title not in text


def test_konsole_zeigt_kachel_mit_kennzahlen(client):
    _login(client, _setup_user("readonly"))
    text = client.get("/").text
    assert 'href="/strassensperren" class="module-btn"' in text
    assert "module-btn__badge" in text and " aktiv · " in text


def test_konsole_ohne_kachel_wenn_modul_aus(client):
    _login(client, _setup_user("readonly", enabled=False))
    try:
        text = client.get("/").text
        assert 'href="/strassensperren" class="module-btn"' not in text
    finally:
        _setup_user("readonly", enabled=True)  # Org-Schalter für folgende Tests wieder an


def test_kennzahlen_und_scope_public_ohne_partnersperren(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"kz-{uuid4().hex}", name="Kennzahlen-Org", timezone="Europe/Vienna")
        partner = FireDept(slug=f"kzp-{uuid4().hex}", name="Partner-Org", timezone="Europe/Vienna")
        db.add_all([org, partner])
        db.flush()
        db.add_all([OrgSettings(org_id=org.id, strassensperren_modul_aktiv=True),
                    OrgSettings(org_id=partner.id, strassensperren_modul_aktiv=True)])
        db.add(OrgPartner(org_id=partner.id, partner_org_id=org.id))
        now = _now()

        def add(org_id, **werte):
            values = {"org_id": org_id, "title": "KZ", "street": "Weg", "restriction_type": "closed",
                      "valid_from": now - timedelta(hours=2)}
            values.update(werte)
            closure = RoadClosure(**values)
            db.add(closure)
            db.flush()
            return closure

        add(org.id, valid_until=now + timedelta(days=3), geometry_status="needs_review")
        add(org.id, restriction_type="partial")
        add(org.id, valid_from=now + timedelta(days=5))
        add(org.id, cancelled_at=now)
        add(org.id, valid_from=now - timedelta(days=5), valid_until=now - timedelta(days=1))
        geteilt = add(partner.id)
        db.add(RoadClosureShare(road_closure_id=geteilt.id, org_id=org.id))
        db.flush()

        alle = kennzahlen(db, org, scope="all", now=now)
        assert alle["aktiv"] == 3
        assert alle["geplant"] == 1
        assert alle["vollsperren_aktiv"] == 2
        assert alle["endet_in_7_tagen"] == 1
        assert alle["geometrie_pruefen"] == 1
        assert alle["nach_typ"] == {"closed": 3, "partial": 1}
        assert alle["naechste_aenderung"] is not None

        public = kennzahlen(db, org, scope="public", now=now)
        assert public["aktiv"] == 2
        assert public["geometrie_pruefen"] == 0
    finally:
        db.rollback()
        db.close()
