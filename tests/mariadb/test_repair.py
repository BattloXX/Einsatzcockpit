"""0328: echtes MariaDB-Schema, Mutex über Sessions und bedingter Einmalverbrauch."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.models.admin_repair import AdminApproval, AdminRepairRun
from app.models.admin_security import utcnow
from app.models.user import User
from app.services.repair.runner import action_hash, claim_run, consume_approval
from app.core.security import hash_api_key

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


def test_0328_schema_mutex_and_approval():
    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    factory = sessionmaker(engine)
    ids = []
    created_user_id = None
    try:
        schema = inspect(engine)
        for name in ("admin_repair_run", "admin_approval"):
            assert "org_id" not in {c["name"] for c in schema.get_columns(name)}
        assert any(fk["referred_table"] == "diag_finding" for fk in schema.get_foreign_keys("admin_repair_run"))
        assert any(fk["referred_table"] == "admin_repair_run" for fk in schema.get_foreign_keys("admin_approval"))
        for column in ("active_aktion", "idempotency_key"):
            assert any(i["column_names"] == [column] and i["unique"] for i in schema.get_indexes("admin_repair_run"))
        with factory() as db:
            user = User(username="repair-mariadb-" + uuid4().hex, display_name="Repair Test", password_hash="test")
            db.add(user)
            db.flush()
            user_id = created_user_id = user.id
            for _ in range(2):
                run = AdminRepairRun(aktion_id="test.mariadb", risiko="high", status="simuliert", scope={"id": 1},
                    simulation_hash="f" * 64, simulated_at=utcnow(), korrelations_id=str(uuid4()), angefordert_von=user_id)
                db.add(run)
                db.flush()
                ids.append(run.id)
            db.commit()
        barrier = Barrier(2)
        def claim(ident):
            with factory() as db:
                barrier.wait(timeout=10)
                return claim_run(db, ident, uuid4().hex)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ids))
        assert sorted(results) == [False, True]
        with factory() as db:
            run = db.get(AdminRepairRun, ids[0])
            token = uuid4().hex
            now = utcnow()
            row = AdminApproval(repair_run_id=run.id, user_id=user_id, token_hash=hash_api_key(token),
                aktion_hash=action_hash(run), created_at=now, expires_at=now + timedelta(minutes=10))
            db.add(row)
            db.commit()
            bad = AdminApproval(repair_run_id=run.id, user_id=user_id, token_hash=uuid4().hex,
                aktion_hash=action_hash(run), created_at=now, expires_at=now + timedelta(minutes=11))
            db.add(bad)
            with pytest.raises((IntegrityError, OperationalError)):
                db.commit()
            db.rollback()
        barrier = Barrier(2)
        def consume(_):
            with factory() as db:
                run = db.get(AdminRepairRun, ids[0])
                barrier.wait(timeout=10)
                try:
                    consume_approval(db, run, user_id, token)
                    db.commit()
                    return True
                except ValueError:
                    db.rollback()
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(consume, range(2))) == [False, True]
    finally:
        if ids:
            with factory() as db:
                db.query(AdminApproval).filter(AdminApproval.repair_run_id.in_(ids)).delete()
                db.query(AdminRepairRun).filter(AdminRepairRun.id.in_(ids)).delete()
                db.commit()
        if created_user_id is not None:
            with factory() as db:
                db.query(User).filter(User.id == created_user_id).delete()
                db.commit()
        engine.dispose()


def test_repair_live_roles_are_read_after_concurrent_revocation():
    from app.core.tenant import set_tenant_context
    from app.models.master import SystemSettings
    from app.models.user import Role, UserRole
    from app.services.repair.runner import live_user, prepare_session
    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    factory = sessionmaker(engine)
    user_id, old = None, {}
    try:
        with factory() as db:
            set_tenant_context(db, None)
            role = db.scalar(select(Role).where(Role.code == "system_admin"))
            user = User(username="repair-live-" + uuid4().hex, display_name="Repair Test", password_hash="test",
                        admin_mcp_access=True)
            db.add(user)
            db.flush()
            user_id = user.id
            db.add(UserRole(user_id=user_id, role_id=role.id))
            for key in ("admin_2fa_enabled", "admin_mcp_enabled", "admin_repair_enabled"):
                row = db.get(SystemSettings, key)
                old[key] = row.value if row else None
                if row is None:
                    row = SystemSettings(key=key)
                    db.add(row)
                row.value = "true"
            db.commit()
        with factory() as reader:
            set_tenant_context(reader, None)
            prepare_session(reader)
            assert reader.connection().get_isolation_level() == "READ COMMITTED"
            assert live_user(reader, user_id, 0).is_system_admin
            with factory() as writer:
                writer.query(UserRole).filter(UserRole.user_id == user_id).delete()
                writer.commit()
            # Dieselbe offene Transaktion muss den Rollenentzug sofort erkennen.
            with pytest.raises(ValueError, match="widerrufen"):
                live_user(reader, user_id, 0)
            reader.commit()
            assert reader.connection().get_isolation_level() == "READ COMMITTED"
    finally:
        with factory() as db:
            if user_id is not None:
                db.query(UserRole).filter(UserRole.user_id == user_id).delete()
                db.query(User).filter(User.id == user_id).delete()
            for key, value in old.items():
                row = db.get(SystemSettings, key)
                if value is None:
                    db.delete(row)
                else:
                    row.value = value
            db.commit()
        engine.dispose()
