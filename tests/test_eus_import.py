"""EUS-Import-Script (scripts/eus_import.py): Mapping, Nummernvergabe, Import-Schritte."""
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite-Testumgebung
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


# Script als Modul laden (scripts/ ist kein Package)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eus_import  # noqa: E402

from app.core.tenant import set_tenant_context  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.master import FireDept  # noqa: E402
from app.models.objekt import (  # noqa: E402
    Objekt,
    ObjektBMA,
    ObjektKontakt,
    ObjektMerkmal,
)


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("eus,erwartet", [
    ("Brandschutzbeauftragter", "brandschutzbeauftragter"),
    ("  betreiber ", "betreiber"),
    ("Hausverwaltung", "hausverwaltung"),
    ("Schlüsselträger", "schluesseltraeger"),
    ("schluesseltraeger", "schluesseltraeger"),
    ("Hausmeister", "sonstig"),
    ("", "sonstig"),
    (None, "sonstig"),
])
def test_map_kontakt_art(eus, erwartet):
    assert eus_import.map_kontakt_art(eus) == erwartet


@pytest.mark.parametrize("wert,erwartet_jahr", [
    ("2026-03-01", 2026),
    ("2026-03-01T10:30:00Z", 2026),
    ("2026-03-01 10:30:00", 2026),
    (None, None),
    ("", None),
    ("kein datum", None),
])
def test_parse_datum(wert, erwartet_jahr):
    ergebnis = eus_import.parse_datum(wert)
    if erwartet_jahr is None:
        assert ergebnis is None
    else:
        assert ergebnis is not None and ergebnis.year == erwartet_jahr


# ── In-Memory-DB ──────────────────────────────────────────────────────────────

