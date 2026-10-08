"""Statusseite, Infoscreen und externe Status-/Infoscreen-Zugänge der Straßensperren."""

import json
import re
from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.invitation import OrgPartner
from app.models.master import FireDept
from app.models.road_closure import RoadClosure, RoadClosureAccessToken, RoadClosureShare
from app.models.user import AuditLog
from app.services.road_closure_token_service import normalize_berechtigungen
from tests.test_strassensperren_freigaben import GEHEIM, _modul_an, _now, _set_token, _sperre, _token
from tests.test_strassensperren_ui import _login, _setup_user


def _daten(response) -> dict:
    match = re.search(r'<script type="application/json" id="sperren-daten">(.*?)</script>', response.text, re.S)
    assert match, "eingebettete Daten fehlen"
    return json.loads(match.group(1))


def _titles(data: dict) -> set[str]:
    return {item["title"] for item in data["sperren"]}


def _partner_sperre(org_id: int, tag: str) -> RoadClosure:
    """Sperre einer Nachbar-Org, die für org_id freigegeben ist."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        partner = FireDept(slug=f"partner-{uuid4().hex}", name=f"Partner {tag}", timezone="Europe/Vienna")
        db.add(partner)
        db.flush()
        db.add(OrgPartner(org_id=partner.id, partner_org_id=org_id))
        closure = RoadClosure(org_id=partner.id, title=f"{tag} partner", valid_from=_now() - timedelta(hours=1),
                              restriction_type="closed", description=GEHEIM)
        db.add(closure)
        db.flush()
        db.add(RoadClosureShare(road_closure_id=closure.id, org_id=org_id))
        db.commit()
        db.refresh(closure)
        db.expunge(closure)
        return closure
    finally:
        db.close()


def _assert_whitelist(text: str) -> None:
    assert GEHEIM not in text


# --- Intern -----------------------------------------------------------------------------------------------------


def test_interne_statusseite_mit_daten_filtern_und_ohne_interna(client) -> None:
    tag = f"Intern{uuid4().hex[:8]}"
    aktiv = _sperre(title=f"{tag} aktiv")
    geplant = _sperre(title=f"{tag} geplant", valid_from=_now() + timedelta(days=3))
    partner = _partner_sperre(1, tag)
    _login(client, _setup_user("readonly"))

    page = client.get(f"/strassensperren/status?q={tag}")
    assert page.status_code == 200
    assert "&larr; Übersicht" in page.text and 'id="fullscreen"' in page.text
    data = _daten(page)
    assert {aktiv.title, geplant.title, partner.title} <= _titles(data)
    assert next(item for item in data["sperren"] if item["title"] == partner.title)["nachbar"].startswith("Partner")
    _assert_whitelist(page.text)

    nur_geplant = client.get(f"/strassensperren/status/daten?status=planned&q={tag}").json()
    assert _titles(nur_geplant) == {geplant.title}
    assert set(nur_geplant) >= {"stand", "kennzahlen", "sperren", "refresh_sec", "rotation_sec"}
    assert "description" not in nur_geplant["sperren"][0]


def test_interner_infoscreen(client) -> None:
    tag = f"InfoIntern{uuid4().hex[:8]}"
    aktiv = _sperre(title=f"{tag} aktiv")
    _login(client, _setup_user("readonly"))
    page = client.get("/strassensperren/infoscreen")
    assert page.status_code == 200
    assert '<body class="infoscreen">' in page.text
    assert "Weitere Straßensperren anzeigen" not in page.text
    assert aktiv.title in _titles(client.get("/strassensperren/infoscreen/daten").json())


def test_interner_infoscreen_modul_aus(client) -> None:
    _login(client, _setup_user("readonly", enabled=False))
    try:
        assert client.get("/strassensperren/infoscreen").status_code == 404
        assert client.get("/strassensperren/status/daten").status_code == 404
    finally:
        _setup_user("readonly", enabled=True)


# --- Extern: Statusseite ------------------------------------------------------------------------------------------


def test_externe_statusseite_nur_eigene_aktuelle(client) -> None:
    tag = f"Ext{uuid4().hex[:8]}"
    aktiv = _sperre(title=f"{tag} aktiv")
    geplant = _sperre(title=f"{tag} geplant", valid_from=_now() + timedelta(days=2))
    deaktiviert = _sperre(title=f"{tag} deaktiviert", cancelled_at=_now())
    partner = _partner_sperre(1, tag)
    _id, raw = _token(aktiv, "status")
    assert raw.startswith("rcs_")
    client.cookies.clear()

    page = client.get(f"/oeffentlich/strassensperren/{raw}")
    assert page.status_code == 200
    assert 'href="/login?next=/strassensperren"' in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-robots-tag"].startswith("noindex")
    titles = _titles(_daten(page))
    assert {aktiv.title, geplant.title} <= titles
    assert deaktiviert.title not in titles and partner.title not in titles
    _assert_whitelist(page.text)

    daten = client.get(f"/oeffentlich/strassensperren/{raw}/daten")
    assert daten.status_code == 200
    assert partner.title not in daten.text
    _assert_whitelist(daten.text)


def test_externe_berechtigungen_werden_beachtet(client) -> None:
    tag = f"Rechte{uuid4().hex[:8]}"
    aktiv = _sperre(title=f"{tag} aktiv", reason="Kanalbau-Grund")
    geplant = _sperre(title=f"{tag} geplant", valid_from=_now() + timedelta(days=2))
    _id, raw = _token(aktiv, "status", berechtigungen={
        "zeige_geplante": False, "zeige_grund": False, "zeige_karte": False, "zeige_einschraenkungen": False,
    })
    client.cookies.clear()
    page = client.get(f"/oeffentlich/strassensperren/{raw}")
    data = _daten(page)
    assert aktiv.title in _titles(data) and geplant.title not in _titles(data)
    item = next(item for item in data["sperren"] if item["title"] == aktiv.title)
    assert "reason" not in item and "exceptions" not in item and "einschraenkungen" not in item
    assert "Kanalbau-Grund" not in page.text
    assert data["kennzahlen"]["geplant"] == 0
    assert 'class="panel map-panel" hidden' in page.text
    assert "<span>Geplant</span>" not in page.text


@pytest.mark.parametrize("fall", ["widerrufen", "abgelaufen", "falsche_art", "muell"])
def test_ungueltige_status_tokens(client, fall) -> None:
    closure = _sperre()
    token_id, raw = _token(closure, "infoscreen" if fall == "falsche_art" else "status")
    if fall == "widerrufen":
        _set_token(token_id, revoked_at=_now())
    elif fall == "abgelaufen":
        _set_token(token_id, expires_at=_now() - timedelta(seconds=1))
    elif fall == "muell":
        raw = "rcs_" + uuid4().hex
    client.cookies.clear()
    for url in (f"/oeffentlich/strassensperren/{raw}", f"/oeffentlich/strassensperren/{raw}/daten"):
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 404
        assert "/login" not in response.headers.get("location", "")


# --- Extern: Infoscreen ------------------------------------------------------------------------------------------


def test_rci_infoscreen_nur_oeffentliche_sperren_mit_rotation(client) -> None:
    tag = f"Rci{uuid4().hex[:8]}"
    aktiv = _sperre(title=f"{tag} aktiv")
    partner = _partner_sperre(1, tag)
    _id, raw = _token(aktiv, "infoscreen", berechtigungen={"rotation_sec": 20, "refresh_sec": 5})
    assert raw.startswith("rci_")
    client.cookies.clear()
    page = client.get(f"/infoscreen/strassensperren/{raw}")
    assert page.status_code == 200 and '<body class="infoscreen">' in page.text
    data = client.get(f"/infoscreen/strassensperren/{raw}/daten").json()
    assert aktiv.title in _titles(data) and partner.title not in _titles(data)
    assert data["rotation_sec"] == 20
    assert data["refresh_sec"] == 30  # auf Minimum begrenzt
    _assert_whitelist(page.text)


def test_rci_mit_status_token_wird_abgelehnt(client) -> None:
    closure = _sperre()
    _id, raw = _token(closure, "status")
    client.cookies.clear()
    fake = "rci_" + raw[4:]
    assert client.get(f"/infoscreen/strassensperren/{fake}").status_code == 404
    assert client.get(f"/infoscreen/strassensperren/{fake}/daten").status_code == 404


def test_normalize_berechtigungen() -> None:
    werte = normalize_berechtigungen("infoscreen", {"rotation_sec": "9999", "refresh_sec": "1", "zeige_karte": ""})
    assert werte["rotation_sec"] == 600 and werte["refresh_sec"] == 30 and werte["zeige_karte"] is False
    assert normalize_berechtigungen("status", {"rotation_sec": 50})["rotation_sec"] == 0
    assert normalize_berechtigungen("status", {"refresh_sec": "abc"})["refresh_sec"] == 60


# --- Verwaltung der Zugänge -------------------------------------------------------------------------------------


def test_admin_erzeugt_und_widerruft_zugaenge(client) -> None:
    admin = _setup_user("org_admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        _modul_an(db, admin.org_id)
        db.commit()
    finally:
        db.close()
    _login(client, admin)
    page = client.get("/admin/settings/strassensperren-infoscreen")
    assert page.status_code == 200
    assert "Straßensperren – Externe Zugänge" in page.text
    assert 'href="/admin/settings/strassensperren-infoscreen"' in page.text  # Tab
    csrf = client.cookies.get("ec_csrf")
    label = f"Foyer {uuid4().hex[:6]}"
    response = client.post("/admin/settings/strassensperren-infoscreen/neu", data={
        "_csrf": csrf, "art": "infoscreen", "label": label, "zeige_karte": "1", "zeige_geplante": "1",
        "rotation_sec": "15", "refresh_sec": "60",
    }, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/admin/settings/strassensperren-infoscreen")
    assert label in page.text and "/infoscreen/strassensperren/rci_" in page.text
    client.post("/admin/settings/strassensperren-infoscreen/neu", data={"_csrf": csrf, "art": "status", "label": label})
    assert "/oeffentlich/strassensperren/rcs_" in client.get("/admin/settings/strassensperren-infoscreen").text
    assert client.post("/admin/settings/strassensperren-infoscreen/neu",
                       data={"_csrf": csrf, "art": "detail"}).status_code == 400

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        token = db.query(RoadClosureAccessToken).filter_by(label=label, art="infoscreen").one()
        token_id = token.id
        assert json.loads(token.berechtigungen_json)["rotation_sec"] == 15
        assert db.query(AuditLog).filter_by(action="road_closure.token_created", org_id=admin.org_id).count() >= 2
    finally:
        db.close()
    assert client.post(f"/admin/settings/strassensperren-infoscreen/{token_id}/widerrufen",
                       data={"_csrf": csrf}, follow_redirects=False).status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(RoadClosureAccessToken, token_id).revoked_at is not None
    finally:
        db.close()


def test_admin_seite_nur_fuer_org_admin_und_fremde_tokens_404(client) -> None:
    for rolle in ("readonly", "objekt_verwalter"):
        _login(client, _setup_user(rolle))
        assert client.get("/admin/settings/strassensperren-infoscreen").status_code == 403
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        fremd = FireDept(slug=f"fremd-{uuid4().hex}", name="Fremd", timezone="Europe/Vienna")
        db.add(fremd)
        db.flush()
        _modul_an(db, fremd.id)
        from app.services.road_closure_token_service import create_token

        token, _raw = create_token(db, fremd.id, "status", None)
        db.commit()
        fremd_id = token.id
    finally:
        db.close()
    _login(client, _setup_user("org_admin"))
    response = client.post(f"/admin/settings/strassensperren-infoscreen/{fremd_id}/widerrufen",
                           data={"_csrf": client.cookies.get("ec_csrf")})
    assert response.status_code == 404


# --- CSP ---------------------------------------------------------------------------------------------------------


def test_csp_erlaubt_was_die_seite_laedt(client) -> None:
    closure = _sperre()
    _id, raw = _token(closure, "status")
    client.cookies.clear()
    csp = client.get(f"/oeffentlich/strassensperren/{raw}").headers["content-security-policy"]
    directives = {part.strip().split(" ", 1)[0]: part for part in csp.split(";") if part.strip()}
    for directive in ("script-src", "style-src", "img-src", "connect-src", "font-src"):
        assert "'self'" in directives[directive], directive
    assert "frame-ancestors" in directives
