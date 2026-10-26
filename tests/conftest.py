# ruff: noqa: E402 -- Testumgebung muss vor den App-Imports gesetzt sein.
import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# pytest lädt diese Datei je nach Importpfad als ``conftest`` oder
# ``tests.conftest``. Tests importieren ihre gemeinsamen Fixtures über den
# Paketpfad; ohne Alias würde die Datei ein zweites Mal ausgeführt und dabei die
# SQLite-Datei eines laufenden Workers entfernt.
sys.modules.setdefault("tests.conftest", sys.modules[__name__])

# Test-Umgebungsvariablen VOR dem App-Import setzen
os.environ.setdefault("SECRET_KEY", "test-secret-key-fuer-tests-mindestens-32-zeichen!")
os.environ.setdefault("DEBUG", "true")
os.environ["TESTING"] = "true"
os.environ["ICONS_STRICT"] = "true"
# SQLite für alle Tests erzwingen – verhindert, dass Fixtures und Client
# auf unterschiedliche Datenbanken schreiben/lesen.
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
TEST_DB_PATH = Path(f"test_{WORKER}.db")
TEST_DB_URL = f"sqlite:///./{TEST_DB_PATH}"
TEST_STORAGE_ROOT = Path(f".test_storage_{WORKER}")
os.environ["DATABASE_URL"] = TEST_DB_URL
# Auch Dateien, die einzelne Tests nicht ohnehin auf tmp_path umbiegen, dürfen
# nicht zwischen xdist-Workern geteilt werden.
os.environ["MEDIA_STORAGE_DIR"] = str(TEST_STORAGE_ROOT / "incident_media")
os.environ["PROBE_MEDIA_DIR"] = str(TEST_STORAGE_ROOT / "probe_media")
os.environ["OBJEKT_MEDIA_DIR"] = str(TEST_STORAGE_ROOT / "objekt_media")
os.environ["NACHSCHLAGEWERK_DATA_DIR"] = str(TEST_STORAGE_ROOT / "nachschlagewerk")
os.environ["BACKUP_DIR"] = str(TEST_STORAGE_ROOT / "backups")
os.environ["LEADER_LOCK_PATH"] = str(TEST_STORAGE_ROOT / "background-leader.lock")

# Ein abgebrochener Lauf kann Daten hinterlassen; vor create_all immer sauber
# beginnen, damit eindeutige Seed-Werte keine scheinbaren Parallelfehler liefern.
def _remove_test_db() -> None:
    """Entfernt auch SQLite-WAL-/Journal-Dateien eines abgebrochenen Laufs."""
    for suffix in ("", "-journal", "-shm", "-wal"):
        TEST_DB_PATH.with_name(f"{TEST_DB_PATH.name}{suffix}").unlink(missing_ok=True)


# Nachschlagewerk-Sync im Test hart deaktivieren: der lifespan-Loop synchronisiert
# beim Start SOFORT gegen die echte Euro-Rescue-Katalog-API (synchroner httpx-Call in
# einem to_thread-Worker). In jedem TestClient-Startup blockierte dieser Netz-Call den
# Default-Executor-Thread bis zum Timeout (~5 s), was den Client-Teardown pro Test um
# ~5 s verzögerte (Gesamtlaufzeit CI > 1 h) und die fremde Produktiv-API bei jedem
# Testlauf beschoss. Die Sync-Logik selbst wird in test_rettungskarten_katalog.py
# direkt (mit gemocktem _hole_rohdaten) getestet – der Loop wird hier nicht gebraucht.
os.environ["NACHSCHLAGEWERK_SYNC_ENABLED"] = "false"
# IMAP-/Graph-Poller würden in der TestClient-Lifespan echte Verbindungen zu Test-Hosts aufbauen
# und auf MariaDB parallel zu den Tests DB-Sessions halten (CI hing dadurch); Tests rufen die
# Poller-Funktionen direkt auf.
os.environ["MAIL_IMAP_POLL_ENABLED"] = "false"
os.environ["MAIL_GRAPH_POLL_ENABLED"] = "false"
# BMA-Webplattform-Import: der Loop schlaeft zwar erst (kein Sofort-Sync wie beim
# Nachschlagewerk-Sync oben), aber ohne diesen Schalter wuerde ein langsamer/
# Abruf gegen die BMA-Webplattform ausloesen - hart aus, analog.

from app.core import security as _security
from app.core.tenant import set_tenant_context
from app.db import Base, get_db
from app.main import app
from app.seed_data import seed

# Passwort-Hashes in Tests mit minimalem bcrypt-Kostenfaktor: 12 Runden kosten pro Benutzer
# bzw. Login ~0,2 s lokal und mehr in der CI – bei tausenden Tests der größte Einzelposten.
# Produktion bleibt bei security.BCRYPT_ROUNDS = 12 (siehe test_bcrypt_produktionsrunden).
_security.BCRYPT_ROUNDS = 4
from app.models import uas as _uas_models  # noqa: F401 – alle UAS-Tabellen in Base.metadata registrieren


# SQLite unterstützt kein BigInteger-Autoincrement — BigInteger als INTEGER kompilieren
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"

engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def reset_lis_in_memory_state():
    """LIS-Poll-Zustand darf nicht zwischen einzelnen Tests weiterleben."""
    from app.services.lis import lis_loop, lis_sync

    lis_sync._tasks_retry_after.clear()
    lis_sync._released_vehicle_misses.clear()
    lis_loop._org_clients.clear()
    lis_loop._last_started_at.clear()
    from app.services import device_auth_failure_service
    device_auth_failure_service.reset_state()
    yield
    lis_sync._tasks_retry_after.clear()
    lis_sync._released_vehicle_misses.clear()
    lis_loop._org_clients.clear()
    lis_loop._last_started_at.clear()


def flatten_routes(routes):
    """Rekursiv abflachen: ab Starlette >=1.x (fastapi >=0.139) wrapped `include_router()`
    Sub-Router in einen `_IncludedRouter` statt sie wie zuvor flach in `app.routes` zu haengen
    -- dieser Wrapper hat kein `.path`/`.name`/`.endpoint` mehr direkt dran, die echten Routes
    liegen unter `.original_router.routes`. Mit aelteren Starlette-Versionen (kein
    `original_router`-Attribut) unveraendertes Verhalten -- Routes werden einfach durchgereicht.
    `pyproject.toml` pinnt fastapi ohne Obergrenze, CI installiert daher potenziell eine
    neuere Version als lokal gecached; dieser Helper macht Routen-Introspektion in Tests
    unabhaengig davon, welche der beiden Varianten gerade installiert ist."""
    out = []
    for r in routes:
        original_router = getattr(r, "original_router", None)
        if original_router is not None:
            out.extend(flatten_routes(original_router.routes))
        else:
            out.append(r)
    return out


def all_app_paths(app_) -> set[str]:
    """Alle registrierten Pfade der App (rekursiv abgeflacht, s. flatten_routes)."""
    return {r.path for r in flatten_routes(app_.routes) if hasattr(r, "path")}


def override_get_db():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    _remove_test_db()
    shutil.rmtree(TEST_STORAGE_ROOT, ignore_errors=True)
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    set_tenant_context(db, None)
    seed(db)
    # Viele Tests vergeben die Rolle ``org_admin`` und legen sie nur an, wenn sie
    # fehlt (teils mit veraltetem ``name=``). Seriell hat das ein früherer Test
    # erledigt; mit xdist ist die Reihenfolge pro Worker zufällig. Daher im
    # Test-Seed anlegen, nicht im produktiven Seed.
    from app.models.user import Role
    if db.query(Role).filter(Role.code == "org_admin").first() is None:
        db.add(Role(code="org_admin", label="Organisations-Administrator"))
        db.commit()
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    _remove_test_db()
    shutil.rmtree(TEST_STORAGE_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def _rate_limit_zuruecksetzen():
    # Viele Tests bauen ihren TestClient selbst (ohne die client-Fixture). Mit xdist
    # laufen ihre Logins in kurzer Zeit im selben Prozess und reißen das
    # /login-Limit (10/minute) – daher vor JEDEM Test zurücksetzen.
    from app.core.rate_limit import limiter
    if limiter is not None:
        limiter.reset()


@pytest.fixture
def client():
    # Rate-Limit-Zustand je Test zurücksetzen: der slowapi-Limiter nutzt In-Memory-Storage,
    # das sonst über die gesamte Session akkumuliert – viele Logins in einem Test-File
    # könnten das /login-Limit für nachfolgende Tests aufbrauchen (Cross-Test-Pollution).
    from app.core.rate_limit import limiter
    if limiter is not None:
        limiter.reset()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def sms_outbox_state():
    """Dauerhafte Job-IDs/Cursor sind pro Test isoliert (wie echte neue DBs)."""
    from app.models.api_message import ApiMessage, ApiMessageRecipient
    from app.models.sms import SmsDispatchAttempt, SmsDispatchCursor, SmsDispatchJob, SmsLog, SmsLogRecipient
    from app.services.sms_outbox import database
    def clear():
        with database() as db:
            log_ids = [r[0] for r in db.query(SmsLogRecipient.sms_log_id).join(SmsDispatchJob,
                SmsDispatchJob.id == SmsLogRecipient.dispatch_job_id).execution_options(include_all_tenants=True)]
            api_ids = [r[0] for r in db.query(ApiMessageRecipient.message_id).join(SmsDispatchJob,
                SmsDispatchJob.id == ApiMessageRecipient.dispatch_job_id).execution_options(include_all_tenants=True)]
            db.query(ApiMessageRecipient).filter(ApiMessageRecipient.message_id.in_(api_ids)).execution_options(
                include_all_tenants=True).delete(synchronize_session=False)
            db.query(ApiMessage).filter(ApiMessage.id.in_(api_ids)).execution_options(
                include_all_tenants=True).delete(synchronize_session=False)
            db.query(SmsLogRecipient).filter(SmsLogRecipient.sms_log_id.in_(log_ids)).delete(synchronize_session=False)
            db.query(SmsLog).filter(SmsLog.id.in_(log_ids)).execution_options(
                include_all_tenants=True).delete(synchronize_session=False)
            db.query(SmsDispatchAttempt).execution_options(include_all_tenants=True).delete()
            db.query(SmsDispatchJob).execution_options(include_all_tenants=True).delete()
            db.query(SmsDispatchCursor).execution_options(include_all_tenants=True).delete()
            db.commit()
    clear()
    yield
    clear()
