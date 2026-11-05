"""Jeder registrierte Check: positive/negative Daten und unveraenderter Fachbestand."""
import json
import sqlite3
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import Boolean, Date, DateTime, Integer, LargeBinary, Numeric, create_engine, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
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


@pytest.mark.parametrize("status", ["open", "in_progress", "done"])
def test_cancel_task_haelt_status_flags_und_diagnose_konsistent(diag_engine, factory, status):
    from app.models.incident import IncidentChange, Task
    from app.services.incident_service import cancel_task, set_task_status

    with factory() as db:
        org = insert(db, "fire_dept", slug="auftraege")
        incident = insert(db, "incident", primary_org_id=org, status="active")
        ident = insert(db, "task", incident_id=incident, status="open", is_done=False, is_cancelled=False)
        task = db.get(Task, ident)
        set_task_status(db, task, status)
        db.commit()
        for zielstatus, aktion in [("cancelled", "task.cancelled"), ("open", "task.restored")]:
            vorher = {"status": task.status, "is_done": task.is_done, "is_cancelled": task.is_cancelled}
            assert cancel_task(db, task) is task
            db.commit()
            db.refresh(task)
            assert task.status == zielstatus
            assert task.is_cancelled == (zielstatus == "cancelled")
            assert not task.is_done and task.done_at is None
            assert (task.cancelled_at is not None) == task.is_cancelled
            log = db.query(IncidentChange).filter_by(incident_id=incident, action=aktion).one()
            assert json.loads(log.before_json) == vorher
            assert json.loads(log.after_json) == {
                "status": zielstatus, "is_done": False, "is_cancelled": task.is_cancelled,
            }
            result = registry.execute_check(diag_engine, registry.CHECKS["einsatz.integritaet"])
            assert not any(f.kern == "task_flags" for f in result.findings)


def test_katplan_relation_ziel_darf_in_anderem_plan_aber_nicht_anderer_org_liegen(diag_engine, factory):
    with factory() as db:
        org = insert(db, "fire_dept", slug="relationen")
        andere_org = insert(db, "fire_dept", slug="andere-relationen")
        plan = insert(db, "katplan_plan", org_id=org, status="entwurf")
        zielplan = insert(db, "katplan_plan", org_id=org, status="entwurf")
        fremdplan = insert(db, "katplan_plan", org_id=andere_org, status="entwurf")
        quelle = insert(db, "katplan_element", org_id=org, plan_id=plan, uuid="quelle")
        insert(db, "katplan_element", org_id=org, plan_id=zielplan, uuid="ziel")
        insert(db, "katplan_element", org_id=andere_org, plan_id=fremdplan, uuid="fremd")
        gueltig = insert(db, "katplan_relation", org_id=org, plan_id=plan,
                         von_element_id=quelle, zu_element_uuid="ziel", art="siehe_auch")
        ungueltig = [insert(db, "katplan_relation", org_id=org, plan_id=plan,
                            von_element_id=quelle, zu_element_uuid=uuid, art="siehe_auch")
                     for uuid in ("fremd", "fehlt")]
    result = registry.execute_check(diag_engine, registry.CHECKS["katplan.integritaet"])
    finding, = [f for f in result.findings if f.kern == "relation_ziel"]
    assert finding.schweregrad == "fail"
    assert set(finding.betroffene_ids["katplan_relation"]) == set(ungueltig)
    assert gueltig not in finding.betroffene_ids["katplan_relation"]


