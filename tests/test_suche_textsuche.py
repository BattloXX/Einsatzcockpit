"""Gezielte Tests fuer Datenmodell, Tenant-Schutz und Textsuche."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import aliased

from app.core.tenant import TenantContextMissing, set_tenant_context
from app.models.master import FireDept, OrgSettings
from app.models.suche import SearchChunk, SearchDocument, SearchOutbox
from app.services.suche.synonyme import (
    entferne_telefonnummern,
    expandiere,
    normalisiere,
    suchwoerter,
)
from app.services.suche.textsuche import Kandidat, baue_boolean_query, volltext_kandidaten
from tests.conftest import TestingSession


@pytest.fixture
def daten():
    with TestingSession() as db:
        set_tenant_context(db, None)
        a = FireDept(slug=uuid4().hex, name="Suche A")
        b = FireDept(slug=uuid4().hex, name="Suche B")
        db.add_all([a, b])
        db.flush()

        def dokument(org, quelle, titel, text, keywords=None):
            doc = SearchDocument(
                org_id=org.id, quelle=quelle, quell_id=uuid4().hex,
                titel=titel, search_keywords=keywords, inhalt_hash="d" * 64,
            )
            doc.chunks = [SearchChunk(org_id=org.id, chunk_nr=0, text=text, inhalt_hash="c" * 64)]
            db.add(doc)
            db.flush()
            return doc

        halle = dokument(a, "objekt", "Brandmeldezentrale Halle 3", "Zugang im Norden")
        fremd = dokument(b, "objekt", "Brandmeldezentrale Halle 3", "Zugang im Norden")
        text = dokument(a, "katplan", "Lager", "Brandmeldezentrale im Sueden")
        keywords = dokument(a, "objekt", "Brueckenplan", "Hinweise", "bruecke 50% a_b")
        db.add_all([SearchOutbox(org_id=org.id, quelle="objekt", quell_id="1") for org in (a, b)])
        db.flush()
        yield db, a, b, halle, fremd, text, keywords
        db.rollback()


def test_normalisiere():
    assert normalisiere("  BRÜCKE\tÖl ÄRGER Straße\n ") == "bruecke oel aerger strasse"
    assert normalisiere("Bruecke") == normalisiere("Brücke")


def test_suchwoerter():
    assert suchwoerter('+bma -x "y" (z)*') == ["bma", "x", "y", "z"]
    assert suchwoerter(' <bma> ~bmz @gas, „werk“ !!! ') == ["bma", "bmz", "gas", "werk"]
    assert suchwoerter(" ".join(f"wort{i}" for i in range(20))) == [f"wort{i}" for i in range(12)]
    assert suchwoerter(" + - () ~ * \" @ ") == []


def test_expandiere():
    assert expandiere(["BMA"]) == ["bma", "brandmeldeanlage"]
    assert expandiere(["unbekannt"]) == ["unbekannt"]
    assert expandiere(["bma", "werk", "brandmeldeanlage", "bma"]) == ["bma", "werk", "brandmeldeanlage"]
    assert "feuerwehrschluesseldepot" in expandiere(["FSD"])


@pytest.mark.parametrize("nummer", [
    "+43 5574 12345", "0664/1234567", "05574-6789-0", "0043 664 123 45 67",
    "(05574) 12345", "+436641234567", "1234567",
])
def test_telefonnummern_entfernen(nummer):
    assert entferne_telefonnummern(f"Telefon: {nummer}, erreichbar.") == "Telefon: [Tel.], erreichbar."
    assert entferne_telefonnummern(f"Telefon: {nummer}.") == "Telefon: [Tel.]."


@pytest.mark.parametrize("text", [
    "PLZ 6922", "20.10.2026", "14:30", "Nr. 12a", "Objekt 4711", "47.4712, 9.7534",
    "Jahr 2026", "Objekt 123456", "Nr. 1234567a",
])
def test_keine_telefonnummer(text):
    assert entferne_telefonnummern(text) == text


def test_boolean_query():
    query, kurze = baue_boolean_query(["bma", "werk", "2"])
    assert query == "+(bma* brandmeldeanlage*) +werk*"
    assert kurze == ["2"]
    assert baue_boolean_query(["rwa"])[0] == "+(rwa* rauchwaermeabzug*)"
    assert baue_boolean_query(["pv"])[1] == ["pv"]
    assert baue_boolean_query([]) == ("", [])


def test_boolean_sonderzeichen():
    query, kurze = baue_boolean_query(['+bma', '-werk*', '"x"', '(z)~@<>', 'abc:def', "foo'bar"])
    assert query == "+(bma* brandmeldeanlage*) +werk* +abc* +def* +foo* +bar*"
    assert kurze == ["x", "z"]


def test_sqlite_synonyme_und_org(daten):
    db, a, b, halle, fremd, text, _ = daten
    treffer = volltext_kandidaten(db, a.id, ["bmz"])
    assert {t.document_id for t in treffer} == {halle.id, text.id}
    assert fremd.id not in {t.document_id for t in treffer}
    assert [t.rang for t in treffer] == list(range(1, len(treffer) + 1))
    assert treffer[0].document_id == halle.id
    assert treffer[-1].document_id == text.id
    assert len({(t.document_id, t.chunk_id) for t in treffer}) == len(treffer)
    assert {t.document_id for t in volltext_kandidaten(db, b.id, ["BMZ"])} == {fremd.id}


def test_sqlite_und_filter_leer_limit(daten):
    db, a, _, halle, _, text, _ = daten
    assert {t.document_id for t in volltext_kandidaten(db, a.id, ["bmz", "norden"])} == {halle.id}
    assert volltext_kandidaten(db, a.id, ["bmz", "unbekannt"]) == []
    assert {t.document_id for t in volltext_kandidaten(db, a.id, ["bmz"], quellen=["katplan"])} == {text.id}
    assert volltext_kandidaten(db, a.id, ["bmz"], quellen=[]) == []
    assert volltext_kandidaten(db, a.id, []) == []
    assert volltext_kandidaten(db, a.id, ["bmz"], limit=0) == []
    assert volltext_kandidaten(db, a.id, ["bmz"], limit=1) == [Kandidat(halle.id, None, 1)]


def test_sqlite_keywords_escaping_und_dokument_ohne_chunks(daten):
    db, a, _, _, _, _, doc = daten
    for wort in ["Brücke", "50%", "a_b"]:
        assert {t.document_id for t in volltext_kandidaten(db, a.id, [wort])} == {doc.id}
    assert volltext_kandidaten(db, a.id, ["b_c"]) == []
    doc.chunks.clear()
    db.flush()
    assert volltext_kandidaten(db, a.id, ["bruecke"]) == [Kandidat(doc.id, None, 1)]


def test_chunk_reihenfolge_und_cascade(daten):
    db, a, _, halle, _, _, _ = daten
    halle.chunks.append(SearchChunk(org_id=a.id, chunk_nr=2, text="Zugang", inhalt_hash="x" * 64))
    halle.chunks.append(SearchChunk(org_id=a.id, chunk_nr=1, text="Zugang", inhalt_hash="y" * 64))
    db.flush()
    db.expire(halle, ["chunks"])
    assert [c.chunk_nr for c in halle.chunks] == [0, 1, 2]
    assert [t.chunk_id for t in volltext_kandidaten(db, a.id, ["zugang"])] == [c.id for c in halle.chunks]
    doc_id = halle.id
    db.delete(halle)
    db.flush()
    assert db.scalars(sa.select(SearchChunk).where(SearchChunk.document_id == doc_id)).all() == []


def test_defaults(daten):
    db, a, _, halle, _, _, _ = daten
    settings = OrgSettings(org_id=a.id)
    db.add(settings)
    db.flush()
    assert settings.suche_aktiv is False
    assert halle.scope_typ == "org" and halle.scope_id == 0 and halle.sichtbarkeit == "standard"
    assert halle.indexiert_am.tzinfo is None
    assert abs((datetime.now(UTC).replace(tzinfo=None) - halle.indexiert_am).total_seconds()) < 60
    outbox = db.scalars(sa.select(SearchOutbox).where(SearchOutbox.org_id == a.id)).one()
    assert outbox.status == "pending" and outbox.versuche == 0
    assert outbox.erstellt_am.tzinfo is None and outbox.naechster_versuch_am.tzinfo is None


@pytest.mark.parametrize("modell", [SearchDocument, SearchChunk, SearchOutbox])
def test_tenant_filter_und_fail_closed(daten, modell):
    db, a, _, _, _, _, _ = daten
    set_tenant_context(db, a.id)
    assert all(zeile.org_id == a.id for zeile in db.scalars(sa.select(modell)))
    alias = aliased(modell)
    assert all(zeile.org_id == a.id for zeile in db.scalars(sa.select(alias)))
    db.info.pop("current_org_id")
    with pytest.raises(TenantContextMissing):
        db.execute(sa.select(modell))
    set_tenant_context(db, None)


@pytest.mark.parametrize("dialekt", ["mysql", "mariadb"])
def test_mysql_query_gebunden_und_scoped(dialekt):
    db = MagicMock()
    db.get_bind.return_value.dialect.name = dialekt
    db.execute.return_value = []
    assert volltext_kandidaten(db, 321, ["bma", "2"], quellen=["objekt"]) == []
    abfrage, parameter = db.execute.call_args.args
    sql = str(abfrage.compile(dialect=mysql.dialect()))
    assert sql.count("MATCH (") == 4
    assert "IN BOOLEAN MODE" in sql
    assert "bma" not in sql and "321" not in sql
    assert parameter == {"q": "+(bma* brandmeldeanlage*)"}
    assert "search_chunk.org_id =" in sql and "search_document.org_id =" in sql
    assert "search_document.quelle IN" in sql and "LIKE" in sql


def test_mysql_nur_kurze_woerter():
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "mysql"
    db.execute.return_value = []
    volltext_kandidaten(db, 321, ["pv", "2"])
    abfrage, parameter = db.execute.call_args.args
    sql = str(abfrage.compile(dialect=mysql.dialect()))
    assert "MATCH" not in sql and "LIKE" in sql
    assert parameter == {}
    assert "%photovoltaik%" in abfrage.compile().params.values()


def test_org_filter_auch_auf_chunks(daten):
    db, a, b, halle, _, _, _ = daten
    halle.chunks.append(SearchChunk(org_id=b.id, chunk_nr=1, text="Fremder Text", inhalt_hash="f" * 64))
    db.flush()
    assert volltext_kandidaten(db, a.id, ["fremder"]) == []


def test_migration_sqlite_upgrade_downgrade():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0300_suche_index.py"
    spec = importlib.util.spec_from_file_location("suche_migration", pfad)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE fire_dept (id BIGINT PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE org_settings (id INTEGER PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            inspector = sa.inspect(conn)
            for modell in (SearchDocument, SearchChunk, SearchOutbox):
                spalten = {s["name"]: s for s in inspector.get_columns(modell.__tablename__)}
                assert set(spalten) == set(modell.__table__.columns.keys())
                assert spalten["org_id"]["nullable"] is False
                assert {i["name"] for i in inspector.get_indexes(modell.__tablename__)} == {
                    i.name for i in modell.__table__.indexes
                }
            conn.execute(sa.text("INSERT INTO org_settings (id) VALUES (1)"))
            assert conn.scalar(sa.text("SELECT suche_aktiv FROM org_settings")) == 0
            migration.downgrade()
            assert set(sa.inspect(conn).get_table_names()) == {"fire_dept", "org_settings"}
            assert [s["name"] for s in sa.inspect(conn).get_columns("org_settings")] == ["id"]
    engine.dispose()


@pytest.mark.parametrize("wort,synonym", [
    ("Hochwasser", "überflutung"), ("HQ", "überschwemmung"),
    ("Ueberflutung", "hochwasser"), ("Überschwemmung", "hq"),
    ("Blackout", "stromausfall"), ("Stromausfall", "blackout"),
    ("Evakuierung", "räumung"), ("Raeumung", "evakuierung"),
    ("Sammelplatz", "treffpunkt"), ("Treffpunkt", "sammelplatz"),
    ("Einsatzleiter", "el"), ("EL", "einsatzleitung"),
    ("KatS", "katastrophenschutz"), ("Kat-Schutz", "kats"),
    ("Fdl", "fahrdienstleiter"), ("Fahrdienstleiter", "fdl"),
    ("Oberleitung", "fahrleitung"), ("Fahrleitung", "oberleitung"),
])
def test_katplan_synonyme(wort, synonym):
    assert normalisiere(synonym) in expandiere([wort])


def test_katplan_markierung_praefix_synonym_und_html():
    from app.services.katplan_suche_service import markiere

    assert str(markiere("Stromausfallplan", "Blackout")) == "<mark>Stromausfallplan</mark>"
    assert str(markiere("RÄUMUNGSPLAN & <script>", "raeumung")) == (
        "<mark>RÄUMUNGSPLAN</mark> &amp; &lt;script&gt;"
    )
    assert str(markiere("Überflutung UEBERFLUTUNG", "HQ")) == (
        "<mark>Überflutung</mark> <mark>UEBERFLUTUNG</mark>"
    )
    assert str(markiere("Fahrstraße Strasse", "strass")) == "Fahrstraße <mark>Strasse</mark>"
    assert str(markiere('<b> & "Alarm"', '<b>')) == '<mark>&lt;b&gt;</mark> &amp; &quot;Alarm&quot;'
    assert str(markiere("<b>&", "")) == "&lt;b&gt;&amp;"


def test_katplan_ausschnitt_dichtestes_fenster():
    from app.services.katplan_suche_service import ausschnitt

    text = "Alarm einzeln " + "Fülltext " * 80 + "Alarm Sammelplatz Stromausfallplan Ende"
    snippet = ausschnitt(text, ["alarm", "treffpunkt", "blackout"])
    assert "Alarm Sammelplatz Stromausfallplan" in snippet
    assert "einzeln" not in snippet and snippet.startswith("... ")
    assert ausschnitt("kurzer Text", []) == "kurzer Text"
    assert ausschnitt(None, ["alarm"]) == ""
    assert ausschnitt("a " * 200, ["fehlt"]) == "a " * 100 + " ..."
    assert "Alarm Sammelplatz" in ausschnitt(text, ["alarm", "treffpunkt"], laenge=80)


def test_katplan_synonyme_im_boolean_query():
    assert "katastrophenschutz*" in baue_boolean_query(["Kat-Schutz"])[0]
    assert "ueberschwemmung*" in baue_boolean_query(["Hochwasser"])[0]
