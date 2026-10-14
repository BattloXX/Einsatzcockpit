"""End-to-end-Regressionen der lesenden GSL-Lagewerkzeuge."""

import json
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    CitizenReport,
    CommLogEntry,
    CrossSiteMarker,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageJournalEntry,
    MajorIncident,
    Sector,
)
from tests.test_mcp_gsl_ressourcen import _call, _new_seed
from tests.test_mcp_objekte import _token


def _lage(seed):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = MajorIncident(org_id=seed["org_id"], name="Lage Geheim", public_token=f"public-geheim-{uuid4().hex}")
        db.add(lage)
        db.flush()
        sector = Sector(major_incident_id=lage.id, name="Abschnitt Geheim")
        site = IncidentSite(
            major_incident_id=lage.id,
            org_id=seed["org_id"],
            sector_id=None,
            bezeichnung="Stelle Geheim",
            einsatzgrund="Wasser",
        )
        einheit = LageEinheit(lage_id=lage.id, label="Einheit Geheim", status="im_einsatz")
        db.add_all([sector, site, einheit])
        db.flush()
        site.sector_id = sector.id
        db.add_all(
            [
                EinheitSiteDispatch(einheit_id=einheit.id, site_id=site.id, dispatched_at=lage.started_at),
                LageJournalEntry(
                    major_incident_id=lage.id, text="Journal", body_html="<b>Text</b>", author_name="Autor Geheim"
                ),
                CommLogEntry(major_incident_id=lage.id, direction="in", message="Funk", author_name="Funker Geheim"),
                CitizenReport(
                    major_incident_id=lage.id,
                    description="Meldung",
                    reporter_name="Melder Geheim",
                    reporter_contact="0664 Geheim",
                    source_ip="127.0.0.1",
                    site_id=site.id,
                ),
                CrossSiteMarker(
                    major_incident_id=lage.id, org_id=seed["org_id"], title="Marker", author_name="Marker Geheim"
                ),
            ]
        )
        db.commit()
        return lage.id, site.id
    finally:
        db.close()


def test_lesende_lagewerkzeuge_und_datenschutz(client):
    seed = _new_seed(f"mcp-gsl-lage-{uuid4().hex[:8]}", {"reader": "readonly"})
    lage_id, site_id = _lage(seed)
    token = _token(client, seed, "reader")
    antworten = [
        _call(client, token, "gsl_lagen_liste"),
        _call(client, token, "gsl_lagebild"),
        _call(client, token, "gsl_einsatzstellen_liste"),
        _call(client, token, "gsl_einsatzstelle_lesen", lage_id=lage_id, site_id=site_id),
        _call(client, token, "gsl_kraefteuebersicht"),
        _call(client, token, "gsl_abschnitte"),
        _call(client, token, "gsl_journal"),
        _call(client, token, "gsl_meldungen"),
        _call(client, token, "gsl_uebergreifend_liste"),
    ]
    assert antworten[1]["lagebild"]["lage"]["id"] == lage_id
    assert antworten[2]["einsatzstellen"][0]["id"] == site_id
    assert antworten[3]["einsatzstelle"]["auftraege"][0]["dispatch_id"]
    dump = json.dumps(antworten, ensure_ascii=False)
    for geheimnis in ("Melder Geheim", "0664 Geheim", "127.0.0.1", "Autor Geheim", "Funker Geheim", "public-geheim"):
        assert geheimnis not in dump


def test_fremde_lage_und_unbekannter_bereich(client):
    own = _new_seed(f"mcp-gsl-lage-own-{uuid4().hex[:8]}", {"reader": "readonly"})
    foreign = _new_seed(f"mcp-gsl-lage-foreign-{uuid4().hex[:8]}", {"reader": "readonly"})
    own_lage, own_site = _lage(own)
    foreign_lage, _ = _lage(foreign)
    token = _token(client, own, "reader")
    assert "Lage nicht gefunden." in _call(client, token, "gsl_lagebild", lage_id=foreign_lage)["__fehler__"]
    assert (
        "Unbekannte Bereiche"
        in _call(client, token, "gsl_einsatzstelle_lesen", lage_id=own_lage, site_id=own_site, bereiche=["falsch"])[
            "__fehler__"
        ]
    )


def test_abgezogene_einheit_nicht_in_chips_aber_im_auftrag(client):
    seed = _new_seed(f"mcp-gsl-lage-abzug-{uuid4().hex[:8]}", {"reader": "readonly"})
    lage_id, site_id = _lage(seed)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dispatch = db.query(EinheitSiteDispatch).filter_by(site_id=site_id).one()
        dispatch.beendet_at = dispatch.abgezogen_at = dispatch.dispatched_at
        db.commit()
    finally:
        db.close()
    token = _token(client, seed, "reader")
    stellen = _call(client, token, "gsl_einsatzstellen_liste", lage_id=lage_id)["einsatzstellen"]
    assert stellen[0]["einheiten"] == []
    stelle = _call(client, token, "gsl_einsatzstelle_lesen", lage_id=lage_id, site_id=site_id, bereiche=["auftraege"])
    assert stelle["einsatzstelle"]["auftraege"][0]["abgezogen"]


def test_ohne_aktive_lage_und_deaktivierte_module(client):
    from app.models.master import OrgSettings

    seed = _new_seed(f"mcp-gsl-lage-modul-{uuid4().hex[:8]}", {"reader": "readonly"})
    token = _token(client, seed, "reader")
    assert "Keine aktive Lage." in _call(client, token, "gsl_lagebild")["__fehler__"]
    lage_id, _ = _lage(seed)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        settings_row = db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one()
        settings_row.mi_feature_meldungen = False
        settings_row.mi_feature_funkjournal = False
        db.commit()
    finally:
        db.close()
    assert "__fehler__" in _call(client, token, "gsl_meldungen", lage_id=lage_id)
    eintraege = _call(client, token, "gsl_journal", lage_id=lage_id)["eintraege"]
    assert eintraege and all(item["quelle"] != "funk" for item in eintraege)
    assert "offene_buergermeldungen" not in _call(client, token, "gsl_lagebild", lage_id=lage_id)["lagebild"]
