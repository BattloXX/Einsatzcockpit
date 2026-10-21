"""Abfragevertrag, Live-Rechte, veralteter Index und hybride Rangbildung."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentOrg, Message, Task
from app.models.katplan import KatplanDokument, KatplanDokumentSeite, KatplanElement, KatplanPlan
from app.models.major_incident import (
    CommLogEntry, IncidentSite, LageEinheit, LageJournalEntry, MajorIncident, SiteLogEntry, SitePhase,
)
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import Objekt, ObjektBMA, ObjektDokument, ObjektDokumentSeite
from app.models.road_closure import RoadClosure
from app.models.suche import SearchDocument, SearchOutbox
from app.models.user import User
from app.models.wasserstelle import Wasserstelle
from app.services.katplan_freigabe_service import freigeben
from app.services.suche import abfrage
from app.services.suche.abfrage import fusioniere, suche
from app.services.suche.indexer import indexiere, neuaufbau
from app.services.suche.vertrag import SuchAnfrage, Treffer
from tests.conftest import TestingSession


def _user(org, rolle="readonly"):
    return SimpleNamespace(org_id=org.id, roles=[SimpleNamespace(code=rolle)])


@pytest.fixture
def daten():
    with TestingSession() as db:
        set_tenant_context(db, None)
        a, b = [FireDept(slug=uuid4().hex, name=f"Suche {name}") for name in ("A", "B")]
        db.add_all([a, b])
        db.flush()
        for org in (a, b):
            db.add(OrgSettings(org_id=org.id, suche_aktiv=True, objekt_module_enabled=True,
                               katplan_modul_aktiv=True, strassensperren_modul_aktiv=True))
        for key in ("objekt_module_enabled", "katplan_module_enabled", "strassensperren_module_enabled"):
            flag = db.query(SystemSettings).filter_by(key=key).first()
            if flag is None:
                db.add(SystemSettings(key=key, value="true"))
            else:
                flag.value = "true"
        o = Objekt(org_id=a.id, nummer=42, name="Fabrik", status="freigegeben", informationen="Brandmeldezentrale")
        db.add(o)
        db.flush()
        yield db, a, b, o, _user(a)
        db.rollback()


def _suche(db, user, q="Brand", **kwargs):
    db.flush()
    return suche(db, user, SuchAnfrage(q=q, **kwargs))


def _lage(db, org, name="Hochwasser"):
    lage = MajorIncident(org_id=org.id, name=name)
    db.add(lage)
    db.flush()
    return lage


def _stelle(db, org, lage, name="Brand Werk", **kwargs):
    site = IncidentSite(org_id=org.id, major_incident_id=lage.id, bezeichnung=name, **kwargs)
    db.add(site)
    db.flush()
    return site


def test_objektnummer_zuerst(daten):
    db, a, _, o, user = daten
    db.add(Objekt(org_id=a.id, nummer=99, name="42 im Text", status="freigegeben"))
    db.flush()
    neuaufbau(db, a.id)
    result = _suche(db, user, "42")
    assert (result.treffer[0].quelle, result.treffer[0].quell_id) == ("objekt", str(o.id))
    assert "exakt" in result.treffer[0].grund
    assert result.treffer[0].url == f"/objekte/{o.id}"
    assert not result.teilweise


def test_synonym_und_archivierter_index(daten):
    db, a, _, o, user = daten
    o.informationen = "Stammdaten ohne Suchbegriff"
    db.add(ObjektBMA(org_id=a.id, objekt_id=o.id, bmz_standort="Empfang"))
    db.flush()
    neuaufbau(db, a.id)
    result = _suche(db, user, "bmz", typen=["objekt"])
    assert result.treffer[0].quell_id == str(o.id)
    detail = _suche(db, user, "Empfang", typen=["objekt"]).treffer[0]
    assert detail.fundstelle == "Abschnitt BMA"
    assert "Empfang" in detail.auszug
    o.status = "archiviert"
    assert not _suche(db, user, "bmz").treffer
    assert db.scalar(select(SearchDocument).where(SearchDocument.org_id == a.id)) is not None


@pytest.mark.parametrize("aenderung", ["freigabe", "version", "objekt", "loeschung"])
def test_dokument_live_freigabe(daten, aenderung):
    db, a, _, o, user = daten
    d = ObjektDokument(org_id=a.id, objekt_id=o.id, dateiname_original="Plan.pdf", pfad="test.pdf")
    db.add(d)
    db.flush()
    s = ObjektDokumentSeite(org_id=a.id, objekt_id=o.id, dokument_id=d.id, seiten_nr=3,
                            volltext="Spezialfund BMZ")
    db.add(s)
    db.flush()
    indexiere(db, a.id, "objekt_dokument_seite", str(s.id))
    result = _suche(db, user, "Spezialfund")
    assert result.treffer[0].url == f"/objekte/{o.id}/dokumente/viewer?seite={s.id}"
    assert result.treffer[0].fundstelle == "Seite 3"
    if aenderung == "freigabe":
        d.freigabe_status = "entwurf"
    elif aenderung == "version":
        d.ist_aktuelle_version = False
    elif aenderung == "objekt":
        o.status = "archiviert"
    else:
        db.delete(s)
    assert not _suche(db, user, "Spezialfund").treffer


def _plan(db, org):
    plan = KatplanPlan(org_id=org.id, titel="Notfallplan", status="in_pruefung")
    freigeber = User(org_id=org.id, username=uuid4().hex, password_hash="test", display_name="Test")
    db.add_all([plan, freigeber])
    db.flush()
    element = KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", kennung="I1",
                            titel="Geheimlager", vertraulich=True)
    db.add(element)
    db.flush()
    return plan, freigeber, element


def test_vertraulich_und_versionswechsel(daten):
    db, a, _, _, user = daten
    plan, freigeber, element = _plan(db, a)
    freigeben(db, plan, freigeber)
    db.flush()
    neuaufbau(db, a.id)
    assert not _suche(db, user, "Geheimlager").treffer
    leiter = _user(a, "incident_leader")
    result = _suche(db, leiter, "I1")
    assert result.treffer[0].grund[0] == "exakt"
    assert "exakt" in _suche(db, leiter, "I", typen=["katplan_element"]).treffer[0].grund
    assert result.treffer[0].url == f"/plaene/{plan.id}#element-{element.uuid}"
    plan.status = "in_pruefung"
    element.titel = "Neuer Stand"
    db.flush()
    freigeben(db, plan, freigeber)
    assert not _suche(db, leiter, "Geheimlager").treffer


def test_gsl_scope_und_fremde_org(daten):
    db, a, b, _, user = daten
    lage, andere, fremd = _lage(db, a), _lage(db, a), _lage(db, b)
    site = _stelle(db, a, lage)
    _stelle(db, a, andere)
    _stelle(db, b, fremd)
    db.add_all([SiteLogEntry(incident_site_id=site.id, text="Brandbericht"),
               LageJournalEntry(major_incident_id=andere.id, text="Brandbericht anders")])
    result = _suche(db, user, kontext="gsl", kontext_id=lage.id)
    assert {t.quelle for t in result.treffer} == {"gsl_stelle", "gsl_meldung"}
    assert [t.quell_id for t in result.treffer if t.quelle == "gsl_stelle"] == [str(site.id)]
    with pytest.raises(PermissionError):
        _suche(db, user, kontext="gsl", kontext_id=fremd.id)


@pytest.mark.parametrize("phase", [SitePhase.erledigt, SitePhase.abgebrochen])
def test_gsl_erledigt(daten, phase):
    db, a, _, _, user = daten
    lage = _lage(db, a)
    _stelle(db, a, lage, phase=phase)
    assert not _suche(db, user, kontext="gsl", kontext_id=lage.id).treffer
    result = _suche(db, user, kontext="gsl", kontext_id=lage.id, inkl_erledigt=True)
    assert result.treffer[0].ist_aktuell is False
    assert result.treffer[0].status


def test_journal_funk_privatsphaere_und_flags(daten):
    db, a, _, _, user = daten
    lage = _lage(db, a)
    site = _stelle(db, a, lage, name="Werk")
    db.add_all([
        SiteLogEntry(incident_site_id=site.id, text="Brand Notiz", author_name="PrivaterAutor"),
        LageJournalEntry(major_incident_id=lage.id, text="Brand Journal", body_html="<p>Brand Inhalt</p>",
                         author_name="PrivaterAutor", partner_from="PrivaterPartner"),
        LageJournalEntry(major_incident_id=lage.id, text="Brand storniert", storniert_at=datetime.now(UTC)),
        CommLogEntry(major_incident_id=lage.id, direction="in", message="Brand Funk",
                     author_name="PrivaterAutor", partner="PrivaterPartner"),
    ])
    result = _suche(db, user, kontext="gsl", kontext_id=lage.id)
    assert len(result.treffer) == 3
    assert "Privater" not in result.model_dump_json()
    assert "storniert" not in result.model_dump_json()
    assert "<p>" not in result.model_dump_json()
    settings = db.query(OrgSettings).filter_by(org_id=a.id).one()
    settings.mi_feature_funkjournal = False
    result = _suche(db, user, kontext="gsl", kontext_id=lage.id)
    assert len(result.treffer) == 2
    assert all(not t.quell_id.startswith("funk:") for t in result.treffer)


def test_einheiten_freigegeben_und_ohne_personal(daten):
    db, a, _, _, user = daten
    lage = _lage(db, a)
    site = _stelle(db, a, lage, name="Werk")
    e = LageEinheit(lage_id=lage.id, label="Pumpe", funkrufname="Florian", org_name="Feuerwehr",
                    incident_site_id=site.id, commander_label="PrivaterKommandant")
    db.add(e)
    result = _suche(db, user, "Florian", kontext="gsl", kontext_id=lage.id)
    assert result.treffer[0].untertitel == "Werk"
    assert "PrivaterKommandant" not in result.model_dump_json()
    e.released_at = datetime.now(UTC)
    assert not _suche(db, user, "Florian", kontext="gsl", kontext_id=lage.id).treffer
    assert not _suche(db, user, "Florian", kontext="gsl", kontext_id=lage.id,
                      inkl_erledigt=True).treffer[0].ist_aktuell


@pytest.mark.parametrize("zustand", ["geplant", "abgelaufen", "storniert"])
def test_sperre_gueltigkeitszeitraum(daten, zustand):
    db, a, _, _, user = daten
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    s = RoadClosure(org_id=a.id, title="Brand Zufahrt", restriction_type="closed",
                    valid_from=jetzt - timedelta(days=2))
    if zustand == "geplant":
        s.valid_from = jetzt + timedelta(days=1)
    elif zustand == "abgelaufen":
        s.valid_until = jetzt - timedelta(days=1)
    else:
        s.cancelled_at = jetzt
    db.add(s)
    assert not _suche(db, user).treffer
    result = _suche(db, user, inkl_erledigt=True)
    assert result.treffer[0].ist_aktuell is False
    assert result.treffer[0].url == f"/strassensperren/{s.id}"


def test_wasser_defekt_geo_und_sortierung(daten):
    db, a, _, _, user = daten
    db.add_all([
        Wasserstelle(org_id=a.id, bezeichnung="Hydrant fern", lat=48, lng=10),
        Wasserstelle(org_id=a.id, bezeichnung="Hydrant defekt", lat=47.001, lng=9,
                      status="defekt", aktiv=False),
        Wasserstelle(org_id=a.id, bezeichnung="Hydrant nah", lat=47, lng=9),
    ])
    result = _suche(db, user, "Hydrant", lat=47, lng=9, radius_m=500, inkl_erledigt=True)
    assert [t.titel for t in result.treffer] == ["Hydrant nah", "Hydrant defekt"]
    assert result.treffer[1].status == "Defekt"
    assert 100 < result.treffer[1].distanz_m < 120
    assert result.treffer[0].distanz_m == 0
    assert result.treffer[0].url == ""
    assert len(_suche(db, user, "Hydrant", lat=47, lng=9, radius_m=500).treffer) == 1


def test_einsatz_sichtbarkeit_kollaboration_und_exakt(daten):
    db, a, b, _, user = daten
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    eigen = Incident(primary_org_id=a.id, reason="Brand Eigen")
    gemeinsam = Incident(primary_org_id=b.id, reason="Brand Gemeinsam", lis_operation_number="F12345678",
                          started_at=jetzt - timedelta(days=1000))
    fremd = Incident(primary_org_id=b.id, reason="Brand Fremd")
    db.add_all([eigen, gemeinsam, fremd])
    db.flush()
    db.add(IncidentOrg(incident_id=gemeinsam.id, org_id=a.id))
    result = _suche(db, user)
    assert [t.quell_id for t in result.treffer] == [str(eigen.id)]
    result = _suche(db, user, "F12345678")
    assert result.treffer[0].quell_id == str(gemeinsam.id)
    assert "exakt" in result.treffer[0].grund
    gemeinsam.started_at = jetzt
    assert {t.quell_id for t in _suche(db, user).treffer} == {str(eigen.id), str(gemeinsam.id)}
    with pytest.raises(PermissionError):
        _suche(db, user, kontext="einsatz", kontext_id=fremd.id)


def test_einsatz_meldungen_scope_und_privatsphaere(daten):
    db, a, _, _, user = daten
    e, andere = Incident(primary_org_id=a.id), Incident(primary_org_id=a.id)
    db.add_all([e, andere])
    db.flush()
    db.add_all([Task(incident_id=e.id, title="Brand Auftrag"),
               Message(incident_id=e.id, title="Brand Meldung", author_name="PrivaterAutor"),
               Message(incident_id=andere.id, title="Brand Falsch")])
    result = _suche(db, user, kontext="einsatz", kontext_id=e.id, typen=["einsatz_meldung"])
    assert len(result.treffer) == 2
    assert "PrivaterAutor" not in result.model_dump_json()
    assert "Falsch" not in result.model_dump_json()
    assert all("#" in t.url for t in result.treffer)


def test_org_trennung_index_live(daten):
    db, a, b, _, user = daten
    db.add_all([Objekt(org_id=b.id, name="Brand Fremd", status="freigegeben"),
               Wasserstelle(org_id=b.id, bezeichnung="Brand Fremd"),
               Wasserstelle(org_id=a.id, bezeichnung="Brand Eigen")])
    db.flush()
    neuaufbau(db, a.id)
    neuaufbau(db, b.id)
    result = _suche(db, user)
    assert {t.titel for t in result.treffer} == {"Brand Eigen", "42 Fabrik"}
    assert "Fremd" not in result.model_dump_json()


def test_stufenfehler_live_bleibt(daten, monkeypatch, caplog):
    db, _, _, _, user = daten
    def kaputt(*args, **kwargs):
        raise RuntimeError("GeheimerSuchtext")
    monkeypatch.setattr(abfrage, "volltext_kandidaten", kaputt)
    db.add(Wasserstelle(org_id=user.org_id, bezeichnung="GeheimerSuchtext"))
    result = _suche(db, user, "GeheimerSuchtext")
    assert result.teilweise and "Volltextsuche nicht verfügbar" in result.hinweise
    assert result.treffer[0].quelle == "wasserstelle"
    assert "GeheimerSuchtext" not in caplog.text


def test_index_inaktiv_live_bleibt(daten):
    db, a, _, _, user = daten
    neuaufbau(db, a.id)
    db.query(OrgSettings).filter_by(org_id=a.id).one().suche_aktiv = False
    db.add(Wasserstelle(org_id=a.id, bezeichnung="Brand Wasser"))
    result = _suche(db, user)
    assert [t.quelle for t in result.treffer] == ["wasserstelle"]
    assert "Suchindex für diese Organisation nicht aktiv" in result.hinweise
    assert "volltext" not in result.modi and "live" in result.modi
    assert not _suche(db, user, "42").treffer


@pytest.mark.parametrize("werte", [
    {"q": " "}, {"q": "x" * 501}, {"limit": 51}, {"limit": 0}, {"radius_m": 12},
    {"typen": ["ungueltig"]}, {"kontext": "einsatz"}, {"kontext": "gsl"},
    {"lat": 91, "lng": 0, "radius_m": 1}, {"lat": 0, "lng": 181, "radius_m": 1},
    {"lat": 0, "lng": 0, "radius_m": 5001}, {"lat": float("nan"), "lng": 0, "radius_m": 1},
])
def test_validierung(werte):
    with pytest.raises(ValidationError):
        SuchAnfrage(**{"q": "Brand", **werte})


def test_rrf_vereint_und_exakt_vorne():
    def t(ident, grund):
        return Treffer(quelle="objekt", quell_id=ident, titel=ident, url="", grund=[grund])
    result = fusioniere([[t("eins", "volltext"), t("beide", "volltext")],
                         [t("beide", "live")], [t("exakt", "exakt")]])
    assert [r.quell_id for r in result] == ["exakt", "beide", "eins"]
    assert result[1].grund == ["volltext", "live"]


def test_indexstand_beruecksichtigt_listener_outbox(daten):
    db, a, _, o, user = daten
    neuaufbau(db, a.id)
    result = _suche(db, user, "42")
    # Objekt-Aenderungen erzeugen automatisch offene Outbox-Zeilen.
    erwartet = db.scalar(select(func.min(SearchOutbox.erstellt_am)).where(
        SearchOutbox.org_id == a.id, SearchOutbox.status.in_(("pending", "retry")),
    ))
    assert erwartet is not None and result.index_stand == erwartet
    for zeile in db.scalars(select(SearchOutbox).where(SearchOutbox.org_id == a.id)):
        zeile.status = "done"
    result = _suche(db, user, "42")
    assert result.index_stand == db.scalar(select(func.max(SearchDocument.indexiert_am)).where(
        SearchDocument.org_id == a.id,
    ))


def test_kontext_index_opt_in_und_typfilter(daten):
    db, a, _, _, user = daten
    neuaufbau(db, a.id)
    lage = _lage(db, a)
    assert not _suche(db, user, "bmz", kontext="gsl", kontext_id=lage.id).treffer
    assert _suche(db, user, "bmz", kontext="gsl", kontext_id=lage.id, ganze_org=True).treffer
    assert _suche(db, user, "bmz", kontext="gsl", kontext_id=lage.id, typen=["objekt"]).treffer
    assert not _suche(db, user, "bmz", typen=["wasserstelle"]).treffer


def test_index_modul_und_leserechte(daten):
    db, a, _, _, user = daten
    neuaufbau(db, a.id)
    assert not _suche(db, _user(a, "unbekannt"), "42").treffer
    db.query(OrgSettings).filter_by(org_id=a.id).one().objekt_module_enabled = False
    assert not _suche(db, user, "42").treffer


def test_nachladen_nach_resolver(daten, monkeypatch):
    db, a, _, o, user = daten
    for nr in range(6):
        db.add(Objekt(org_id=a.id, nummer=100 + nr, name="Brand", status="freigegeben"))
    db.flush()
    neuaufbau(db, a.id)
    # Erste Dokumente bleiben im Index, sind aber live nicht mehr sichtbar.
    objekte = db.query(Objekt).filter(Objekt.org_id == a.id, Objekt.id != o.id).order_by(Objekt.id).all()
    for objekt in objekte[:2]:
        objekt.status = "archiviert"
    limits = []
    original = abfrage.volltext_kandidaten
    def zaehle(*args, **kwargs):
        limits.append(kwargs["limit"])
        return original(*args, **kwargs)
    monkeypatch.setattr(abfrage, "volltext_kandidaten", zaehle)
    result = _suche(db, user, limit=1, typen=["objekt"])
    assert result.treffer and result.treffer[0].quell_id == str(objekte[2].id)
    assert limits == [3, 6]


def test_katplan_quellseite_live_sichtbar(daten):
    db, a, _, _, user = daten
    plan, freigeber, _ = _plan(db, a)
    dokument = KatplanDokument(org_id=a.id, plan_id=plan.id, titel="Quelle", dateiname_original="plan.pdf",
                               pfad="plan.pdf", groesse_bytes=10, seitenzahl=1, status="bereit")
    db.add(dokument)
    db.flush()
    seite = KatplanDokumentSeite(org_id=a.id, plan_id=plan.id, dokument_id=dokument.id, seiten_nr=1,
                                 einzel_pdf_pfad="seite.pdf", volltext="EinzigartigerQuelltext")
    db.add(seite)
    db.flush()
    freigeben(db, plan, freigeber)
    db.flush()
    neuaufbau(db, a.id)
    result = _suche(db, user, "EinzigartigerQuelltext")
    assert len(result.treffer) == 1
    assert result.treffer[0].url == (
        f"/plaene/{plan.id}/quellen/{dokument.id}?seite=1&version_id={plan.aktuelle_version_id}"
    )
    dokument.archiviert = True
    assert not _suche(db, user, "EinzigartigerQuelltext").treffer


@pytest.mark.parametrize("kontext", ["einsatz", "gsl"])
def test_nahe_sperre_ohne_texttreffer(daten, kontext):
    db, a, _, _, user = daten
    e = Incident(primary_org_id=a.id, lat=47, lng=9)
    db.add(e)
    db.flush()
    lage = _lage(db, a)
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    db.add_all([
        RoadClosure(org_id=a.id, title="Bruecke", restriction_type="closed",
                    valid_from=jetzt - timedelta(days=1), geometry_geojson='{"type":"Point","coordinates":[9,47]}',
                    bbox_min_lat=47, bbox_max_lat=47, bbox_min_lng=9, bbox_max_lng=9),
        RoadClosure(org_id=a.id, title="Tunnel fern", restriction_type="closed",
                    valid_from=jetzt - timedelta(days=1), geometry_geojson='{"type":"Point","coordinates":[10,48]}',
                    bbox_min_lat=48, bbox_max_lat=48, bbox_min_lng=10, bbox_max_lng=10),
    ])
    kwargs = {"kontext": kontext, "kontext_id": e.id if kontext == "einsatz" else lage.id}
    if kontext == "gsl":
        kwargs.update(lat=47, lng=9, radius_m=1000)
    result = _suche(db, user, "anfahrt", **kwargs)
    assert [t.titel for t in result.treffer] == ["Bruecke"]
    assert not _suche(db, user, "Bruecke", kontext="gsl", kontext_id=lage.id,
                      typen=["wasserstelle"]).treffer


def test_live_synonyme_und_und_verknuepfung(daten):
    db, a, _, _, user = daten
    lage = _lage(db, a)
    _stelle(db, a, lage, name="Brandmeldezentrale Werk")
    _stelle(db, a, lage, name="Brandmeldezentrale Anderswo")
    result = _suche(db, user, "bmz Werk", kontext="gsl", kontext_id=lage.id)
    assert [t.titel for t in result.treffer] == ["Brandmeldezentrale Werk"]


def test_geo_index_ohne_koordinaten_bleibt(daten):
    db, a, _, o, user = daten
    db.add(Objekt(org_id=a.id, nummer=43, name="Fabrik fern", status="freigegeben", lat=48, lng=10))
    db.flush()
    neuaufbau(db, a.id)
    result = _suche(db, user, "Fabrik", lat=47, lng=9, radius_m=100)
    assert [t.quell_id for t in result.treffer] == [str(o.id)]
    assert result.treffer[0].distanz_m is None


def test_live_auszug_ohne_telefonnummer(daten):
    db, a, _, _, user = daten
    db.add(Incident(primary_org_id=a.id, reason="Brand Keller",
                    report_text="Brand im Keller, Anrufer erreichbar unter 0664 1234567"))
    lage = _lage(db, a)
    site = _stelle(db, a, lage)
    db.add(SiteLogEntry(incident_site_id=site.id, text="Brand gemeldet, Rueckruf +43 5574 12345"))
    treffer = _suche(db, user).treffer + _suche(db, user, kontext="gsl", kontext_id=lage.id).treffer
    assert {t.quelle for t in treffer} >= {"einsatz", "gsl_meldung"}
    for t in treffer:
        for feld in (t.titel, t.auszug):
            assert "1234567" not in feld and "12345" not in feld


def test_gsl_braucht_board_leserolle(daten):
    db, a, _, _, _ = daten
    lage = _lage(db, a)
    _stelle(db, a, lage)
    with pytest.raises(PermissionError):
        _suche(db, _user(a, "fahrtenbuch_admin"), kontext="gsl", kontext_id=lage.id)
    assert _suche(db, _user(a, "recorder"), kontext="gsl", kontext_id=lage.id).treffer
