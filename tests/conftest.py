import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles

# Test-Umgebungsvariablen VOR dem App-Import setzen
os.environ.setdefault("SECRET_KEY", "test-secret-key-fuer-tests-mindestens-32-zeichen!")
os.environ.setdefault("DEBUG", "true")
# SQLite für alle Tests erzwingen – verhindert, dass Fixtures und Client
# auf unterschiedliche Datenbanken schreiben/lesen.
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from app.core.tenant import set_tenant_context
from app.db import Base, get_db
from app.main import app
from app.seed_data import seed
from app.models import uas as _uas_models  # noqa: F401 – alle UAS-Tabellen in Base.metadata registrieren


# SQLite unterstützt kein BigInteger-Autoincrement — BigInteger als INTEGER kompilieren
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"

TEST_DB_URL = "sqlite:///./test.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    set_tenant_context(db, None)
    seed(db)
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)


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