@pytest.fixture()
def import_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)
    org = FireDept(slug="eus-org", name="EUS Import Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.commit()
    yield db, org.id
    db.close()
    Base.metadata.drop_all(bind=engine)


def _leere_stats() -> dict:
    return {
        "kategorien_neu": 0, "kategorien_vorhanden": 0,
        "merkmale_katalog_neu": 0, "merkmale_katalog_vorhanden": 0,
        "objekte_neu": 0, "objekte_uebersprungen": 0, "objekte_fehler": 0,
        "bma_angelegt": 0, "kontakte_angelegt": 0, "merkmal_zuordnungen": 0,
        "dokumente_phase2": 0,
    }


def test_nummernvergabe(import_db):
    db, org_id = import_db
    db.add(Objekt(org_id=org_id, nummer=5, name="Bestand", status="freigegeben"))
    db.commit()
    nummern = eus_import.Nummernvergabe(db, org_id)
    assert nummern.vergib(10) == 10        # Wunschnummer frei
    assert nummern.vergib(5) == 6          # kollidiert mit Bestand → MAX+1
    assert nummern.vergib(None) == 7       # keine Wunschnummer → fortlaufend
    assert nummern.vergib("kaputt") == 8   # unparsebar → fortlaufend
    assert nummern.vergib(10) == 9         # 10 inzwischen vergeben → fortlaufend


def test_kategorien_und_merkmale_dedupliziert(import_db):
    db, org_id = import_db
    stats = _leere_stats()
    mapping1 = eus_import.importiere_kategorien(
        db, org_id, [{"eus_id": "k1", "name": "Gewerbe", "aktiv": True}], stats
    )
    mapping2 = eus_import.importiere_kategorien(
        db, org_id, [{"eus_id": "k1b", "name": "Gewerbe", "aktiv": True}], stats
    )
    assert mapping1["k1"] == mapping2["k1b"]  # zweiter Lauf findet Bestand
    assert stats["kategorien_neu"] == 1
    assert stats["kategorien_vorhanden"] == 1

    m1 = eus_import.importiere_merkmale(
        db, org_id, [{"eus_id": "m1", "name": "Sprinkler", "code": "SPR"}], stats
    )
    m2 = eus_import.importiere_merkmale(
        db, org_id, [{"eus_id": "m1b", "name": "Sprinkler", "code": "SPR"}], stats
    )
    assert m1["m1"] == m2["m1b"]
    assert stats["merkmale_katalog_neu"] == 1


def _beispiel_objekt() -> dict:
    return {
        "eus_objekt_daten_id": 1,
        "name": "Volksschule Mähdle",
        "objektnummer": None,
        "kategorie_eus_id": "k1",
        "strasse": "Mähdlestraße",
        "hausnummer": "27",
        "plz": "6922",
        "ort": "Wolfurt",
        "lat": 47.461843,
        "lng": 9.747645,
        "informationen": "Rauchabzug manuell über Taster.",
        "anfahrtsweg": "über Wagnerstraße",
        "revision_datum": None,
        "bma": {
            "bma_nummer": "interne BMA, kein FBF oder BMZ",
            "bmz_standort": None,
            "fbf_standort": None,
            "schluesselsafe_vorhanden": True,
            "schluesselsafe_standort": "bei Haupteingang",
            "schluesselsafe_inhalt": "1 Schlüssel",
            "benachrichtigung_sms": None,
        },
        "kontakte": [
            {"art": "Brandschutzbeauftragter", "name": "Max Muster",
             "telefone": ["0664 123456", "05574 111"], "email": "max@example.com",
             "erreichbarkeit": None},
            {"art": "Hausmeister", "name": "Hans Wart", "telefone": [], "email": None,
             "erreichbarkeit": "werktags"},
        ],
        "merkmale": [
            {"merkmal_eus_id": "m1", "hinweis": "im Keller"},
            {"merkmal_eus_id": "m1", "hinweis": "doppelt"},        # Duplikat → 1x
            {"merkmal_eus_id": "unbekannt", "hinweis": None},      # nicht im Katalog
        ],
        "dokumente": [{"eus_datei_id": "d1"}, {"eus_datei_id": "d2"}],
    }


def test_importiere_objekt_komplett(import_db):
    db, org_id = import_db
    stats = _leere_stats()
    kat = eus_import.importiere_kategorien(
        db, org_id, [{"eus_id": "k1", "name": "Gemeinde"}], stats
    )
    mer = eus_import.importiere_merkmale(
        db, org_id, [{"eus_id": "m1", "name": "Sprinkler", "code": "SPR"}], stats
    )
    nummern = eus_import.Nummernvergabe(db, org_id)

    detail = eus_import.importiere_objekt(db, org_id, _beispiel_objekt(), kat, mer, nummern)
    db.commit()

    objekt = db.query(Objekt).filter(Objekt.name == "Volksschule Mähdle").first()
    assert objekt is not None
    assert objekt.status == "freigegeben"
    assert objekt.org_id == org_id
    assert objekt.kategorie_id == kat["k1"]
    assert objekt.lat == pytest.approx(47.461843)

    bma = db.query(ObjektBMA).filter(ObjektBMA.objekt_id == objekt.id).first()
    assert bma is not None and bma.schluesselsafe_vorhanden is True
    assert bma.schluesselsafe_standort == "bei Haupteingang"
    assert any("Freitext" in w for w in detail["warnungen"])  # BMA-Nummer ist Freitext

    kontakte = (
        db.query(ObjektKontakt)
        .filter(ObjektKontakt.objekt_id == objekt.id)
        .order_by(ObjektKontakt.sort)
        .all()
    )
    assert len(kontakte) == 2
    assert kontakte[0].art == "brandschutzbeauftragter"
    assert json.loads(kontakte[0].telefone_json) == ["0664 123456", "05574 111"]
    assert kontakte[1].art == "sonstig"          # "Hausmeister" → Default
    assert kontakte[1].telefone_json is None     # keine Nummern → NULL

    merkmale = db.query(ObjektMerkmal).filter(ObjektMerkmal.objekt_id == objekt.id).all()
    assert len(merkmale) == 1                    # Duplikat + unbekanntes Merkmal gefiltert
    assert merkmale[0].hinweis == "im Keller"

    assert detail["dokumente_phase2"] == 2       # Phase 2: nur gezaehlt
    assert detail["kontakte"] == 2 and detail["merkmale"] == 1 and detail["bma"] is True


def test_main_dry_run_schreibt_nichts(import_db, tmp_path, monkeypatch, capsys):
    db, org_id = import_db
    export = {
        "meta": {"exported_at": "2026-07-05", "source": "EUSWolfurt",
                 "counts": {"objekte": 1}},
        "kategorien": [{"eus_id": "k1", "name": "Gemeinde", "aktiv": True}],
        "merkmale": [{"eus_id": "m1", "name": "Sprinkler", "code": "SPR"}],
        "objekte": [_beispiel_objekt()],
    }
    eingabe = tmp_path / "export.json"
    eingabe.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")

    # Script auf die Test-Session umbiegen (close() unterdruecken, Fixture braucht sie noch)
    monkeypatch.setattr(eus_import, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(eus_import, "LOG_DATEI", tmp_path / "log.json")
    monkeypatch.setattr(sys, "argv", [
        "eus_import.py", "--input", str(eingabe), "--org-id", str(org_id), "--dry-run",
    ])

    rc = eus_import.main()
    assert rc == 0
    ausgabe = capsys.readouterr().out
    assert "[DRY RUN]" in ausgabe
    assert "Objekte:        1 neu" in ausgabe.replace("  ", " ") or "1 neu" in ausgabe

    # Dry-Run: Rollback → keine Objekte in der DB
    assert db.query(Objekt).count() == 0
    # Log-Datei existiert trotzdem
    log = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))
    assert log["dry_run"] is True
    assert log["stats"]["objekte_neu"] == 1


