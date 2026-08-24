# Tests

← [Zurück zur Startseite](Home)

## Test-Stack

| Werkzeug | Zweck |
|----------|-------|
| **pytest** | Test-Runner |
| **FastAPI TestClient** | HTTP-Tests ohne echten Server |
| **SQLite** | In-Memory-DB für Tests (statt MariaDB) |
| **unittest.mock** | Mocking für externe Dienste (KI, Push) |

## Tests ausführen

```bash
# Alle Tests (Unit-Tests laufen ohne DB):
pytest tests/ -v

# Nur Unit-Tests (kein MariaDB erforderlich):
pytest tests/test_breathing.py tests/test_api_hardening.py \
       tests/test_isolation.py tests/test_autoclose_per_org.py \
       tests/test_sysadmin.py tests/test_smoke.py -v

# Integrations-Tests (MariaDB muss laufen):
pytest tests/test_api.py tests/test_api_full.py -v

# Mit Coverage:
pytest tests/ --cov=app --cov-report=html
```

## Test-Struktur

```
tests/
├── conftest.py               Fixtures: SQLite-DB, TestClient, API-Key, Mock-AI
├── test_api.py               REST-API (Einsatz anlegen, Idempotenz) — benötigt DB
├── test_api_hardening.py     AlarmPayload/LageAlarmPayload Validierung
│                             + get_api_key_identifier() Rate-Limit-Key
├── test_breathing.py         Atemschutz-Zustandsmaschine (Unit-Tests)
├── test_isolation.py         Multi-Tenancy Row-Level-Isolation
│                             + can_access_incident + visible_incidents_q
├── test_autoclose_per_org.py Auto-Schließen: global vs. org-spezifisch
├── test_sysadmin.py          _org_stats() Aggregation (System-Admin-Konsole)
└── test_smoke.py             Import-Smoke-Tests aller neuen Module
```

## Wichtige Test-Kategorien

### test_api_hardening.py — Payload-Validierung

```python
def test_alarm_payload_key_stripped():
    p = AlarmPayload(Key="  A-001  ")
    assert p.Key == "A-001"         # whitespace wird getrimmt

def test_alarm_payload_key_whitespace_only():
    with pytest.raises(ValidationError):
        AlarmPayload(Key="   ")     # nur Whitespace → Fehler

def test_alarm_payload_stufe_normalized():
    p = AlarmPayload(Key="K1", Stufe="f3")
    assert p.Stufe == "F3"          # Normalisierung uppercase

def test_lage_payload_lat_out_of_range():
    with pytest.raises(ValidationError):
        LageAlarmPayload(Key="L-001", Lat=91.0)

def test_api_key_identifier_falls_back_to_ip():
    key = get_api_key_identifier(request_without_key)
    assert key == "10.0.0.1"
```

### test_isolation.py — Multi-Tenancy

```python
def test_alarm_type_tenant_scoped_filter():
    # AlarmType.query() filtert automatisch nach aktiver Org
    ...

def test_can_access_incident_own_org():
    assert can_access_incident(user_org_a, incident_org_a) is True

def test_can_access_incident_other_org():
    assert can_access_incident(user_org_a, incident_org_b) is False

def test_visible_incidents_no_cross_org_leakage():
    # Org A darf Einsätze von Org B nicht sehen
    ...

def test_visible_incidents_includes_collaborating():
    # Wenn Org B als Kollaborator eingetragen ist, sind Einsätze sichtbar
    ...
```

### test_autoclose_per_org.py — Auto-Schließen

```python
def test_org_cfg_null_fields_fall_back():
    # NULL in OrgSettings → globaler Wert aus SystemSettings
    ...

def test_check_closes_after_grace_period():
    # Einsatz älter als after_hours + grace_minutes → close_incident() aufgerufen
    ...
```

### test_sysadmin.py — System-Admin-Konsole

```python
def test_org_stats_no_cross_contamination():
    # Counts für Org A dürfen nicht in Org B erscheinen
    rows_by_id[2]["active_incidents"] == 0  # Org B hat keine eigenen Einsätze
```

## conftest.py — Fixtures

```python
@pytest.fixture(scope="session")
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    seed(db)
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

## CI-Pipeline (GitHub Actions)

Drei Jobs in `.github/workflows/ci.yml`:

1. **lint** — `ruff check app/`
2. **typecheck** — `mypy app/ --ignore-missing-imports`
3. **test** — pytest mit MariaDB-Service-Container (Python 3.14)

```yaml
services:
  mariadb:
    image: mariadb:10.11
    env:
      MARIADB_DATABASE: einsatzleiter_test
      MARIADB_USER: einsatzleiter
      MARIADB_PASSWORD: testpass
```

## Design-Prinzip: keine DB-Mocks für Integrations-Tests

Unit-Tests (Payload-Validierung, Berechnungen, Aggregations-Helfer) verwenden `unittest.mock`. Integrations-Tests, die das komplette Request/Response-Verhalten testen, laufen gegen eine echte SQLite- oder MariaDB-Datenbank. Mock/DB-Divergenz hat in der Vergangenheit Migrationsbugs verdeckt.

## Tests für neue Features schreiben

```python
# Neue Tests in tests/test_*.py
# Fixtures aus conftest.py per Parameter-Name einbinden:
def test_mein_feature(client, api_key, setup_db):
    ...

# Für reine Unit-Tests: SimpleNamespace statt SA-Modelle
from types import SimpleNamespace
org = SimpleNamespace(id=1, name="FF Test", org_id=1)
```

**Besonderheit:** SQLAlchemy `db.get(Model, id)` umgeht den `do_orm_execute`-Event-Handler. Tests für diese Stellen müssen `same_org_or_system_admin()` explizit testen, nicht den automatischen Filter.