@pytest.mark.parametrize("check_id", list(registry.CHECKS))
def test_each_check_consistent_and_inconsistent_readonly(diag_engine, factory, monkeypatch, tmp_path, check_id):
    from app.config import settings
    from app.services import ws_bus
    monkeypatch.setattr("app.services.db_transactions.retry_statistik", lambda: {})
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    redis = {"configured": True, "connected": True, "publisher_ok": True, "subscriber_connected": True,
             "reconnects": 0, "publish_failures": 0, "subscriber_failures": 0}
    monkeypatch.setattr(ws_bus, "status", lambda: dict(redis))
    with factory() as db:
        insert(db, "worker_runtime", worker_id="w", hostname="host", pid=1, started_at=utcnow(),
               heartbeat_at=utcnow(), expires_at=utcnow() + timedelta(seconds=20), is_leader=True,
               redis=redis, loops={})
        org = insert(db, "fire_dept", name="PRIVATE-NAME", slug="test")
    if check_id == "backup":
        from app.services.backup_status import schreibe_status
        monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
        monkeypatch.setattr(settings, "BACKUP_REMOTE_ENABLED", False)
        for typ in ("backup", "restore"):
            schreibe_status(tmp_path, f".status-{typ}.json", {"ok": True})
    if check_id == "osm.gebaeude_bestand":
        monkeypatch.setattr(settings, "OSM_GEBAEUDE_QUELLEN", "")
    if check_id == "deploy.version":
        from app.services.github_readonly import GitHubReadonly
        monkeypatch.setattr(settings, "GIT_SHA", "abcdef12")
        monkeypatch.setattr(GitHubReadonly, "compare", lambda self, sha: {
            "status": "ok", "daten": {"commits_hinter_main": 0}})
    if check_id == "app.geraete":
        monkeypatch.setattr("app.services.diagnose.app_geraete.android_release_version_cached", lambda: None)
    check = registry.CHECKS[check_id]
    before = snapshot(diag_engine)
    result = registry.execute_check(diag_engine, check)
    assert result.findings == [], (check_id, result)
    assert snapshot(diag_engine) == before
    with factory() as db:
        if check_id == "backup":
            schreibe_status(tmp_path, ".status-backup.json", {"ok": False})
        elif check_id == "deploy.version":
            monkeypatch.setattr(settings, "GIT_SHA", "")
        elif check_id == "osm.gebaeude_bestand":
            monkeypatch.setattr(settings, "OSM_GEBAEUDE_QUELLEN", "region=https://osm.test/region.pbf")
        elif check_id == "db.snapshot_isolation":
            monkeypatch.setattr("app.services.db_transactions.retry_statistik", lambda: {1020: 1})
        elif check_id == "db.zustand":
            db.execute(text("UPDATE alembic_version SET version_num = 'alt'"))
            db.commit()
        elif check_id == "worker.zustand":
            insert(db, "worker_runtime", worker_id="w2", hostname="host", pid=2, started_at=utcnow(),
                   heartbeat_at=utcnow(), expires_at=utcnow() + timedelta(seconds=20), is_leader=True,
                   redis=redis, loops={})
        elif check_id == "redis.zustand":
            redis.update(connected=False, publish_failures=3)
        elif check_id == "app.geraete":
            insert(db, "app_geraet", user_id=99999, sdk_int=23)
        elif check_id == "app.standort":
            insert(db, "device_token", label="x", token_hash="h" * 64, user_id=99999, platform="android",
                   last_used_at=utcnow(), created_at=utcnow(), fail_count=0, duty_active=False)
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


