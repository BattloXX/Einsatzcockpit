"""Objektverwaltung: KI-Stammdaten-Extraktion aus Objektbeschreibungs-Seiten.

Zweiter Extraktionsdurchlauf neben der bestehenden Dokumentart-Klassifikation
(PR8, siehe test_objekt_pr8.py): wenn eine Seite als "objektinformation"
erkannt wird (typischerweise die Objektbeschreibungs-Tabelle eines
Brandschutzplans), extrahiert eine zweite KI-Anfrage strukturierte Felder für
Objekt.informationen / ObjektBMA / ObjektGefahr / ObjektMerkmal - wie die
Dokumentart-Klassifikation NIE automatisch übernommen (Review-Queue).

Deckt zusätzlich ab: Dubletten-Vermeidung (bereits Erfasstes wird weder erneut
vorgeschlagen noch bei der Übernahme überschrieben) und die Möglichkeit,
bisher unbekannte Gefahren-/Merkmal-Kategorien vorzuschlagen (werden beim
Übernehmen als neue Katalogeinträge angelegt statt verworfen).
"""
import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    KI_VORSCHLAG_OFFEN,
    KI_VORSCHLAG_UEBERNOMMEN,
    OBJEKT_STATUS_FREIGEGEBEN,
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektStammdatenVorschlag,
)
from app.services.objekt_ki_service import _bereits_vorhanden, _ohne_bereits_vorhandenes, _parse_stammdaten_antwort


# ── Antwort-Parser ────────────────────────────────────────────────────────────

def test_parse_stammdaten_antwort_gueltig():
    antwort = (
        '{"informationen_text": "Gebäudeklasse -, 1 UG, 9 OG, Fluchtniveau 20.8m", '
        '"bma_nummer": null, "bmz_standort": "BE1, UG (Feuerwehrzugang 1)", '
        '"fbf_standort": "BE1, UG BMZ (Feuerwehrzugang 1)", '
        '"laufkarten_ablageort": "FW Zugang 1", "schluesselsafe_standort": null, '
        '"gefahren": [{"piktogramm_typ": "gas", "name": null, "detail": "Sauerstofftank 2300l, Gaslager Propan"}], '
        '"merkmale": [{"code": "sprinkler", "name": null}, {"code": null, "name": null}], '
        '"begruendung": "Objektbeschreibungs-Tabelle Seite 4"}'
    )
    p = _parse_stammdaten_antwort(antwort)
    assert p is not None
    assert "Fluchtniveau" in p["informationen_text"]
    assert p["bmz_standort"] == "BE1, UG (Feuerwehrzugang 1)"
    assert p["bma_nummer"] is None
    import json
    gefahren = json.loads(p["gefahren_json"])
    assert gefahren == [{"piktogramm_typ": "gas", "name": None, "detail": "Sauerstofftank 2300l, Gaslager Propan"}]
    merkmale = json.loads(p["merkmale_json"])
    assert merkmale == [{"code": "sprinkler", "name": None}]  # leerer Eintrag (code=null, name=null) verworfen


def test_parse_stammdaten_antwort_unbekannte_kategorie_bleibt_erhalten():
    """Ein piktogramm_typ ausserhalb der 8 bekannten Icon-Typen wird verworfen
    (steuert das Rendering), ein NAME fuer eine neue Kategorie/ein neues
    Merkmal bleibt dagegen erhalten - das ist die "bisher unbekannte
    Kategorie"-Vorschlagsfaehigkeit."""
    antwort = (
        '{"gefahren": [{"piktogramm_typ": "phantasie", "name": "x", "detail": "x"}, '
        '{"piktogramm_typ": "pv", "name": "Photovoltaik-Anlage Dach", "detail": "Dachflaeche komplett belegt"}], '
        '"merkmale": [{"code": null, "name": "Regenvorhang Anlieferung"}], '
        '"begruendung": "test"}'
    )
    p = _parse_stammdaten_antwort(antwort)
    assert p is not None
    import json
    gefahren = json.loads(p["gefahren_json"])
    assert gefahren == [{"piktogramm_typ": "pv", "name": "Photovoltaik-Anlage Dach",
                         "detail": "Dachflaeche komplett belegt"}]
    merkmale = json.loads(p["merkmale_json"])
    assert merkmale == [{"code": None, "name": "Regenvorhang Anlieferung"}]


