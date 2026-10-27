"""Jeder registrierte Check: positive/negative Daten und unveraenderter Fachbestand."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import Boolean, Date, DateTime, Integer, LargeBinary, Numeric, create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.admin_security import utcnow
from app.services.diagnose import registry
from app.services.diagnose.helpers import table


@pytest.fixture
def diag_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'diag.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.begin() as con:
        con.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        for head in ScriptDirectory("alembic").get_heads():
            con.execute(text("INSERT INTO alembic_version VALUES (:head)"), {"head": head})
    yield engine
    engine.dispose()


@pytest.fixture
def factory(diag_engine):
    return sessionmaker(diag_engine, autoflush=False)


def insert(db, table_name, **values):
    """Kleine Core-Fixtures ohne fachliche Listener oder abgeleitete Mutationen."""
    source = table(table_name)
    for c in source.c:
        if c.name in values or c.nullable or c.default is not None or c.server_default is not None:
            continue
        if c.primary_key and isinstance(c.type, Integer):
            continue
        if isinstance(c.type, Boolean):
            values[c.name] = False
        elif isinstance(c.type, Integer):
            values[c.name] = 1
        elif isinstance(c.type, DateTime):
            values[c.name] = utcnow()
        elif isinstance(c.type, Date):
            values[c.name] = utcnow().date()
        elif isinstance(c.type, Numeric):
            values[c.name] = Decimal(0)
        elif isinstance(c.type, LargeBinary):
            values[c.name] = b""
        else:
            values[c.name] = "fixture"
    ident = db.execute(source.insert().values(**values)).inserted_primary_key[0]
    db.commit()
    return ident


def snapshot(engine):
    with engine.connect() as con:
        return {t.name: [tuple(r) for r in con.execute(select(t))] for t in Base.metadata.tables.values()}


@pytest.mark.parametrize("check_id", list(registry.CHECKS))
def test_each_check_consistent_and_inconsistent_readonly(diag_engine, factory, monkeypatch, tmp_path, check_id):
    from app.config import settings
    from app.services import ws_bus
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    redis = {"configured": True, "connected": True, "publisher_ok": True, "subscriber_connected": True,
             "reconnects": 0, "publish_failures": 0, "subscriber_failures": 0}
    monkeypatch.setattr(ws_bus, "status", lambda: dict(redis))
    with factory() as db:
        insert(db, "worker_runtime", worker_id="w", hostname="host", pid=1, started_at=utcnow(),
               heartbeat_at=utcnow(), expires_at=utcnow() + timedelta(seconds=20), is_leader=True,
               redis=redis, loops={})
        org = insert(db, "fire_dept", name="PRIVATE-NAME", slug="test")
    check = registry.CHECKS[check_id]
    before = snapshot(diag_engine)
    result = registry.execute_check(diag_engine, check)
    assert result.findings == [], (check_id, result)
    assert snapshot(diag_engine) == before
    with factory() as db:
        if check_id == "db.zustand":
            db.execute(text("UPDATE alembic_version SET version_num = 'alt'"))
            db.commit()
        elif check_id == "worker.zustand":
            insert(db, "worker_runtime", worker_id="w2", hostname="host", pid=2, started_at=utcnow(),
                   heartbeat_at=utcnow(), expires_at=utcnow() + timedelta(seconds=20), is_leader=True,
                   redis=redis, loops={})
        elif check_id == "redis.zustand":
            redis.update(connected=False, publish_failures=3)
        elif check_id == "gateway.jobs":
            insert(db, "incident_alarm_job", incident_id=99999, org_id=org, channel="sms",
                   next_attempt_at=utcnow() - timedelta(minutes=10), status="pending", context={})
        elif check_id == "einsatz.integritaet":
            insert(db, "einheit_site_dispatch", einheit_id=99999, site_id=99999)
        elif check_id == "objekte.dokumente":
            insert(db, "objekt_dokument", org_id=org, objekt_id=99999, pfad="PRIVATE-NAME.pdf", status="fertig")
        elif check_id == "katplan.integritaet":
            insert(db, "katplan_plan", org_id=org, status="freigegeben")
        elif check_id == "kontakte.integritaet":
            insert(db, "user", username="private-user", org_id=99999, member_id=99999)
            insert(db, "kontakt", org_id=org, email="private@example.org")
            insert(db, "kontakt", org_id=org, email="PRIVATE@example.org")
        elif check_id == "suche.rueckstand":
            insert(db, "search_outbox", org_id=org, quelle="kontakt", quell_id="1",
                   erstellt_am=utcnow() - timedelta(minutes=10))
        else:
            pytest.fail("Fixture fuer neuen Check ergaenzen")
    before = snapshot(diag_engine)
    result = registry.execute_check(diag_engine, check)
    assert result.findings, check_id
    assert result.vollstaendig
    assert snapshot(diag_engine) == before
    content = json.dumps([f.__dict__ for f in result.findings])
    assert "PRIVATE-NAME" not in content and "private@example.org" not in content


@pytest.mark.parametrize("statement", ["UPDATE fire_dept SET name='changed'", "DELETE FROM fire_dept",
    "INSERT INTO fire_dept (slug,name) VALUES ('bad','bad')", "CREATE TABLE bad (id INT)",
    "PRAGMA user_version=2", "WITH x AS (SELECT 1) DELETE FROM fire_dept"])
def test_sql_write_guard(diag_engine, statement):
    def bad(db, budget):
        db.connection().execute(text(statement))
        db.commit()
        return registry.CheckResult()
    before = snapshot(diag_engine)
    with pytest.raises(PermissionError):
        registry.execute_check(diag_engine, registry.Check("bad", "test", "", "leicht", bad))
    assert snapshot(diag_engine) == before


def test_orm_flush_guard(diag_engine):
    from app.models.master import FireDept
    def bad(db, budget):
        db.add(FireDept(slug="bad", name="bad"))
        db.commit()
        return registry.CheckResult()
    with pytest.raises(PermissionError):
        registry.execute_check(diag_engine, registry.Check("bad", "test", "", "leicht", bad))
    assert not snapshot(diag_engine)["fire_dept"]


def test_sqlite_timeout_and_row_budget(diag_engine):
    def slow(db, budget):
        # SELECT mit grossem kartesischen Produkt wird im SQLite-VM-Handler beendet.
        db.execute(text("SELECT count(*) FROM json_each(:v) a, json_each(:v) b, json_each(:v) c"),
                   {"v": json.dumps(list(range(500)))}).all()
        return registry.CheckResult()
    with pytest.raises((TimeoutError, OperationalError)):
        registry.execute_check(diag_engine, registry.Check("slow", "test", "", "leicht", slow), 0.001)
    budget = registry.Budget(rows=2)
    with diag_engine.connect() as connection:
        assert len(budget.rows(connection, select(text("1")).union_all(select(text("2")), select(text("3"))))) == 2
    assert not budget.complete


def test_registry_refuses_repairs_and_duplicate_ids():
    with pytest.raises(ValueError):
        registry.register_check("repair", "test", "", read_only=False)
    with pytest.raises(ValueError):
        registry.register_check("db.zustand", "test", "")(lambda: None)


def test_valid_documents_then_missing_file_and_cross_org_target(diag_engine, factory, monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    (tmp_path / "original.pdf").write_bytes(b"%PDF fixture")
    (tmp_path / "page.pdf").write_bytes(b"%PDF fixture")
    with factory() as db:
        org = insert(db, "fire_dept", name="Privat", slug="org")
        objekt = insert(db, "objekt", name="Privat", org_id=org)
        document = insert(db, "objekt_dokument", org_id=org, objekt_id=objekt, status="fertig", pfad="original.pdf")
        insert(db, "objekt_dokument_seite", org_id=org, objekt_id=objekt, dokument_id=document,
               seiten_nr=1, einzel_pdf_pfad="page.pdf", volltext="PRIVATER TEXT")
    check = registry.CHECKS["objekte.dokumente"]
    before = snapshot(diag_engine)
    assert registry.execute_check(diag_engine, check).findings == []
    assert snapshot(diag_engine) == before
    (tmp_path / "original.pdf").unlink()
    result = registry.execute_check(diag_engine, check)
    assert any(f.kern == ["objekt_dokument", "datei"] for f in result.findings)
    with factory() as db:
        other = insert(db, "fire_dept", name="Privat2", slug="org2")
        db.execute(table("objekt_dokument").update().values(org_id=other))
        db.commit()
    assert any(f.kern == ["objekt_dokument", "objekt_id"] for f in registry.execute_check(diag_engine, check).findings)


def test_plan_page_references_and_review(diag_engine, factory):
    with factory() as db:
        org = insert(db, "fire_dept", name="Privat", slug="org")
        plan = insert(db, "katplan_plan", org_id=org, status="freigegeben", zuletzt_geprueft_am=utcnow().date())
        document = insert(db, "katplan_dokument", org_id=org, plan_id=plan, seitenzahl=1)
        insert(db, "katplan_dokument_seite", org_id=org, plan_id=plan, dokument_id=document, seiten_nr=1)
        element = insert(db, "katplan_element", org_id=org, plan_id=plan, typ="massnahme",
                         quelle_dokument_id=document, quelle_seiten="1")
    check = registry.CHECKS["katplan.integritaet"]
    assert registry.execute_check(diag_engine, check).findings == []
    with factory() as db:
        db.execute(table("katplan_element").update().where(table("katplan_element").c.id == element)
                   .values(quelle_seiten="2"))
        db.commit()
    before = snapshot(diag_engine)
    result = registry.execute_check(diag_engine, check)
    assert any(f.kern == "seitenreferenz" for f in result.findings)
    assert snapshot(diag_engine) == before
