"""P4: Limits, Sperre und Einmalcodes mit alten MariaDB-Snapshots."""
import hashlib
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app import db as app_db
from app.config import settings
from app.core.crypto import encrypt_secret
from app.core.tenant import set_tenant_context
from app.models.admin_security import UserMfaSms
from app.models.login_pin import LoginPin
from app.models.master import FireDept
from app.models.user import AuditLog, User
from app.services import mfa_service as mfa
from app.services import mfa_sms_service as sms
from app.services.sms_service import SmsDeliveryResult
from tests.mariadb.test_high_risk_snapshot_claims import _race

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


@pytest.fixture
def database(monkeypatch):
    monkeypatch.setattr(settings, "DB_SNAPSHOT_ISOLATION_OFF", True)
    engine = create_engine(os.environ["MARIADB_TEST_URL"], isolation_level="REPEATABLE READ")
    event.listen(engine, "connect", app_db._set_db_timezone_utc)
    monkeypatch.setattr(app_db, "engine", engine)
    factory = sessionmaker(engine, autoflush=False)
    sent = []

    def session():
        db = factory()
        set_tenant_context(db, None)
        db.execute(text("SET SESSION innodb_lock_wait_timeout=10"))
        db.commit()
        return db

    async def send(*args):
        sent.append(args)
        return SmsDeliveryResult(success=True)

    monkeypatch.setattr(sms, "send_sms", send)
    monkeypatch.setattr(sms, "sms_available", lambda *args: True)
    with session() as db:
        org = FireDept(name="P4 parallel", slug=uuid4().hex)
        db.add(org)
        db.flush()
        user = User(org_id=org.id, username=uuid4().hex, display_name="P4 parallel")
        db.add(user)
        db.flush()
        db.add(UserMfaSms(user_id=user.id, phone_enc=encrypt_secret("+436641234567"), verified_at=mfa.utcnow()))
        db.commit()
        org_id, user_id = org.id, user.id
    try:
        yield session, user_id, sent
    finally:
        with session() as db:
            for model in (AuditLog, LoginPin, UserMfaSms):
                for row in db.query(model).filter(model.user_id == user_id).all():
                    db.delete(row)
            db.flush()
            db.delete(db.get(User, user_id))
            db.flush()
            db.delete(db.get(FireDept, org_id))
            db.commit()
        engine.dispose()


def _load(db, user_id):
    user = db.get(User, user_id)
    # Beide Transaktionen haben ihren Snapshot vor dem ersten Versand/Versuch.
    db.query(LoginPin).filter(LoginPin.user_id == user_id).all()
    db.get(UserMfaSms, user_id)
    return user


def test_parallel_send_observes_latest_limit(database):
    session, user_id, sent = database
    with session() as db:
        for _ in range(2):
            db.add(LoginPin(user_id=user_id, purpose="mfa", pin_hash="0" * 64,
                            created_at=mfa.utcnow(), expires_at=mfa.utcnow() + timedelta(minutes=5)))
        db.commit()

    def action(db, user, index):
        try:
            sms.send_code(db, user, "mfa")
        except ValueError as exc:
            assert str(exc) == "limit"
            return False
        return True

    assert _race(session, lambda db: _load(db, user_id), action) == [True, False]
    assert len(sent) == 1


@pytest.mark.parametrize("wrong", [False, True])
def test_parallel_verify_single_use_and_lock(database, wrong):
    session, user_id, _ = database
    with session() as db:
        factor = db.get(UserMfaSms, user_id)
        factor.failed_count = 4 if wrong else 0
        db.add(LoginPin(user_id=user_id, purpose="mfa", pin_hash=hashlib.sha256(b"123456").hexdigest(),
                        created_at=mfa.utcnow(), expires_at=mfa.utcnow() + timedelta(minutes=5)))
        db.commit()

    results = _race(session, lambda db: _load(db, user_id),
                    lambda db, user, index: sms.verify_sms(db, user, "000000" if wrong else "123456", "mfa"))
    assert results == ([False, False] if wrong else [True, False])
    with session() as db:
        factor = db.get(UserMfaSms, user_id)
        if wrong:
            assert factor.failed_count == 5 and factor.locked_until > mfa.utcnow()
        else:
            assert db.query(LoginPin).filter_by(user_id=user_id, purpose="mfa").one().used_at is not None
