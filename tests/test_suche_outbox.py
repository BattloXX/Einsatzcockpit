"""Gezielte Lebenszyklus-, Fehler- und Mandantentests fuer die Such-Outbox."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.sql.dml import Insert

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanDokument, KatplanDokumentSeite, KatplanElement
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    GefahrenKatalog, MerkmalKatalog, Objekt, ObjektBMA, ObjektDokument, ObjektDokumentSeite,
    ObjektGefahr, ObjektMerkmal, ObjektZusatzadresse,
)
from app.models.suche import SearchDocument, SearchOutbox
from app.services.katplan_freigabe_service import freigeben
from app.services.suche import outbox
from tests.conftest import TestingSession
from tests.test_suche_index import _katplan


@pytest.fixture
def daten(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "SEARCH_INDEX_WORKER_ENABLED", True)
    with TestingSession() as db:
        set_tenant_context(db, None)
        # Andere Tests im selben xdist-Worker hinterlassen Outbox-Zeilen (Listener ist global);
        # die Zaehlungen hier sollen nur eigene Zeilen sehen.
        for fremd in db.scalars(select(SearchOutbox)):
            db.delete(fremd)
        db.commit()
        org = FireDept(slug=uuid4().hex, name="Outbox-Test")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, suche_aktiv=True))
        o = Objekt(org_id=org.id, name="Suchobjekt", status="freigegeben")
        db.add(o)
        db.commit()
        org_id = org.id
        yield db, org, o
        db.rollback()
        for job in db.scalars(select(SearchOutbox).where(SearchOutbox.org_id == org_id)):
            db.delete(job)
        db.commit()


def _jobs(db, org):
    return db.scalars(select(SearchOutbox).where(SearchOutbox.org_id == org.id).order_by(SearchOutbox.id)).all()


def _docs(db, org):
    return {(d.quelle, d.quell_id) for d in db.scalars(
        select(SearchDocument).where(SearchDocument.org_id == org.id))}


def _lauf(db, org):
    for job in _jobs(db, org):
        if job.status in ("pending", "retry"):
            job.naechster_versuch_am = outbox._jetzt() - timedelta(seconds=1)
    db.commit()
    return outbox.verarbeite_faellige(db, limit=1000)


def _seiten(db, org, o):
    d = ObjektDokument(org_id=org.id, objekt_id=o.id, dateiname_original="Plan.pdf", pfad="plan.pdf")
    db.add(d)
    db.flush()
    s = ObjektDokumentSeite(org_id=org.id, objekt_id=o.id, dokument_id=d.id,
                           seiten_nr=1, volltext="Zugang zur Brandmeldezentrale")
    db.add(s)
    db.commit()
    return d, s


def test_erfassung_dedupe_und_worker(daten):
    db, org, o = daten
    jobs = _jobs(db, org)
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.quelle, job.quell_id, job.status) == ("objekt", str(o.id), "pending")
    assert job.naechster_versuch_am - job.erstellt_am == timedelta(seconds=2)
    assert job.erstellt_am.tzinfo is None
    for name in ("Geaendert", "Nochmals geaendert"):
        o.name = name
        db.commit()
        assert len(_jobs(db, org)) == 1
    assert outbox.verarbeite_faellige(db) == 0
    assert _lauf(db, org) == 1
    assert _docs(db, org) == {("objekt", str(o.id))}
    assert (job.status, job.versuche, job.gesperrt_bis) == ("done", 1, None)
    assert db.scalar(select(SearchDocument.titel).where(SearchDocument.org_id == org.id)) == o.name


def test_archivieren_entfernt_objekt_und_seiten(daten):
    db, org, o = daten
    _, s = _seiten(db, org, o)
    _lauf(db, org)
    assert ("objekt_dokument_seite", str(s.id)) in _docs(db, org)
    o.status = "archiviert"
    db.commit()
    assert _lauf(db, org) == 1
    assert _docs(db, org) == set()


def test_dokument_freigabe_entziehen(daten):
    db, org, o = daten
    d, s = _seiten(db, org, o)
    _lauf(db, org)
    assert ("objekt_dokument_seite", str(s.id)) in _docs(db, org)
    d.freigabe_status = "entwurf"
    db.commit()
    assert _lauf(db, org) == 1
    assert _docs(db, org) == {("objekt", str(o.id))}


def test_objekt_loeschen_raeumt_cascade_seiten_auf(daten):
    db, org, o = daten
    d, s = _seiten(db, org, o)
    _lauf(db, org)
    ident, seite = o.id, str(s.id)
    # Core-Delete simuliert DB-CASCADE ohne after_flush fuer die Kindobjekte.
    db.execute(ObjektDokumentSeite.__table__.delete().where(
        ObjektDokumentSeite.org_id == org.id, ObjektDokumentSeite.id == s.id))
    db.execute(ObjektDokument.__table__.delete().where(
        ObjektDokument.org_id == org.id, ObjektDokument.id == d.id))
    db.delete(o)
    db.commit()
    assert set(outbox.loese_auf(db, org.id, "objekt", str(ident))) == {
        ("objekt", str(ident)), ("objekt_dokument_seite", seite),
    }
    assert _lauf(db, org) == 1
    assert not _docs(db, org)


def test_katplan_versionswechsel_und_aufloesung(daten):
    db, org, _ = daten
    plan, user, alt = _katplan(db, org)
    freigeben(db, plan, user)
    dok = KatplanDokument(org_id=org.id, plan_id=plan.id, titel="Quellplan",
                         dateiname_original="plan.pdf", pfad="plan.pdf", groesse_bytes=10, seitenzahl=1)
    db.add(dok)
    db.flush()
    seite = KatplanDokumentSeite(org_id=org.id, plan_id=plan.id, dokument_id=dok.id,
                                seiten_nr=1, einzel_pdf_pfad="seite.pdf", volltext="Quelltext")
    db.add(seite)
    db.commit()
    alter_key = ("katplan_element", f"{plan.id}:{alt.uuid}")
    seiten_key = ("katplan_seite", str(seite.id))
    assert {alter_key, seiten_key} <= set(outbox.loese_auf(db, org.id, "katplan_plan", str(plan.id)))
    _lauf(db, org)
    db.delete(alt)
    neu = KatplanElement(org_id=org.id, plan_id=plan.id, typ="info", titel="Neues Element")
    db.add(neu)
    plan.status = "in_pruefung"
    db.flush()
    freigeben(db, plan, user)
    db.commit()
    _lauf(db, org)
    docs = _docs(db, org)
    assert alter_key not in docs
    assert ("katplan_element", f"{plan.id}:{neu.uuid}") in docs
    assert seiten_key in docs
    dok.archiviert = True
    db.commit()
    _lauf(db, org)
    assert seiten_key not in _docs(db, org)


def test_arbeitskopie_und_arbeitsstand_ignoriert(daten):
    db, org, o = daten
    _lauf(db, org)
    vorher = len(_jobs(db, org))
    kopie = Objekt(org_id=org.id, name="Kopie", entwurf_von_id=o.id)
    katalog = MerkmalKatalog(org_id=org.id, name="Katalog")
    db.add_all([kopie, katalog])
    db.commit()
    kopie.name = "Kopie geaendert"
    db.commit()
    assert len(_jobs(db, org)) == vorher
    plan, _, element = _katplan(db, org)
    db.commit()
    vorher = len(_jobs(db, org))
    element.titel = "Arbeitsstand"
    db.commit()
    assert len(_jobs(db, org)) == vorher


@pytest.mark.parametrize("schalter", ["org", "worker"])
def test_deaktiviert_erledigt_ohne_index(daten, monkeypatch, schalter):
    db, org, _ = daten
    if schalter == "org":
        db.scalar(select(OrgSettings).where(OrgSettings.org_id == org.id)).suche_aktiv = False
        db.commit()
    else:
        monkeypatch.setattr(settings, "SEARCH_INDEX_WORKER_ENABLED", False)
    assert _lauf(db, org) == 1
    assert _jobs(db, org)[0].status == "done"
    assert not _docs(db, org)


def test_retry_backoff_und_failed(daten, monkeypatch):
    db, org, _ = daten

    def fehler(*args):
        raise RuntimeError("Adapter kaputt " + "x" * 600)

    monkeypatch.setattr(outbox, "indexiere", fehler)
    for versuch in range(1, 9):
        vorher = outbox._jetzt()
        assert _lauf(db, org) == 1
        job = _jobs(db, org)[0]
        assert job.versuche == versuch
        assert job.status == ("failed" if versuch == 8 else "retry")
        assert job.gesperrt_bis is None
        assert job.letzter_fehler.startswith("RuntimeError: Adapter kaputt")
        assert len(job.letzter_fehler) == 500
        backoff = timedelta(seconds=min(3600, 30 * 2 ** (versuch - 1)))
        assert vorher + backoff <= job.naechster_versuch_am <= outbox._jetzt() + backoff
        assert outbox.verarbeite_faellige(db) == 0


@pytest.mark.parametrize("sql_fehler", [False, True])
def test_listener_fehler_verhindert_fachcommit_nicht(daten, monkeypatch, sql_fehler):
    db, org, o = daten
    _lauf(db, org)
    vorher = len(_jobs(db, org))
    if sql_fehler:
        def kaputtes_sql(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO search_outbox"):
                return "INSERT INTO nicht_vorhandene_outbox VALUES (1)", ()
            return statement, parameters

        event.listen(db.bind, "before_cursor_execute", kaputtes_sql, retval=True)
    else:
        def kaputt(*args):
            raise RuntimeError("Insert kaputt")

        monkeypatch.setattr(outbox, "insert", kaputt)
    try:
        o.name = "Fachcommit funktioniert"
        db.commit()
        assert db.get(Objekt, o.id).name == "Fachcommit funktioniert"
        assert len(_jobs(db, org)) == vorher
    finally:
        if sql_fehler:
            event.remove(db.bind, "before_cursor_execute", kaputtes_sql)


def test_search_aus_und_fehlende_org(daten, monkeypatch):
    db, org, o = daten
    _lauf(db, org)
    vorher = len(_jobs(db, org))
    monkeypatch.setattr(settings, "SEARCH_ENABLED", False)
    o.name = "Nicht erfassen"
    db.commit()
    assert len(_jobs(db, org)) == vorher
    monkeypatch.setattr(settings, "SEARCH_ENABLED", True)
    db.add(Objekt(name="Ohne Org"))
    db.commit()
    assert len(_jobs(db, org)) == vorher


def test_aufraeumen_nur_alte_done_und_systemmodus(daten):
    db, org, _ = daten
    alt = outbox._jetzt() - timedelta(days=8)
    jobs = [SearchOutbox(org_id=org.id, quelle="objekt", quell_id=f"test-{i}", status=status,
                        erstellt_am=alt if i != 1 else outbox._jetzt())
            for i, status in enumerate(("done", "done", "pending", "retry", "failed"))]
    db.add_all(jobs)
    db.commit()
    ids = [j.id for j in jobs]
    set_tenant_context(db, org.id)
    outbox.raeume_auf(db)
    assert db.info["current_org_id"] is None
    assert db.get(SearchOutbox, ids[0]) is None
    assert all(db.get(SearchOutbox, ident) is not None for ident in ids[1:])


def test_claim_lease_und_limit(daten):
    db, org, _ = daten
    job = _jobs(db, org)[0]
    job.naechster_versuch_am = outbox._jetzt() - timedelta(seconds=1)
    db.commit()
    assert outbox._claim(db, job, outbox._jetzt())
    with TestingSession() as andere:
        set_tenant_context(andere, None)
        assert not outbox._claim(andere, andere.get(SearchOutbox, job.id), outbox._jetzt())
        assert outbox.verarbeite_faellige(andere) == 0
    db.refresh(job)
    assert job.versuche == 1
    job.gesperrt_bis = outbox._jetzt() - timedelta(seconds=1)
    db.commit()
    assert outbox.verarbeite_faellige(db, limit=0) == 0
    assert outbox.verarbeite_faellige(db, limit=1) == 1


def test_abgleich_nur_aktive_orgs(daten):
    db, org, o = daten
    fremd = FireDept(slug=uuid4().hex, name="Suche deaktiviert")
    db.add(fremd)
    db.flush()
    db.add_all([OrgSettings(org_id=fremd.id, suche_aktiv=False),
                Objekt(org_id=fremd.id, name="Unsichtbar", status="freigegeben")])
    db.commit()
    set_tenant_context(db, fremd.id)
    outbox.abgleich(db)
    assert _docs(db, org) == {("objekt", str(o.id))}
    assert not _docs(db, fremd)


def test_listener_dedupe_im_flush_rollback_und_keine_lazy_loads(daten):
    db, org, o = daten
    _lauf(db, org)
    vorher = len(_jobs(db, org))
    gefahr = GefahrenKatalog(org_id=org.id, name="Gas")
    db.add(gefahr)
    db.flush()
    db.add_all([ObjektBMA(org_id=org.id, objekt_id=o.id),
                ObjektZusatzadresse(org_id=org.id, objekt_id=o.id, bezeichnung="Tor"),
                ObjektGefahr(org_id=org.id, objekt_id=o.id, gefahr_id=gefahr.id)])
    db.flush()
    assert len(_jobs(db, org)) == vorher + 1
    db.rollback()
    assert len(_jobs(db, org)) == vorher
    ident = o.id
    db.expire(o, ["name"])
    statements = []

    def beobachte(conn, clauseelement, multiparams, params, execution_options):
        statements.append(clauseelement)

    event.listen(db.bind, "before_execute", beobachte)
    try:
        o.status = "archiviert"
        db.flush()
    finally:
        event.remove(db.bind, "before_execute", beobachte)
    assert all("FROM objekt" not in str(stmt) for stmt in statements)
    assert any(isinstance(stmt, Insert) for stmt in statements)
    assert _jobs(db, org)[-1].quell_id == str(ident)


def test_aufloesung_org_filter_und_verwaiste_dokumentseiten(daten):
    db, org, o = daten
    d, s = _seiten(db, org, o)
    _lauf(db, org)
    ident, seite = str(d.id), str(s.id)
    db.delete(d)
    db.commit()
    assert outbox.loese_auf(db, org.id, "objekt_dokument", ident) == [("objekt_dokument_seite", seite)]
    assert outbox.loese_auf(db, -1, "objekt_dokument", ident) == []
    _lauf(db, org)
    assert ("objekt_dokument_seite", seite) not in _docs(db, org)


def test_registrierung_idempotent(daten, monkeypatch):
    db, org, o = daten
    _lauf(db, org)
    outbox.registriere_listener()
    outbox.registriere_listener()
    aufrufe = []
    original = outbox.insert

    def zaehle(*args):
        aufrufe.append(1)
        return original(*args)

    monkeypatch.setattr(outbox, "insert", zaehle)
    o.name = "Neue Aenderung"
    db.commit()
    assert len(aufrufe) == 1


def test_geladene_spalten_ueber_commit_ohne_lazy_load(daten):
    db, org, o = daten
    _lauf(db, org)
    ident = o.id
    with TestingSession() as andere:
        set_tenant_context(andere, org.id)
        geladen = andere.get(Objekt, ident)
        andere.commit()
        statements = []

        def beobachte(conn, clauseelement, multiparams, params, execution_options):
            statements.append(str(clauseelement))

        event.listen(andere.bind, "before_execute", beobachte)
        try:
            geladen.name = "Nach Commit geaendert"
            andere.commit()
        finally:
            event.remove(andere.bind, "before_execute", beobachte)
        # SQLAlchemy darf vor dem UPDATE abgelaufene Spalten selbst laden.
        # Der anschliessende after_flush-Listener darf keine weiteren nachladen.
        update_nr = next(i for i, stmt in enumerate(statements) if stmt.startswith("UPDATE objekt "))
        assert all("FROM objekt" not in stmt for stmt in statements[update_nr + 1:])
    assert _lauf(db, org) == 1
    assert db.scalar(select(SearchDocument.titel).where(SearchDocument.org_id == org.id)) == "Nach Commit geaendert"


def test_kindmodelle_und_retry_dedupe(daten):
    db, org, o = daten
    _lauf(db, org)
    katalog = MerkmalKatalog(org_id=org.id, name="Photovoltaik")
    db.add(katalog)
    db.flush()
    kind = ObjektMerkmal(org_id=org.id, objekt_id=o.id, merkmal_id=katalog.id, hinweis="Dach")
    db.add(kind)
    db.commit()
    job = _jobs(db, org)[-1]
    assert (job.quelle, job.quell_id) == ("objekt", str(o.id))
    job.status = "retry"
    db.commit()
    kind.hinweis = "Westdach"
    db.commit()
    assert len([j for j in _jobs(db, org) if j.status in ("pending", "retry")]) == 1
    _lauf(db, org)
    db.delete(kind)
    db.commit()
    assert _jobs(db, org)[-1].status == "pending"
    _lauf(db, org)
