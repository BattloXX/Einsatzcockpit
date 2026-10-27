"""RFC-6238, verschluesselte Faktoren und persistente Einmalverwendung."""
import base64
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.crypto import decrypt_secret
from app.core.security import hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import Base, get_db
from app.main import app
from app.models.admin_security import AdminRecoveryCode, AdminTotp
from app.models.master import SystemSettings
from app.models.user import AuditLog, Role, User, UserRole
from app.services import totp_service as service


@pytest.fixture
def mfa_env(monkeypatch):
    import app.main as main

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    set_tenant_context(db, None)
    role = Role(code="system_admin", label="Systemadministrator")
    user = User(username="mfa-admin", display_name="MFA Admin", password_hash=hash_password("password"))
    regular = User(username="normal", display_name="Normal", password_hash=hash_password("password"))
    db.add_all([role, user, regular])
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()
    db.expire_all()
    user = db.get(User, user.id)
    monkeypatch.setattr(main, "SessionLocal", factory)

    def override():
        with factory() as session:
            set_tenant_context(session, None)
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    client.get("/login")
    yield db, user, regular, client
    client.close()
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    db.close()
    engine.dispose()


def enroll(db, user):
    row = service.start_setup(db, user)
    secret = decrypt_secret(row.secret_enc)
    code = service.totp(secret, int(time.time()) // 30)
    assert service.verify(db, user.id, code, confirming=True)
    codes = service.create_recovery_codes(db, user.id)
    db.commit()
    return secret, codes


@pytest.mark.parametrize("timestamp,expected", [
    (59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
    (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130"),
])
def test_rfc6238_sha1(timestamp, expected):
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert service.totp(secret, timestamp // 30, digits=8) == expected
    assert service.matching_step(secret, expected[-6:], timestamp) == timestamp // 30


def test_window_and_format():
    secret = service.generate_secret()
    assert len(base64.b32decode(secret)) == 20
    for offset in (-1, 0, 1):
        assert service.matching_step(secret, service.totp(secret, 100 + offset), 3000) == 100 + offset
    for offset in (-2, 2):
        assert service.matching_step(secret, service.totp(secret, 100 + offset), 3000) is None
    for invalid in ("", "12345", "1234567", "１２３４５６", "abcdef"):
        assert service.matching_step(secret, invalid, 3000) is None


def test_encryption_replay_and_recovery(mfa_env):
    db, user, _, _ = mfa_env
    secret, codes = enroll(db, user)
    row = db.get(AdminTotp, user.id)
    assert row.secret_enc != secret and secret not in row.secret_enc
    code = service.totp(secret, int(time.time()) // 30)
    assert not service.verify(db, user.id, code)
    assert service.verify(db, user.id, codes[0])
    assert not service.verify(db, user.id, codes[0])
    stored = db.query(AdminRecoveryCode).all()
    assert len(stored) == 10 and len(set(codes)) == 10
    assert all(r.code_hash not in codes and len(r.code_hash) == 64 for r in stored)
    payloads = " ".join(row.payload_json or "" for row in db.query(AuditLog).all())
    assert secret not in payloads and not any(code in payloads for code in codes)
    assert {r.action for r in db.query(AuditLog)} >= {
        "admin.mfa.setup_started", "admin.mfa.confirmed", "admin.mfa.failed", "admin.mfa.recovery_used",
    }


def test_persistent_lockout(mfa_env):
    db, user, _, _ = mfa_env
    _, codes = enroll(db, user)
    for _ in range(service.MAX_FAILED):
        assert not service.verify(db, user.id, "wrong")
    db.commit()
    db.expire_all()
    assert not service.verify(db, user.id, codes[0])
    assert db.get(AdminTotp, user.id).locked_until is not None
    from datetime import timedelta
    db.get(AdminTotp, user.id).locked_until = service.utcnow() - timedelta(seconds=1)
    db.flush()
    assert service.verify(db, user.id, codes[0])


def test_global_requirements_and_revocation(mfa_env):
    db, user, regular, _ = mfa_env
    assert not service.enabled(db)
    db.add(SystemSettings(key="admin_2fa_enabled", value="TRUE"))
    db.flush()
    assert not service.enabled(db)
    with pytest.raises(ValueError, match="MFA Admin"):
        service.set_enabled(db, True, user.id, None)
    enroll(db, user)
    db.add(UserRole(user_id=regular.id, role_id=user.roles[0].id))
    db.commit()
    with pytest.raises(ValueError, match="Normal"):
        service.set_enabled(db, True, user.id, None)
    enroll(db, regular)
    service.set_enabled(db, True, user.id, None)
    db.commit()
    assert service.enabled(db)
    for marker in (None, int(time.time()) - 301, int(time.time()) + 60):
        with pytest.raises(ValueError):
            service.set_enabled(db, False, user.id, marker)
    service.set_enabled(db, False, user.id, int(time.time()))
    db.commit()
    assert not service.enabled(db) and user.session_epoch == 1 and regular.session_epoch == 1


def test_uri_and_qr():
    uri = service.provisioning_uri(service.generate_secret(), "admin+test@example.com")
    assert uri.startswith("otpauth://totp/Einsatzcockpit%3Aadmin%2Btest%40example.com?")
    assert "issuer=Einsatzcockpit" in uri and "algorithm=SHA1" in uri and "period=30" in uri
    assert service.qr_data_uri(uri).startswith("data:image/png;base64,iVBOR")


def test_reset_mail_when_configured(mfa_env, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services import mail_service
    db, user, _, _ = mfa_env
    user.email = "admin@example.com"
    db.commit()
    deliver = AsyncMock()
    monkeypatch.setattr(mail_service, "mail_available", lambda *args: True)
    monkeypatch.setattr(mail_service, "deliver", deliver)
    service.notify_reset(db, user)
    deliver.assert_awaited_once()
    message = deliver.call_args.args[2]
    assert message["To"] == user.email and "zurueckgesetzt" in str(message)
    monkeypatch.setattr(mail_service, "mail_available", lambda *args: False)
    service.notify_reset(db, user)
    assert deliver.await_count == 1


def test_migration_upgrade_downgrade(tmp_path):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text
    spec = importlib.util.spec_from_file_location("migration0325", "alembic/versions/0325_admin_totp.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE user (id BIGINT PRIMARY KEY)"))
        connection.execute(text("INSERT INTO user (id) VALUES (1)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert set(inspect(connection).get_table_names()) == {"user", "admin_totp", "admin_recovery_code"}
            assert tuple(connection.execute(text("SELECT session_epoch, admin_mcp_access FROM user")).one()) == (0, 0)
            migration.downgrade()
            assert inspect(connection).get_table_names() == ["user"]
            assert [c["name"] for c in inspect(connection).get_columns("user")] == ["id"]
    engine.dispose()


def test_replay_rejects_older_step_inside_window(mfa_env, monkeypatch):
    db, user, _, _ = mfa_env
    monkeypatch.setattr(service.time, "time", lambda: 3000)
    row = service.start_setup(db, user)
    secret = decrypt_secret(row.secret_enc)
    assert service.verify(db, user.id, service.totp(secret, 100), confirming=True)
    assert service.verify(db, user.id, service.totp(secret, 101))
    assert not service.verify(db, user.id, service.totp(secret, 100))
    assert not service.verify(db, user.id, service.totp(secret, 99))
    assert not service.verify(db, user.id, service.totp(secret, 101))


@pytest.mark.parametrize("broken", [False, True])
def test_qr_unavailable_keeps_manual_setup(mfa_env, monkeypatch, broken):
    import builtins
    import sys
    db, user, _, client = mfa_env
    if broken:
        original = builtins.__import__
        def no_qr(name, *args, **kwargs):
            if name == "qrcode":
                raise OSError("Defekt")
            return original(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", no_qr)
    else:
        monkeypatch.setitem(sys.modules, "qrcode", None)
    client.cookies.set("session", sign_session(user.id),
                       domain="testserver.local", path="/")
    response = client.get("/mfa/einrichten")
    assert response.status_code == 200 and "Manueller Schluessel" in response.text
    assert "QR-Code derzeit nicht verfuegbar" in response.text