def test_sqlite_timeout_and_row_budget(diag_engine, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(registry, "time", SimpleNamespace(monotonic=lambda: clock.now))
    entered = False

    def expire():
        nonlocal entered
        entered = True
        clock.now = 11.0
        return 1

    def slow(db, budget):
        db.connection().connection.driver_connection.create_function("expire_budget", 0, expire)
        # Die Frist laeuft erst IN der SELECT ab, nicht beim Connect/SQL-Guard.
        db.execute(text("SELECT sum(expire_budget()) FROM json_each(:v) a, json_each(:v) b"),
                   {"v": json.dumps(list(range(100)))}).all()
        pytest.fail("SQLite-Progress-Handler hat die SELECT nicht abgebrochen")

    with pytest.raises(OperationalError, match="interrupted"):
        registry.execute_check(diag_engine, registry.Check("slow", "test", "", "leicht", slow), 10.0)
    assert entered
    budget = registry.Budget(rows=2)
    with diag_engine.connect() as connection:
        assert budget.rows(connection, select(text("1")).union_all(select(text("2")), select(text("3")))) == [
            (1,), (2,),
        ]
    assert budget.remaining == 0 and not budget.complete


@pytest.mark.parametrize("invalidate", [False, True], ids=["rollback", "closed-connection"])
def test_sqlite_timeout_does_not_interrupt_rollback(monkeypatch, invalidate):
    class ProgressConnection(sqlite3.Connection):
        def set_progress_handler(self, handler, n):
            # Erzwingt die sonst zufaellige VM-Grenze waehrend ROLLBACK.
            super().set_progress_handler(handler, 1 if handler else 0)

    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(":memory:", factory=ProgressConnection))
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(registry, "time", SimpleNamespace(monotonic=lambda: clock.now))

    def expire_before_cleanup(db, budget):
        # SQLite fuehrt fuer reine SELECTs im Legacy-Modus sonst kein BEGIN aus.
        db.connection().connection.driver_connection.execute("BEGIN")
        assert db.execute(text("SELECT 1")).scalar_one() == 1
        if invalidate:
            db.connection().invalidate()
        clock.now = 11.0
        return registry.CheckResult()

    try:
        with pytest.raises(TimeoutError, match="Diagnose-Zeitbudget erreicht"):
            registry.execute_check(engine, registry.Check("cleanup", "test", "", "leicht", expire_before_cleanup), 10.0)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()


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


class SnapshotSession:
    """Nur die erwarteten SELECTs erlauben, ohne echten MariaDB-Server."""
    def __init__(self, version="12.3.0-MariaDB", global_wert=1, session_wert=0,
                 isolation="REPEATABLE-READ", dialect="mysql", is_mariadb=True):
        self.dialect = SimpleNamespace(name=dialect, is_mariadb=is_mariadb)
        self.version = version
        self.werte = (global_wert, session_wert, isolation)
        self.statements = []

    def get_bind(self):
        return SimpleNamespace(dialect=self.dialect)

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if sql == "SELECT @@version":
            return SimpleNamespace(scalar_one=lambda: self.version)
        assert sql == ("SELECT @@GLOBAL.innodb_snapshot_isolation, "
                       "@@SESSION.innodb_snapshot_isolation, @@transaction_isolation")
        return SimpleNamespace(one=lambda: self.werte)


@pytest.mark.parametrize("schalter,session_wert,schweregrad", [
    (True, 0, None), (True, "OFF", None), (True, 1, "warn"), (True, "ON", "warn"),
    (False, 1, "info"), (False, 0, "info"),
])
def test_snapshot_session_rule(monkeypatch, schalter, session_wert, schweregrad):
    from app.config import settings
    monkeypatch.setattr(settings, "DB_SNAPSHOT_ISOLATION_OFF", schalter)
    db = SnapshotSession(session_wert=session_wert)
    budget = registry.Budget()
    budget.runtime = {"db_retries": {}, "worker_pid": 42}
    check = registry.CHECKS["db.snapshot_isolation"]
    result = check.fn(db, budget)
    assert check.read_only and result.vollstaendig
    assert len(db.statements) == 2
    assert "GLOBAL=ON" in result.hinweis
    assert "transaction_isolation=REPEATABLE-READ" in result.hinweis
    assert "1020" in result.hinweis and "1205" in result.hinweis and "1213" in result.hinweis
    assert [f.schweregrad for f in result.findings] == ([] if schweregrad is None else [schweregrad])
    if schweregrad:
        finding = result.findings[0]
        assert finding.kern == "snapshot_regel"
        assert "1020" in finding.empfehlung
        # Schweregrad und Messwerte duerfen den Finding-Key nicht aendern.
        assert finding.key(check.id) == registry.Finding("snapshot_regel", "").key(check.id)


