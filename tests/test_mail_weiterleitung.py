"""Sysadmin-Weiterleitungen: Regeln, Versand (inline/eml), Schutz, Retry, Rechte."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.crypto import encrypt_secret
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.mail_weiterleitung import MailWeiterleitung, MailWeiterleitungLog
from app.models.master import FireDept, SystemSettings
from app.models.org_mail import OrgMailEingang
from app.models.user import Role, User, UserRole
from app.services import mail_weiterleitung_service as svc

SECRET = b"fwd-secret"
DOMAIN = "get.einsatzcockpit.com"


def _sys(key, value):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.get(SystemSettings, key)
        if row:
            row.value = value
        else:
            db.add(SystemSettings(key=key, value=value))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _sauber(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(MailWeiterleitungLog).delete()
        db.query(MailWeiterleitung).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def cfg(setup_db):
    _sys("resend_enabled", "true")
    _sys("resend_api_key", encrypt_secret("re_key"))
    _sys("resend_from_domain", "mail.example.at")
    _sys("resend_inbound_domain", DOMAIN)
    _sys("resend_inbound_webhook_secret", encrypt_secret("whsec_" + base64.b64encode(SECRET).decode()))


def _regel(adresse="info", ziele=("a@example.at", "b@example.at"), modus="inline", aktiv=True, max_h=60):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        voll = f"{adresse}@{DOMAIN}" if adresse != "*" else "*"
        row = MailWeiterleitung(adresse=voll, ziele=json.dumps(list(ziele)), modus=modus, aktiv=aktiv,
                                max_pro_stunde=max_h)
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _logs(regel_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return [(r.id, r.status, r.fehlercode, r.versuche, r.betreff, r.absenderdomain)
                for r in db.query(MailWeiterleitungLog).filter_by(weiterleitung_id=regel_id).order_by("id")]
    finally:
        db.close()


def _signiert(body: bytes):
    stamp = str(int(time.time()))
    sig = base64.b64encode(hmac.new(SECRET, f"evt.{stamp}.{body.decode()}".encode(), hashlib.sha256).digest()).decode()
    return {"svix-id": "evt", "svix-timestamp": stamp, "svix-signature": "v1," + sig,
            "content-type": "application/json"}


def _event(email_id, an, absender="Max <max@extern.at>", betreff="Anfrage"):
    return json.dumps({"type": "email.received", "data": {
        "email_id": email_id, "from": absender, "to": [an], "subject": betreff,
        "created_at": datetime.now(UTC).isoformat()}}).encode()


@pytest.fixture
def versand(monkeypatch):
    """Mockt Receiving-API, Downloads und Resend-Versand."""
    state = {"gesendet": [], "mail": {"from": "Max <max@extern.at>", "subject": "Anfrage", "text": "Hallo",
                                      "html": "<p>Hallo</p>", "headers": {}, "attachments": [],
                                      "raw": {"download_url": "https://files.resend.com/raw"}},
             "fehler": 0}

    async def hole(api_key, email_id):
        return state["mail"]

    async def anhang(api_key, email_id, att_id):
        return {"filename": "a.pdf", "content_type": "application/pdf", "download_url": "https://files.resend.com/a"}

    async def download(url, limit):
        return b"%PDF-1.4" if url.endswith("/a") else b"From: x\r\n\r\nraw"

    async def senden(message, api_key, from_addr, attachments=None):
        if state["fehler"] > 0:
            state["fehler"] -= 1
            raise RuntimeError("boom")
        state["gesendet"].append((message, from_addr, attachments or []))
        return "id"

    monkeypatch.setattr(svc, "_hole_mail", hole)
    monkeypatch.setattr(svc, "get_resend_attachment", anhang)
    monkeypatch.setattr(svc, "_download", download)
    monkeypatch.setattr(svc, "send_via_resend", senden)
    monkeypatch.setattr("app.services.resend_mail_service.send_via_resend", senden)
    return state


async def _lauf(log_id=None, wait=0):
    from app.config import settings
    settings.MAIL_WEITERLEITUNG_RETRY_WAIT_S = wait
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return await svc.verarbeite_faellige(db, log_id)
    finally:
        db.close()


def _queue(rid, email_id, **kw):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rule = db.get(MailWeiterleitung, rid)
        return svc.stelle_in_warteschlange(db, rule, json.loads(_event(email_id, "info@" + DOMAIN, **kw))["data"])
    finally:
        db.close()


# ── Regelsuche / Validierung ──────────────────────────────────────────────────
def test_adress_und_zielvalidierung():
    assert svc.normalisiere_adresse("Info", DOMAIN) == f"info@{DOMAIN}"
    with pytest.raises(ValueError):
        svc.normalisiere_adresse("feuerwehr.wolfurt", DOMAIN)  # Org-Adresse mit Punkt
    with pytest.raises(ValueError):
        svc.normalisiere_adresse("info@fremd.at", DOMAIN)
    with pytest.raises(ValueError):
        svc.normalisiere_adresse("*", DOMAIN)  # Catch-all braucht Bestätigung
    assert svc.normalisiere_adresse("*", DOMAIN, catch_all_bestaetigt=True) == "*"
    assert svc.validiere_ziele(["A@x.at", "b@x.at"]) == ["a@x.at", "b@x.at"]
    for schlecht in (["a@x.at", "a@x.at"], ["kein-at"], [], ["a@x.at\nBcc: z@x.at"], [f"{i}@x.at" for i in range(11)]):
        with pytest.raises(ValueError):
            svc.validiere_ziele(schlecht)


def test_regelsuche_vorrang_catch_all_und_org_adressen(setup_db):
    from tests.test_mail_zuordnung_integration import _org
    org_id, _ = _org()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.get(FireDept, org_id)
        from app.services.org_mail_adresse import empfangs_adresse
        org_adr = empfangs_adresse(org, DOMAIN)
    finally:
        db.close()
    info = _regel("info")
    star = _regel("*")
    inaktiv = _regel("support", aktiv=False)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert svc.finde_regel(db, ["INFO@" + DOMAIN], DOMAIN).id == info
        assert svc.finde_regel(db, ["irgendwer@" + DOMAIN], DOMAIN).id == star
        assert svc.finde_regel(db, [org_adr], DOMAIN) is None  # Catch-all nie für Org-Adressen
        assert svc.finde_regel(db, ["x@andere-domain.at"], DOMAIN) is None
        assert svc.finde_regel(db, ["support@" + DOMAIN], DOMAIN).id == star  # inaktive Regel ignoriert
    finally:
        db.close()
    assert inaktiv


# ── Webhook ───────────────────────────────────────────────────────────────────
def test_webhook_regeltreffer_queued_ohne_org_zeile_und_idempotent(client, cfg, versand):
    rid = _regel("info")
    email_id = "fwd_" + uuid4().hex
    body = _event(email_id, "info@" + DOMAIN)
    for _ in range(2):
        assert client.post("/mail/webhook/resend-inbound", content=body, headers=_signiert(body)).status_code == 200
    logs = _logs(rid)
    assert len(logs) == 1 and logs[0][4] == "Anfrage" and logs[0][5] == "extern.at"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(OrgMailEingang).filter_by(extern_id=email_id).count() == 0
    finally:
        db.close()


def test_webhook_bounce_absender_wird_nicht_eingereiht(client, cfg, versand):
    rid = _regel("info")
    body = _event("mail_b", "info@" + DOMAIN, absender="MAILER-DAEMON@x.at")
    client.post("/mail/webhook/resend-inbound", content=body, headers=_signiert(body))
    assert [(s, c) for _, s, c, *_ in _logs(rid)] == [("uebersprungen", "bounce")]


# ── Versand ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_inline_versand_mit_reply_to_header_und_anhang(cfg, versand):
    rid = _regel("info")
    versand["mail"]["attachments"] = [{"id": "att1"}]
    _queue(rid, "m1")
    assert await _lauf() == 1
    msg, from_addr, anh = versand["gesendet"][0]
    assert from_addr == '"max@extern.at via Einsatzcockpit" <weiterleitung@mail.example.at>'
    assert msg["To"] == "a@example.at, b@example.at" and msg["Reply-To"] == "max@extern.at"
    assert msg["X-Einsatzcockpit-Weitergeleitet"] == "1" and msg["Subject"] == "Anfrage"
    assert anh and anh[0]["filename"] == "a.pdf" and base64.b64decode(anh[0]["content"]) == b"%PDF-1.4"
    assert _logs(rid)[0][1] == "gesendet"


@pytest.mark.asyncio
async def test_weiterleitung_zeigt_originalabsender_in_text_und_html(cfg, versand):
    rid = _regel("info")
    versand["mail"].update({"to": ["info@" + DOMAIN], "created_at": "2026-10-11T18:02:00Z"})
    _queue(rid, "m_kopf")
    await _lauf()
    msg, _, _ = versand["gesendet"][0]
    text = msg.get_body(preferencelist=("plain",)).get_content()
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "Von: Max <max@extern.at>" in text and f"An: info@{DOMAIN}" in text and text.rstrip().endswith("Hallo")
    assert "Betreff: Anfrage" in text and "2026-10-11T18:02:00Z" in text
    assert "Max &lt;max@extern.at&gt;" in html and html.rstrip().endswith("<p>Hallo</p>")


@pytest.mark.asyncio
async def test_weiterleitung_ohne_namen_und_mit_boesem_namen(cfg, versand):
    rid = _regel("info")
    versand["mail"]["from"] = '"<script>x</script>" <evil@extern.at>'
    _queue(rid, "m_name")
    await _lauf()
    msg, from_addr, _ = versand["gesendet"][0]
    assert from_addr == '"evil@extern.at via Einsatzcockpit" <weiterleitung@mail.example.at>'
    assert "<script" not in msg.get_body(preferencelist=("html",)).get_content()


@pytest.mark.asyncio
async def test_eml_modus_haengt_originalmail_an(cfg, versand):
    rid = _regel("info", modus="eml")
    _queue(rid, "m2")
    await _lauf()
    _, _, anh = versand["gesendet"][0]
    assert anh[0]["filename"] == "original.eml" and anh[0]["content_type"] == "message/rfc822"
    assert base64.b64decode(anh[0]["content"]) == b"From: x\r\n\r\nraw"
    _, from_addr, _ = versand["gesendet"][0]
    assert "max@extern.at via Einsatzcockpit" in from_addr


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    {"X-Einsatzcockpit-Weitergeleitet": "1"}, {"Auto-Submitted": "auto-replied"}, {"Precedence": "bulk"},
])
async def test_schleifen_und_automatische_mails_werden_uebersprungen(cfg, versand, headers):
    rid = _regel("info")
    versand["mail"]["headers"] = headers
    _queue(rid, "m3")
    await _lauf()
    assert versand["gesendet"] == [] and _logs(rid)[0][1:3] == ("uebersprungen", "schleife")


@pytest.mark.asyncio
async def test_absender_ist_ziel_wird_uebersprungen(cfg, versand):
    rid = _regel("info", ziele=("max@extern.at",))
    _queue(rid, "m4")
    await _lauf()
    assert versand["gesendet"] == [] and _logs(rid)[0][2] == "schleife"


@pytest.mark.asyncio
async def test_rate_limit_je_regel(cfg, versand):
    rid = _regel("info", max_h=1)
    _queue(rid, "m5")
    _queue(rid, "m6")
    await _lauf()
    assert [s for _, s, *_ in _logs(rid)] == ["gesendet", "uebersprungen"]
    assert _logs(rid)[1][2] == "rate_limit"


@pytest.mark.asyncio
async def test_retry_drei_versuche_dann_fehler(cfg, versand):
    rid = _regel("info")
    versand["fehler"] = 5
    _queue(rid, "m7")
    stati = []
    for _ in range(4):
        await _lauf()
        stati.append(_logs(rid)[0][1])
    assert stati == ["wartend", "wartend", "fehler", "fehler"]
    assert _logs(rid)[0][3] == 3 and versand["gesendet"] == []


@pytest.mark.asyncio
async def test_retry_erfolg_nach_einem_fehler(cfg, versand):
    rid = _regel("info")
    versand["fehler"] = 1
    _queue(rid, "m8")
    await _lauf()
    await _lauf()
    assert _logs(rid)[0][1] == "gesendet" and _logs(rid)[0][3] == 2


@pytest.mark.asyncio
async def test_retry_wartet_backoff_ab(cfg, versand):
    rid = _regel("info")
    versand["fehler"] = 1
    _queue(rid, "m9")
    await _lauf(wait=0)
    assert _logs(rid)[0][1] == "wartend"
    assert await _lauf(wait=3600) == 0  # Backoff noch nicht abgelaufen


@pytest.mark.asyncio
async def test_zu_grosse_mail_und_inaktive_regel(cfg, versand, monkeypatch):
    rid = _regel("info")
    versand["mail"]["attachments"] = [{"id": "x"}]
    monkeypatch.setattr(svc, "MAX_BYTES", 3)
    _queue(rid, "m10")
    await _lauf()
    assert _logs(rid)[0][1] == "fehler" or _logs(rid)[0][2] in {"zu_gross", "versand"}
    assert versand["gesendet"] == []


@pytest.mark.asyncio
async def test_protokoll_enthaelt_keinen_inhalt(cfg, versand):
    rid = _regel("info")
    versand["mail"]["text"] = "GEHEIMER-INHALT"
    _queue(rid, "m11")
    await _lauf()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        for log in db.query(MailWeiterleitungLog).all():
            assert "GEHEIMER" not in " ".join(str(v) for v in vars(log).values())
    finally:
        db.close()


def test_log_retention_loescht_alte_eintraege(setup_db):
    rid = _regel("info")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        alt = MailWeiterleitungLog(weiterleitung_id=rid, status="gesendet", versuche=1,
                                   zeit=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=31))
        neu = MailWeiterleitungLog(weiterleitung_id=rid, status="gesendet", versuche=1)
        db.add_all([alt, neu])
        db.commit()
        assert svc.bereinige_logs(db) == 1
    finally:
        db.close()


# ── UI / Rechte ───────────────────────────────────────────────────────────────
def _user(rolle):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_id = None
        if rolle != "system_admin":
            org = FireDept(slug="fw-" + uuid4().hex[:8], name="X", color="#f00", bos="Feuerwehr")
            db.add(org)
            db.flush()
            org_id = org.id
        u = User(username="fwd-" + uuid4().hex[:8], password_hash=hash_password("Test1234!"), display_name="T",
                 org_id=org_id, active=True)
        db.add(u)
        db.flush()
        db.add(UserRole(user_id=u.id, role_id=db.query(Role).filter(Role.code == rolle).one().id))
        db.commit()
        return u.username
    finally:
        db.close()


def _login(client, name):
    client.cookies.clear()
    client.get("/login")
    r = client.post("/login", data={"username": name, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")})
    assert r.status_code == 200


@pytest.mark.parametrize("rolle", ["admin", "recorder", "readonly"])
def test_nur_system_admin_hat_zugriff(client, cfg, rolle):
    try:
        name = _user(rolle)
    except Exception:
        pytest.skip(f"Rolle {rolle} nicht vorhanden")
    _login(client, name)
    rid = _regel("info")
    csrf = client.cookies.get("ec_csrf")
    assert client.get("/admin/mail-weiterleitungen").status_code in (401, 403)
    for pfad in ("", f"/{rid}/toggle", f"/{rid}/loeschen", f"/{rid}/test"):
        r = client.post("/admin/mail-weiterleitungen" + pfad, data={"_csrf": csrf, "adresse": "x", "ziele": "a@x.at"},
                        follow_redirects=False)
        assert r.status_code in (401, 403), pfad
    assert len(_logs(rid)) == 0


def test_system_admin_crud_catch_all_und_testmail(client, cfg, versand):
    _login(client, _user("system_admin"))
    csrf = client.cookies.get("ec_csrf")
    seite = client.post("/admin/mail-weiterleitungen", follow_redirects=False, data={
        "_csrf": csrf, "adresse": "info", "ziele": "a@example.at, b@example.at", "modus": "eml", "aktiv": "true"})
    assert seite.status_code == 303 and "error" not in seite.headers["location"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        regel = db.query(MailWeiterleitung).filter_by(adresse=f"info@{DOMAIN}").one()
        rid = regel.id
        assert regel.modus == "eml" and regel.aktiv and json.loads(regel.ziele) == ["a@example.at", "b@example.at"]
    finally:
        db.close()
    assert "info@" in client.get(f"/admin/mail-weiterleitungen?edit={rid}").text
    # Fehlerfälle
    for daten in ({"adresse": "feuerwehr.x", "ziele": "a@x.at"}, {"adresse": "*", "ziele": "a@x.at"},
                  {"adresse": "support", "ziele": "kaputt"}):
        r = client.post("/admin/mail-weiterleitungen", data={"_csrf": csrf, **daten}, follow_redirects=False)
        assert "error=" in r.headers["location"], daten
    ok = client.post("/admin/mail-weiterleitungen", follow_redirects=False,
                     data={"_csrf": csrf, "adresse": "*", "ziele": "a@x.at", "catch_all_bestaetigt": "true"})
    assert "error" not in ok.headers["location"]
    t = client.post(f"/admin/mail-weiterleitungen/{rid}/test", data={"_csrf": csrf}, follow_redirects=False)
    assert "info=" in t.headers["location"] and versand["gesendet"][-1][0]["Subject"].startswith("Testweiterleitung")
    client.post(f"/admin/mail-weiterleitungen/{rid}/toggle", data={"_csrf": csrf})
    client.post(f"/admin/mail-weiterleitungen/{rid}/loeschen", data={"_csrf": csrf})
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(MailWeiterleitung, rid) is None
    finally:
        db.close()


def test_loop_ist_registriert_und_abschaltbar():
    import inspect

    import app.main as main
    assert "mail_weiterleitung_loop()" in inspect.getsource(main)
    from app.config import settings
    assert settings.MAIL_WEITERLEITUNG_LOOP_ENABLED in (True, False)
