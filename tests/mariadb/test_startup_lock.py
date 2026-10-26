"""Paralleler Erststart in einer leeren, isolierten MariaDB-Testdatenbank.

MARIADB_TEST_URL benoetigt CREATE/DROP DATABASE fuer diese temporaere Datenbank.
SQLite kann den verbindungsgebundenen GET_LOCK nicht abbilden.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import cli, main
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.user import Role, User, UserRole
from app.seed_data import ROLES
from app.services.startup_lock import startup_lock

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


def test_parallel_first_and_repeated_bootstrap(monkeypatch, caplog):
    control = create_engine(os.environ["MARIADB_TEST_URL"], isolation_level="AUTOCOMMIT")
    database = "ecp_bootstrap_test_" + uuid4().hex
    engine = create_engine(control.url.set(database=database), pool_size=6)
    sessions = sessionmaker(engine, autoflush=False)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(cli, "SessionLocal", sessions)
    monkeypatch.setattr(main.settings, "BOOTSTRAP_ADMIN_USER", "startup-test-admin")
    monkeypatch.setattr(main.settings, "BOOTSTRAP_ADMIN_PASSWORD", "startup-test-password")
    try:
        with control.connect() as connection:
            connection.execute(text(f"CREATE DATABASE `{database}`"))
        Base.metadata.create_all(engine)
        for _ in range(2):
            barrier = Barrier(2)

            def bootstrap(_, barrier=barrier):
                barrier.wait(timeout=10)
                with startup_lock(engine):
                    main._bootstrap_admin()

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(bootstrap, number) for number in range(2)]
                for future in futures:
                    future.result(timeout=90)
            with sessions() as db:
                set_tenant_context(db, None)
                assert db.query(User).count() == 1
                assert db.query(User).one().username == "startup-test-admin"
                assert db.query(Role).count() == len(ROLES)
                assert {role.code for role in db.query(Role)} == {role["code"] for role in ROLES}
                assignment = db.query(UserRole).one()
                assert db.get(Role, assignment.role_id).code == "system_admin"
        assert "Bootstrap-Admin/Seed fehlgeschlagen" not in caplog.text
        assert "Startup-Lock ecp_bootstrap nicht erhalten" not in caplog.text
    finally:
        engine.dispose()
        try:
            with control.connect() as connection:
                connection.execute(text(f"DROP DATABASE IF EXISTS `{database}`"))
        finally:
            control.dispose()