@pytest.mark.parametrize("version,is_mariadb,queries", [
    ("11.6.1-MariaDB", True, 1), ("10.11.9-MariaDB", True, 1),
    ("8.4.0", False, 0), ("8.4.0", None, 1),
])
def test_snapshot_unsupported_versions(monkeypatch, version, is_mariadb, queries):
    monkeypatch.setattr("app.services.db_transactions.retry_statistik", lambda: {})
    db = SnapshotSession(version=version, is_mariadb=is_mariadb)
    result = registry.CHECKS["db.snapshot_isolation"].fn(db, registry.Budget())
    assert result.findings == [] and "uebersprungen" in result.hinweis
    assert len(db.statements) == queries


@pytest.mark.parametrize("version", ["11.6.2-MariaDB", "5.5.5-11.6.2-MariaDB", "12.3.0-MariaDB"])
def test_snapshot_supported_versions(monkeypatch, version):
    from app.config import settings
    monkeypatch.setattr(settings, "DB_SNAPSHOT_ISOLATION_OFF", True)
    monkeypatch.setattr("app.services.db_transactions.retry_statistik", lambda: {})
    db = SnapshotSession(version=version, global_wert="OFF", session_wert="ON")
    result = registry.CHECKS["db.snapshot_isolation"].fn(db, registry.Budget())
    assert result.findings[0].schweregrad == "warn"
    assert "GLOBAL=OFF" in result.hinweis and "SESSION=ON" in result.hinweis


@pytest.mark.parametrize("statistik,schweregrad", [
    ({}, None), ({1020: 1}, "info"), ({1205: 1}, "info"), ({1213: 1}, "info"),
    ({1020: 3, 1205: 3, 1213: 3}, "info"), ({1020: 3, 1205: 3, 1213: 4}, "warn"),
])
def test_snapshot_sqlite_retry_detail(diag_engine, monkeypatch, statistik, schweregrad):
    monkeypatch.setattr("app.services.db_transactions.retry_statistik", lambda: statistik)
    result = registry.execute_check(diag_engine, registry.CHECKS["db.snapshot_isolation"])
    assert result.vollstaendig and "keine MariaDB" in result.hinweis
    assert "prozesslokal" in result.hinweis and "seit Prozessstart" in result.hinweis
    assert [f.schweregrad for f in result.findings] == ([] if schweregrad is None else [schweregrad])
    if schweregrad:
        finding = result.findings[0]
        assert finding.evidenz["zaehler"] == {str(code): statistik.get(code, 0) for code in (1020, 1205, 1213)}
        assert finding.key("db.snapshot_isolation") == registry.Finding("retry_konflikte", "").key("db.snapshot_isolation")


def test_no_finding_uses_generic_defaults():
    import ast
    from pathlib import Path
    from app.services.diagnose.registry import Finding
    from app.services.diagnose.texte import TEXTE, WORKER, REDIS

    keys = list(TEXTE) + list(WORKER) + list(REDIS)
    for path in Path("app/services/diagnose").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Finding" and node.args:
                try:
                    keys.append(ast.literal_eval(node.args[0]))
                except (ValueError, TypeError):
                    continue
    for kern in keys:
        finding = Finding(kern, str(kern))
        assert not any(text in finding.ursache_hypothese + finding.empfehlung for text in (
            "Teilkomponente", "Datenbestand oder", "IDs manuell pruefen"))
        assert 0 < len(finding.ursache_hypothese) <= 500
        assert 0 < len(finding.empfehlung) <= 500