def test_main_skip_existing(import_db, tmp_path, monkeypatch, capsys):
    db, org_id = import_db
    export = {
        "meta": {"counts": {"objekte": 1}},
        "kategorien": [], "merkmale": [],
        "objekte": [_beispiel_objekt()],
    }
    eingabe = tmp_path / "export.json"
    eingabe.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(eus_import, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(eus_import, "LOG_DATEI", tmp_path / "log.json")

    # 1. Lauf: importiert
    monkeypatch.setattr(sys, "argv", [
        "eus_import.py", "--input", str(eingabe), "--org-id", str(org_id), "--skip-existing",
    ])
    assert eus_import.main() == 0
    assert db.query(Objekt).count() == 1

    # 2. Lauf: idempotent — SKIP, kein Duplikat
    assert eus_import.main() == 0
    assert db.query(Objekt).count() == 1
    ausgabe = capsys.readouterr().out
    assert "SKIP" in ausgabe


def test_fehler_stoppt_import_nicht(import_db, tmp_path, monkeypatch, capsys):
    """Ein kaputtes Objekt (name=None und keine EUS-ID → OK; provoziere via lat='x')."""
    db, org_id = import_db
    kaputt = _beispiel_objekt()
    kaputt["eus_objekt_daten_id"] = 99
    kaputt["name"] = "Kaputtes Objekt"
    kaputt["lat"] = "keine zahl"
    gut = _beispiel_objekt()
    gut["eus_objekt_daten_id"] = 100
    gut["name"] = "Gutes Objekt"

    export = {"meta": {}, "kategorien": [], "merkmale": [], "objekte": [kaputt, gut]}
    eingabe = tmp_path / "export.json"
    eingabe.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(eus_import, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(eus_import, "LOG_DATEI", tmp_path / "log.json")
    monkeypatch.setattr(sys, "argv", [
        "eus_import.py", "--input", str(eingabe), "--org-id", str(org_id),
    ])

    rc = eus_import.main()
    assert rc == 2  # Fehler vorhanden → Exit-Code 2
    # Das gute Objekt wurde trotzdem importiert (SAVEPOINT je Objekt)
    assert db.query(Objekt).filter(Objekt.name == "Gutes Objekt").count() == 1
    assert db.query(Objekt).filter(Objekt.name == "Kaputtes Objekt").count() == 0
    log = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))
    assert log["stats"]["objekte_fehler"] == 1
    assert log["stats"]["objekte_neu"] == 1
