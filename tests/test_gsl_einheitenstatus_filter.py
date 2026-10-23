"""GSL-06 (vereinfachter Einheitenstatus) und GSL-07 (eigene/fremde Einheiten)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit, MajorIncident
from app.models.master import FireDept, VehicleMaster
from app.services import resource_service
from app.services.resource_service import einheit_ampel
from tests.test_einheit_api import _als, _daten, _device


def _d(status, status_at=None):
    return SimpleNamespace(einheit_status=status, status_at=status_at)


# ── GSL-06: Ableitung ────────────────────────────────────────────────────────

def test_ampel_rot_orange_gruen_wie_beim_fahrzeugstatus():
    assert einheit_ampel("bereitgestellt", [])["code"] == "einsatzbereit"
    assert einheit_ampel("bereitgestellt", [_d("zugewiesen"), _d("bestaetigt")])["code"] == "einsatzbereit"
    assert einheit_ampel("bereitgestellt", [_d("anfahrt")])["code"] == "anfahrt"
    assert einheit_ampel("bereitgestellt", [_d("vor_ort")])["code"] == "am_einsatzort"
    assert einheit_ampel("im_einsatz", [_d("in_arbeit")])["code"] == "am_einsatzort"
    css = {c: einheit_ampel("bereitgestellt", d)["css"] for c, d in (
        ("rot", []), ("orange", [_d("anfahrt")]), ("gruen", [_d("vor_ort")]))}
    assert css == {
        "rot": "unit-status-einsatzbereit",
        "orange": "unit-status-einsatz-uebernommen",
        "gruen": "unit-status-am-einsatzort",
    }


def test_ampel_hoechster_fortschritt_gewinnt_und_labels():
    a = einheit_ampel("bereitgestellt", [_d("anfahrt"), _d("vor_ort")])
    assert (a["code"], a["label"]) == ("am_einsatzort", "Am Einsatzort")
    assert einheit_ampel("bereitgestellt", [_d("anfahrt")])["label"] == "Auf Anfahrt"
    assert einheit_ampel("bereitgestellt", [])["label"] == "Einsatzbereit"
    # Ressource im Einsatz ohne offenen Auftrag: gruen "Im Einsatz"
    im = einheit_ampel("im_einsatz", [])
    assert (im["code"], im["label"], im["css"]) == ("im_einsatz", "Im Einsatz", "unit-status-am-einsatzort")


def test_ampel_angefordert_und_abgerueckt_bleiben_neutral_und_zeitpunkt_wird_uebernommen():
    for status in ("angefordert", "abgerueckt"):
        a = einheit_ampel(status, [_d("vor_ort")])
        assert a["css"] == "" and a["code"] == status
    t1, t2 = datetime(2026, 10, 22, 10, 0), datetime(2026, 10, 22, 11, 30)
    assert einheit_ampel("bereitgestellt", [_d("anfahrt", t1)], t2)["seit"] == t1
    assert einheit_ampel("bereitgestellt", [], t2)["seit"] == t2


# ── GSL-06: Anzeige in Kraefteuebersicht und Einheitenansicht ─────────────────

def test_einheitenansicht_liefert_ampel_im_kopf_und_aendert_sie_mit_dem_auftragsstatus(client, setup_db):
    x = _daten()
    _device(client, x)
    kopf = client.get("/einheit/api/zustand").json()["kopf"]
    assert kopf["ampel"]["code"] == "einsatzbereit" and kopf["ampel"]["css"] == "unit-status-einsatzbereit"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        d = db.get(EinheitSiteDispatch, x["d1"])
        d.einheit_status = "anfahrt"
        d.status_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    finally:
        db.close()
    kopf = client.get("/einheit/api/zustand").json()["kopf"]
    assert (kopf["ampel"]["code"], kopf["ampel"]["label"]) == ("anfahrt", "Auf Anfahrt")
    assert kopf["ampel"]["seit"]


def test_kraefteuebersicht_zeigt_status_farbe_bezeichnung_und_eigen_kennzeichnung(client, setup_db):
    x = _daten()
    _als(client, __import__("app.core.security", fromlist=["sign_session"]).sign_session(x["admin"]))
    seite = client.get(f"/lage/{x['lage']}/ressourcen/kraefteuebersicht")
    assert seite.status_code == 200
    assert 'data-ampel="einsatzbereit"' in seite.text and "unit-status-einsatzbereit" in seite.text
    assert 'data-eigen="1"' in seite.text


# ── GSL-07: eigene und fremde Einheiten ───────────────────────────────────────

def test_eigene_und_fremde_einheiten_werden_unterschieden():
    s = uuid4().hex[:8]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"eig-{s}", name="Eigen")
        andere = FireDept(slug=f"fre-{s}", name="Andere")
        db.add_all([org, andere])
        db.flush()
        eigenes_fzg = VehicleMaster(dept_id=org.id, code="TLF", name="TLF", type="TLF")
        fremdes_fzg = VehicleMaster(dept_id=andere.id, code="RLF", name="RLF", type="RLF")
        db.add_all([eigenes_fzg, fremdes_fzg])
        lage = MajorIncident(org_id=org.id, name="Lage")
        db.add(lage)
        db.flush()
        auto = LageEinheit(lage_id=lage.id, label="Auto", is_from_org=True)
        mit_fzg = LageEinheit(lage_id=lage.id, label="Mit eigenem Fzg", vehicle_id=eigenes_fzg.id)
        fremd_fzg = LageEinheit(lage_id=lage.id, label="Fremdes Fzg", vehicle_id=fremdes_fzg.id)
        extern = LageEinheit(lage_id=lage.id, label="Rotes Kreuz", resource_type="extern", org_name="RK")
        beschriftet = LageEinheit(lage_id=lage.id, label="Nachbarwehr", org_name="FF Nachbar")
        frei = LageEinheit(lage_id=lage.id, label="Materialcontainer", resource_type="material")
        db.add_all([auto, mit_fzg, fremd_fzg, extern, beschriftet, frei])
        db.commit()
        db.refresh(lage)
        eigene = resource_service.eigene_einheit_ids(db, lage)
        assert eigene == {auto.id, mit_fzg.id, frei.id}
        assert {fremd_fzg.id, extern.id, beschriftet.id}.isdisjoint(eigene)
    finally:
        db.close()


def test_filterleiste_ist_standardmaessig_auf_eigene_und_blendet_nur_aus():
    html = Path("app/templates/incident_major/ressourcen.html").read_text(encoding="utf-8")
    assert 'x-data="ressourcenFilter(' in html and ":data-res-filter=\"modus\"" in html
    assert '[data-res-filter="eigene"] .einh-card[data-eigen="0"]' in html
    js = Path("app/static/js/ressourcen_filter.js").read_text(encoding="utf-8")
    assert "modus: 'eigene'" in js and "sessionStorage" in js
    # reine Anzeige: der Filter sendet keine Anfragen
    assert "fetch(" not in js and "htmx" not in js