@pytest.mark.parametrize("tabelle", ["processlist", "innodb_trx"])
@pytest.mark.parametrize("fehlerklasse,code", [
    (OperationalError, 1227), (OperationalError, 1142),
    (ProgrammingError, 1227), (ProgrammingError, 1142),
    (OperationalError, 2013), (OperationalError, 1205), (ProgrammingError, 1064),
])
def test_db_systemtabellen_rechte_fehler(tabelle, fehlerklasse, code):
    class Datenbank:
        def __init__(self):
            self.abfragen = []

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="mariadb"),
                                   engine=SimpleNamespace(pool=SimpleNamespace()))

        def execute(self, statement):
            sql = str(statement)
            self.abfragen.append(sql)
            if "information_schema." + tabelle in sql:
                raise fehlerklasse(sql, {}, Exception(code, "Zugriff verweigert"))
            return SimpleNamespace(all=lambda: [], scalars=lambda: ScriptDirectory("alembic").get_heads())

    db = Datenbank()
    if code not in (1227, 1142):
        with pytest.raises(fehlerklasse):
            registry.CHECKS["db.zustand"].fn(db, registry.Budget())
        return
    ergebnis = registry.CHECKS["db.zustand"].fn(db, registry.Budget())
    assert ergebnis.vollstaendig
    assert [f.schweregrad for f in ergebnis.findings] == ["info"]
    assert "GRANT PROCESS" in ergebnis.findings[0].empfehlung
    # Auch nach dem Prozesslistenfehler wird die Lock-Abfrage noch ausgefuehrt.
    assert any("innodb_trx" in sql for sql in db.abfragen)


@pytest.mark.parametrize("alter_minuten,ersatz,anderer_host,warn", [
    (1, False, False, True), (1, True, False, False),
    (1, True, True, True), (11, False, False, False),
])
def test_worker_ablauf_und_neustart(diag_engine, factory, monkeypatch,
                                   alter_minuten, ersatz, anderer_host, warn):
    from app.services import system_diagnose

    jetzt = utcnow()
    monkeypatch.setattr("app.services.diagnose.worker.utcnow", lambda: jetzt)
    with factory() as db:
        insert(db, "worker_runtime", worker_id="alt", hostname="host", pid=1,
               started_at=jetzt - timedelta(hours=1), heartbeat_at=jetzt - timedelta(minutes=alter_minuten),
               expires_at=jetzt - timedelta(minutes=alter_minuten), is_leader=False, redis={}, loops={})
        insert(db, "worker_runtime", worker_id="aktiv", hostname="anders" if anderer_host else "host", pid=2,
               started_at=jetzt - timedelta(minutes=30 if ersatz else 90), heartbeat_at=jetzt,
               expires_at=jetzt + timedelta(seconds=20), is_leader=True, redis={}, loops={})
        mcp = system_diagnose.worker_status(db, jetzt)
    ergebnis = registry.execute_check(diag_engine, registry.CHECKS["worker.zustand"])
    assert next(f for f in ergebnis.findings if f.kern == "abgelaufen").schweregrad == "info"
    assert any(f.kern == "ausfall" and f.schweregrad == "warn" for f in ergebnis.findings) is warn
    assert mcp["status"] == ("warn" if warn else "ok")
    assert mcp["evidence"]["expired_workers"] == ["alt"]


@pytest.mark.parametrize("alter_stunden,verbunden,schweregrad", [
    (None, True, "info"), (25, True, "info"), (1, True, "warn"), (25, False, "warn"),
])
def test_redis_zaehler_seit_prozessstart(diag_engine, monkeypatch, alter_stunden, verbunden, schweregrad):
    from app.services import ws_bus

    status = {"configured": True, "connected": verbunden, "publisher_ok": verbunden,
              "subscriber_connected": verbunden, "reconnects": 3, "publish_failures": 2,
              "subscriber_failures": 1, "last_error_at": (
                  (utcnow() - timedelta(hours=alter_stunden)).isoformat() if alter_stunden is not None else None)}
    monkeypatch.setattr(ws_bus, "status", lambda: status)
    ergebnis = registry.execute_check(diag_engine, registry.CHECKS["redis.zustand"])
    for befund in ergebnis.findings:
        assert befund.schweregrad == schweregrad
        if befund.kern in ("reconnects", "publish_failures", "subscriber_failures"):
            assert "seit Prozessstart" in befund.titel
            assert befund.evidenz["anzahl"] == status[befund.kern]