def test_parse_stammdaten_antwort_ungueltiges_json():
    assert _parse_stammdaten_antwort("kein json") is None


# ── Dubletten-Filter (_ohne_bereits_vorhandenes) ──────────────────────────────

def test_ohne_bereits_vorhandenes_entfernt_bekannte_bma_felder_und_gefahren():
    import json
    geparst = {
        "informationen_text": "1 UG, 9 OG",
        "bmz_standort": "BE1, UG",  # bereits am Objekt bekannt -> raus
        "fbf_standort": "Neuer Standort",  # noch unbekannt -> bleibt
        "bma_nummer": None, "laufkarten_ablageort": None, "schluesselsafe_standort": None,
        "gefahren_json": json.dumps([
            {"piktogramm_typ": "gas", "name": None, "detail": "x"},   # bereits erfasst -> raus
            {"piktogramm_typ": "pv", "name": None, "detail": "y"},    # neu -> bleibt
        ]),
        "merkmale_json": json.dumps([
            {"code": "sprinkler", "name": None},   # bereits erfasst -> raus
            {"code": "rwa", "name": None},          # neu -> bleibt
        ]),
        "begruendung": "test",
    }
    bereits = {
        "bma_werte": {"bmz_standort": "BE1, UG", "fbf_standort": None,
                      "bma_nummer": None, "laufkarten_ablageort": None, "schluesselsafe_standort": None},
        "gefahr_typen": {"gas"},
        "merkmal_codes": {"sprinkler"},
        "informationen": "1 UG, 9 OG bereits erfasst",
    }
    gefiltert = _ohne_bereits_vorhandenes(geparst, bereits)
    assert gefiltert["bmz_standort"] is None
    assert gefiltert["fbf_standort"] == "Neuer Standort"
    assert gefiltert["informationen_text"] is None  # bereits im Objekt-Freitext enthalten
    gefahren = json.loads(gefiltert["gefahren_json"])
    assert gefahren == [{"piktogramm_typ": "pv", "name": None, "detail": "y"}]
    merkmale = json.loads(gefiltert["merkmale_json"])
    assert merkmale == [{"code": "rwa", "name": None}]


# ── Fixture: Org mit Objekt/Seite + Gefahren-/Merkmal-Katalog ─────────────────

