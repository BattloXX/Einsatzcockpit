"""Keine SQLite-Fixtures: der Integrationstest besitzt seine eigene Engine.

Die uebergeordnete conftest setzt DATABASE_URL auf SQLite, MARIADB_TEST_URL
bleibt davon unberuehrt. Alle geerbten Autouse-Fixtures werden hier ersetzt.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    yield


@pytest.fixture(autouse=True)
def reset_lis_in_memory_state():
    yield


@pytest.fixture(autouse=True)
def _rate_limit_zuruecksetzen():
    yield
