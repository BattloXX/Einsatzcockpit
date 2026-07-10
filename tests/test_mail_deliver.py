"""mail_service.deliver(): Fallback-Kette Office 365 -> eigener SMTP der Org ->
globaler SMTP. Deckt die Reihenfolge, den Fehlerkontrakt (Graph-Fehler
verschluckt, SMTP-Fehler propagiert) und den globalen O365-Kill-Switch ab."""
from email.message import EmailMessage

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


import app.services.mail_service as mail_service
import app.services.o365_mail_service as o365_mail_service
from app.config import settings
from app.core.crypto import encrypt_secret
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept
from app.models.org_mail import OrgO365MailConfig, OrgSmtpConfig


@pytest.fixture()
def deliver_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org = FireDept(slug="deliver-org", name="Deliver Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.commit()

    yield db, org

    db.close()
    Base.metadata.drop_all(bind=engine)


def _msg() -> EmailMessage:
    m = EmailMessage()
    m["To"] = "empfaenger@example.at"
    m["Subject"] = "Test"
    m.set_content("Testinhalt", subtype="plain")
    return m


def _patch_send(monkeypatch):
    """Zeichnet Aufrufe von mail_service._send auf, ohne echt zu senden."""
    calls: list[dict] = []

    async def fake_send(msg, smtp_cfg):
        calls.append(smtp_cfg)

    monkeypatch.setattr(mail_service, "_send", fake_send)
    return calls


def _patch_graph(monkeypatch, *, raises: Exception | None = None):
    """Zeichnet Aufrufe von send_via_graph auf; wirft optional einen Fehler."""
    calls: list = []

    async def fake_send_via_graph(msg, cfg):
        calls.append(cfg)
        if raises:
            raise raises

    monkeypatch.setattr(o365_mail_service, "send_via_graph", fake_send_via_graph)
    return calls


async def test_deliver_uses_graph_when_o365_enabled_and_configured(deliver_db, monkeypatch):
    db, org = deliver_db
    db.add(OrgO365MailConfig(
        org_id=org.id, enabled=True,
        tenant_id="tid", client_id="cid",
        client_secret_enc=encrypt_secret("sec"),
        sender_address="einsatz@example.at",
    ))
    db.commit()

    send_calls = _patch_send(monkeypatch)
    graph_calls = _patch_graph(monkeypatch)

    await mail_service.deliver(db, org.id, _msg())

    assert len(graph_calls) == 1
    assert send_calls == []  # kein SMTP-Versand noetig, Graph war erfolgreich


async def test_deliver_uses_org_smtp_when_o365_disabled(deliver_db, monkeypatch):
    db, org = deliver_db
    db.add(OrgSmtpConfig(
        org_id=org.id, enabled=True,
        host="smtp.org-eigen.at", port=587, user="user@org-eigen.at",
        password_enc=encrypt_secret("pw"), from_addr="einsatz@org-eigen.at",
        starttls=True, timeout=15,
    ))
    db.commit()

    send_calls = _patch_send(monkeypatch)
    graph_calls = _patch_graph(monkeypatch)

    await mail_service.deliver(db, org.id, _msg())

    assert graph_calls == []  # O365 nicht konfiguriert -> nie versucht
    assert len(send_calls) == 1
    assert send_calls[0]["host"] == "smtp.org-eigen.at"


async def test_deliver_falls_back_to_global_smtp_without_org_config(deliver_db, monkeypatch):
    db, org = deliver_db
    # Keine OrgO365MailConfig, keine OrgSmtpConfig fuer diese Org angelegt.

    send_calls = _patch_send(monkeypatch)
    graph_calls = _patch_graph(monkeypatch)
    expected_global_cfg = mail_service.get_smtp_cfg(db)

    await mail_service.deliver(db, org.id, _msg())

    assert graph_calls == []
    assert len(send_calls) == 1
    assert send_calls[0] == expected_global_cfg


async def test_deliver_falls_back_to_org_smtp_when_graph_fails(deliver_db, monkeypatch):
    db, org = deliver_db
    db.add(OrgO365MailConfig(
        org_id=org.id, enabled=True,
        tenant_id="tid", client_id="cid",
        client_secret_enc=encrypt_secret("sec"),
        sender_address="einsatz@example.at",
    ))
    db.add(OrgSmtpConfig(
        org_id=org.id, enabled=True,
        host="smtp.org-eigen.at", port=587, user="user@org-eigen.at",
        password_enc=encrypt_secret("pw"), from_addr="einsatz@org-eigen.at",
        starttls=True, timeout=15,
    ))
    db.commit()

    send_calls = _patch_send(monkeypatch)
    graph_calls = _patch_graph(monkeypatch, raises=o365_mail_service.O365MailError("boom"))

    # Graph-Fehler wird verschluckt -> deliver() darf NICHT werfen
    await mail_service.deliver(db, org.id, _msg())

    assert len(graph_calls) == 1
    assert len(send_calls) == 1
    assert send_calls[0]["host"] == "smtp.org-eigen.at"  # nicht der globale Fallback


async def test_deliver_propagates_final_smtp_failure(deliver_db, monkeypatch):
    """Schlaegt auch der SMTP-Fallback fehl, muss der Fehler weiter nach oben
    durchgereicht werden (Aufrufer wie schaden_service protokollieren sonst
    faelschlich einen Erfolg)."""
    db, org = deliver_db

    async def failing_send(msg, smtp_cfg):
        raise RuntimeError("SMTP down")

    monkeypatch.setattr(mail_service, "_send", failing_send)
    _patch_graph(monkeypatch)

    with pytest.raises(RuntimeError, match="SMTP down"):
        await mail_service.deliver(db, org.id, _msg())


async def test_deliver_skips_graph_when_globally_disabled(deliver_db, monkeypatch):
    db, org = deliver_db
    db.add(OrgO365MailConfig(
        org_id=org.id, enabled=True,
        tenant_id="tid", client_id="cid",
        client_secret_enc=encrypt_secret("sec"),
        sender_address="einsatz@example.at",
    ))
    db.commit()

    monkeypatch.setattr(settings, "O365_MAIL_ENABLED", False)
    send_calls = _patch_send(monkeypatch)
    graph_calls = _patch_graph(monkeypatch)

    await mail_service.deliver(db, org.id, _msg())

    assert graph_calls == []  # globaler Kill-Switch greift, obwohl Org-Config aktiv ist
    assert len(send_calls) == 1


async def test_deliver_with_org_id_none_uses_global_smtp_directly(deliver_db, monkeypatch):
    db, org = deliver_db

    send_calls = _patch_send(monkeypatch)
    graph_calls = _patch_graph(monkeypatch)
    expected_global_cfg = mail_service.get_smtp_cfg(db)

    await mail_service.deliver(db, None, _msg())

    assert graph_calls == []
    assert len(send_calls) == 1
    assert send_calls[0] == expected_global_cfg


async def test_deliver_explicit_smtp_cfg_bypasses_org_lookup(deliver_db, monkeypatch):
    """Wenn ein Aufrufer bereits ein smtp_cfg aufgeloest hat (z.B. weil er es fuer
    _build_message brauchte), soll deliver() dieses unveraendert als Fallback
    nutzen, statt es erneut aus der DB aufzuloesen."""
    db, org = deliver_db
    db.add(OrgSmtpConfig(
        org_id=org.id, enabled=True,
        host="smtp.org-eigen.at", port=587, user="user@org-eigen.at",
        password_enc=encrypt_secret("pw"), from_addr="einsatz@org-eigen.at",
        starttls=True, timeout=15,
    ))
    db.commit()

    send_calls = _patch_send(monkeypatch)
    explicit_cfg = {"host": "explicit.example.at", "port": 25, "user": None,
                    "password": None, "from_addr": "x@example.at", "starttls": False, "timeout": 5}

    await mail_service.deliver(db, org.id, _msg(), smtp_cfg=explicit_cfg)

    assert send_calls == [explicit_cfg]