@pytest.fixture()
def stammdaten_db(tmp_path, monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "OBJEKT_MEDIA_DIR", str(tmp_path / "objekt_media"))

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org = FireDept(slug="stammdaten-org", name="Stammdaten Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, objekt_ki_klassifikation_enabled=True))
    db.add(GefahrenKatalog(org_id=org.id, name="Gasanschluss / Gasflaschen", piktogramm_typ="gas"))
    db.add(MerkmalKatalog(org_id=org.id, code="sprinkler", name="Sprinkleranlage", icon="💧"))
    objekt = Objekt(org_id=org.id, nummer=1, name="Meusburger Wolfurt",
                    status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt)
    db.flush()
    dokument = ObjektDokument(org_id=org.id, objekt_id=objekt.id,
                              dateiname_original="bsp.pdf", pfad="x/t/original.pdf",
                              seitenzahl=1, status="fertig")
    db.add(dokument)
    db.flush()
    seite = ObjektDokumentSeite(org_id=org.id, objekt_id=objekt.id,
                                dokument_id=dokument.id, seiten_nr=4)
    db.add(seite)
    db.commit()

    yield db, org, objekt, seite

    db.close()
    Base.metadata.drop_all(bind=engine)


def _bild_datei(seite, tmp_path):
    import io

    from PIL import Image
    from app.services.objekt_dokument_service import _storage_root
    bild_dir = _storage_root() / "x" / "t"
    bild_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (40, 60), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    (bild_dir / "seite_0004.png").write_bytes(buf.getvalue())
    seite.bild_pfad = "x/t/seite_0004.png"


def test_analysiere_seite_objektinformation_erzeugt_beide_vorschlaege(stammdaten_db, tmp_path):
    from app.services import objekt_ki_service
    db, org, objekt, seite = stammdaten_db
    _bild_datei(seite, tmp_path)
    db.commit()

    antworten = [
        # 1. Aufruf: Dokumentart-Klassifikation
        '{"dokumentart": "objektinformation", "titel": "Objektbeschreibung", '
        '"begruendung": "Tabelle mit Ankreuzfeldern"}',
        # 2. Aufruf: Stammdaten-Extraktion
        '{"informationen_text": "1 UG, 9 OG, Fluchtniveau 20.8m", '
        '"bmz_standort": "BE1, UG (Feuerwehrzugang 1)", "fbf_standort": null, '
        '"laufkarten_ablageort": null, "schluesselsafe_standort": null, '
        '"gefahren": [{"piktogramm_typ": "gas", "name": null, "detail": "Sauerstofftank 2300l"}], '
        '"merkmale": [{"code": "sprinkler", "name": null}], "begruendung": "Objektbeschreibung Seite 4"}',
    ]

    async def fake_vision(system, user, images, **kw):
        return antworten.pop(0)

    with patch("app.services.ai_service.complete_vision", side_effect=fake_vision):
        vorschlag = asyncio.run(objekt_ki_service.analysiere_seite(seite, db))
    db.commit()

    assert vorschlag is not None
    assert vorschlag.dokumentart == "objektinformation"

    stammdaten = (
        db.query(ObjektStammdatenVorschlag)
        .filter(ObjektStammdatenVorschlag.seite_id == seite.id)
        .first()
    )
    assert stammdaten is not None
    assert stammdaten.status == KI_VORSCHLAG_OFFEN
    assert stammdaten.bmz_standort == "BE1, UG (Feuerwehrzugang 1)"
    assert "Fluchtniveau" in stammdaten.informationen_text
    assert stammdaten.gefahren == [{"piktogramm_typ": "gas", "name": None, "detail": "Sauerstofftank 2300l"}]
    assert stammdaten.merkmale == [{"code": "sprinkler", "name": None}]

    # Objekt selbst bleibt unveraendert (nie Auto-Apply)
    db.refresh(objekt)
    assert objekt.informationen is None
    assert objekt.bma is None


def test_analysiere_seite_erzeugt_keinen_vorschlag_wenn_alles_schon_bekannt(stammdaten_db, tmp_path):
    """Ist am Objekt bereits alles erfasst, was die KI erkennt, entsteht gar
    kein Vorschlag - vermeidet Dubletten UND unnoetigen Review-Laerm."""
    from app.services import objekt_ki_service
    db, org, objekt, seite = stammdaten_db
    _bild_datei(seite, tmp_path)
    objekt.bma = ObjektBMA(org_id=org.id, objekt_id=objekt.id, bmz_standort="BE1, UG (Feuerwehrzugang 1)")
    db.commit()

    antworten = [
        '{"dokumentart": "objektinformation", "begruendung": "Tabelle"}',
        '{"bmz_standort": "BE1, UG (Feuerwehrzugang 1)", "begruendung": "Objektbeschreibung"}',
    ]

    async def fake_vision(system, user, images, **kw):
        return antworten.pop(0)

    with patch("app.services.ai_service.complete_vision", side_effect=fake_vision):
        asyncio.run(objekt_ki_service.analysiere_seite(seite, db))
    db.commit()

    assert db.query(ObjektStammdatenVorschlag).filter(
        ObjektStammdatenVorschlag.seite_id == seite.id
    ).first() is None


def test_analysiere_seite_andere_dokumentart_erzeugt_keinen_stammdaten_vorschlag(stammdaten_db, tmp_path):
    from app.services import objekt_ki_service
    db, org, objekt, seite = stammdaten_db
    _bild_datei(seite, tmp_path)
    db.commit()

    async def fake_vision(system, user, images, **kw):
        return '{"dokumentart": "brandschutzplan", "titel": "Grundriss OG", "begruendung": "Plan"}'

    with patch("app.services.ai_service.complete_vision", side_effect=fake_vision):
        asyncio.run(objekt_ki_service.analysiere_seite(seite, db))
    db.commit()

    assert db.query(ObjektStammdatenVorschlag).filter(
        ObjektStammdatenVorschlag.seite_id == seite.id
    ).first() is None


def test_stammdaten_vorschlag_uebernehmen_befuellt_objekt_bma_gefahr_merkmal(stammdaten_db):
    from app.routers.ui_objekt_dokumente import _stammdaten_vorschlag_uebernehmen
    db, org, objekt, seite = stammdaten_db

    vorschlag = ObjektStammdatenVorschlag(
        org_id=org.id, objekt_id=objekt.id, seite_id=seite.id,
        informationen_text="1 UG, 9 OG, Fluchtniveau 20.8m",
        bmz_standort="BE1, UG (Feuerwehrzugang 1)",
        fbf_standort="BE1, UG BMZ (Feuerwehrzugang 1)",
        gefahren_json='[{"piktogramm_typ": "gas", "name": null, "detail": "Sauerstofftank 2300l"}, '
                       '{"piktogramm_typ": "ex", "name": null, "detail": "Lager Druckgasverpackung UN 1950"}]',
        merkmale_json='[{"code": "sprinkler", "name": null}]',
        status=KI_VORSCHLAG_OFFEN,
    )
    db.add(vorschlag)
    db.commit()

    class _FakeUser:
        id = 1

    _stammdaten_vorschlag_uebernehmen(db, vorschlag, _FakeUser())
    db.commit()

    db.refresh(objekt)
    assert "Fluchtniveau" in objekt.informationen
    assert objekt.bma is not None
    assert objekt.bma.bmz_standort == "BE1, UG (Feuerwehrzugang 1)"
    assert objekt.bma.fbf_standort == "BE1, UG BMZ (Feuerwehrzugang 1)"

    # "gas" nutzt den bestehenden Katalogeintrag, "ex" hat keinen -> wird NEU
    # angelegt (bisher unbekannte Kategorie), statt uebersprungen zu werden.
    gefahren_typen = sorted(g.gefahr.piktogramm_typ for g in objekt.gefahren)
    assert gefahren_typen == ["ex", "gas"]
    neuer_katalogeintrag = db.query(GefahrenKatalog).filter(
        GefahrenKatalog.org_id == org.id, GefahrenKatalog.piktogramm_typ == "ex"
    ).first()
    assert neuer_katalogeintrag is not None

    assert objekt.hat_merkmal("sprinkler")
    assert vorschlag.status == KI_VORSCHLAG_UEBERNOMMEN
    assert vorschlag.entschieden_am is not None


def test_stammdaten_vorschlag_uebernehmen_legt_neues_merkmal_ohne_code_an(stammdaten_db):
    """Ein von der KI erkanntes, keinem Standard-Code zuordenbares Merkmal wird
    als neuer, codeloser Katalogeintrag angelegt (individuelle Besonderheit)."""
    from app.routers.ui_objekt_dokumente import _stammdaten_vorschlag_uebernehmen
    db, org, objekt, seite = stammdaten_db

    vorschlag = ObjektStammdatenVorschlag(
        org_id=org.id, objekt_id=objekt.id, seite_id=seite.id,
        merkmale_json='[{"code": null, "name": "Regenvorhang Anlieferung"}]',
        status=KI_VORSCHLAG_OFFEN,
    )
    db.add(vorschlag)
    db.commit()

    class _FakeUser:
        id = 1

    _stammdaten_vorschlag_uebernehmen(db, vorschlag, _FakeUser())
    db.commit()

    db.refresh(objekt)
    neu = db.query(MerkmalKatalog).filter(
        MerkmalKatalog.org_id == org.id, MerkmalKatalog.name == "Regenvorhang Anlieferung"
    ).first()
    assert neu is not None
    assert neu.code is None
    assert any(m.merkmal_id == neu.id for m in objekt.merkmale)


def test_stammdaten_vorschlag_uebernehmen_ueberschreibt_vorhandene_bma_felder_nicht(stammdaten_db):
    """Ein bereits manuell gepflegter BMZ-Standort darf durch einen (evtl.
    ungenaueren) KI-Vorschlag nicht stillschweigend ersetzt werden."""
    from app.routers.ui_objekt_dokumente import _stammdaten_vorschlag_uebernehmen
    db, org, objekt, seite = stammdaten_db
    objekt.bma = ObjektBMA(org_id=org.id, objekt_id=objekt.id, bmz_standort="Manuell gepflegter Standort")
    db.commit()

    vorschlag = ObjektStammdatenVorschlag(
        org_id=org.id, objekt_id=objekt.id, seite_id=seite.id,
        bmz_standort="Anderer KI-Standort", fbf_standort="Neuer FBF-Standort",
        status=KI_VORSCHLAG_OFFEN,
    )
    db.add(vorschlag)
    db.commit()

    class _FakeUser:
        id = 1

    _stammdaten_vorschlag_uebernehmen(db, vorschlag, _FakeUser())
    db.commit()

    db.refresh(objekt)
    assert objekt.bma.bmz_standort == "Manuell gepflegter Standort"  # unveraendert
    assert objekt.bma.fbf_standort == "Neuer FBF-Standort"  # leeres Feld wird befuellt


def test_stammdaten_vorschlag_uebernehmen_ist_idempotent_bei_merkmal(stammdaten_db):
    """Ein zweiter Uebernahme-Durchlauf (z.B. zweite Seite mit gleichem Merkmal)
    darf kein doppeltes ObjektMerkmal anlegen (UniqueConstraint objekt_id+merkmal_id)."""
    from app.routers.ui_objekt_dokumente import _stammdaten_vorschlag_uebernehmen
    db, org, objekt, seite = stammdaten_db

    class _FakeUser:
        id = 1

    for _ in range(2):
        vorschlag = ObjektStammdatenVorschlag(
            org_id=org.id, objekt_id=objekt.id, seite_id=seite.id,
            merkmale_json='[{"code": "sprinkler", "name": null}]', status=KI_VORSCHLAG_OFFEN,
        )
        db.add(vorschlag)
        db.commit()
        _stammdaten_vorschlag_uebernehmen(db, vorschlag, _FakeUser())
        db.commit()

    db.refresh(objekt)
    assert len(objekt.merkmale) == 1


def test_bereits_vorhanden_liest_aktuellen_objekt_stand(stammdaten_db):
    db, org, objekt, seite = stammdaten_db
    objekt.bma = ObjektBMA(org_id=org.id, objekt_id=objekt.id, bmz_standort="BE1, UG")
    objekt.informationen = "1 UG, 9 OG"
    db.commit()
    db.refresh(objekt)

    bereits = _bereits_vorhanden(objekt)
    assert bereits["bma_werte"]["bmz_standort"] == "BE1, UG"
    assert bereits["informationen"] == "1 UG, 9 OG"
    assert bereits["gefahr_typen"] == set()
    assert bereits["merkmal_codes"] == set()


def test_pr_registrierung():
    from app.core.tenant import _TENANT_TABLE_NAMES
    assert "objekt_stammdaten_vorschlag" in _TENANT_TABLE_NAMES
    from app.routers.ui_objekt_dokumente import router
    pfade = {r.path for r in router.routes}
    assert "/objekte/{objekt_id}/dokumente/ki-review-stammdaten/{vorschlag_id}/uebernehmen" in pfade
    assert "/objekte/{objekt_id}/dokumente/ki-review-stammdaten/{vorschlag_id}/verwerfen" in pfade
