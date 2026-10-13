"""Integrationstests: Mail → Meldung im Einsatz (Zuordnung, Anhänge, Rechte, Mandanten)."""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, Message, MessageMedia
from app.models.master import OrgSettings
from app.models.org_mail import OrgMailEingang
from app.services import mail_eingang_anhang as anhang_service
from app.services import mail_zuordnung_service as zuordnung
from tests.test_resend_posteingang import _login, _org


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (3, 3)).save(buf, "PNG")
    return buf.getvalue()


def _einsatz(org_id, *, exercise=False, status="active", started_at=None):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        inc = Incident(primary_org_id=org_id, alarm_type_code="T1", status=status,
                       started_at=started_at or datetime.now(UTC), is_exercise=exercise)
        db.add(inc)
        db.flush()
        db.add(IncidentColumn(incident_id=inc.id, code="messages", column_kind="messages", title="Meldungen"))
        db.commit()
        return inc.id
    finally:
        db.close()


def _settings(org_id, modus="freigegeben", absender="@example.at", mehrere="posteingang", rueckblick=30):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first() or OrgSettings(org_id=org_id)
        row.mail_eingang_modus, row.mail_eingang_absender, row.mail_eingang_mehrere = modus, absender, mehrere
        row.mail_eingang_rueckblick_min = rueckblick
        db.add(row)
        db.commit()
    finally:
        db.close()


def _mail(org_id, *, absender="lls@example.at", betreff="Lagebild", anhaenge=(), text="Hallo Einsatz",
          email_id=None, abgerufen=True):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = OrgMailEingang(
            org_id=org_id, quelle="resend", extern_id=email_id or f"m_{datetime.now().timestamp()}", absender=absender,
            empfaenger="[]", betreff=betreff, empfangen_at=datetime.now(), text_body=text,
            anhang_json=json.dumps([{"id": a[0], "filename": a[1]} for a in anhaenge]),
            abgerufen_at=datetime.now() if abgerufen else None,
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _zustand(eingang_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.get(OrgMailEingang, eingang_id)
        return row.status, row.zuordnung, row.incident_id, row.message_id_ref, row.zuordnung_grund
    finally:
        db.close()


async def _lauf(eingang_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return await zuordnung.ordne_zu(db, eingang_id)
    finally:
        db.close()


@pytest.fixture
def anhaenge(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    daten = {
        "a_png": ("bild.png", "image/png", _png()),
        "a_pdf": ("doc.pdf", "application/pdf", b"%PDF-1.4\n%%EOF"),
        "a_txt": ("notiz.txt", "text/plain", b"hallo"),
    }

    async def fake_attachment(api_key, email_id, att_id):
        name, ctype, _ = daten[att_id]
        return {"filename": name, "content_type": ctype, "download_url": f"https://files.resend.com/{att_id}"}

    class _Resp:
        def __init__(self, content):
            self.content, self.is_success = content, True

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def aiter_bytes(self):
            yield self.content

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            return _Resp(daten[url.rsplit("/", 1)[1]][2])

        def stream(self, method, url):
            return _Resp(daten[url.rsplit("/", 1)[1]][2])

    monkeypatch.setattr(anhang_service, "get_resend_attachment", fake_attachment)
    monkeypatch.setattr(anhang_service.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(zuordnung, "broadcast_message_created", _noop_broadcast)


async def _noop_broadcast(*a, **k):
    return None


@pytest.mark.asyncio
async def test_freigegebener_absender_erzeugt_meldung_mit_anhaengen(setup_db, anhaenge):
    org_id, _ = _org()
    inc_id = _einsatz(org_id)
    _settings(org_id)
    mail_id = _mail(org_id, anhaenge=[("a_png", "bild.png"), ("a_pdf", "doc.pdf"), ("a_txt", "notiz.txt")])
    assert await _lauf(mail_id) is True
    status, art, incident_id, msg_id, grund = _zustand(mail_id)
    assert (status, art, incident_id, grund) == ("zugeordnet", "auto", inc_id, None)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        msg = db.get(Message, msg_id)
        assert msg.title == "Lagebild" and "Hallo Einsatz" in msg.detail
        assert msg.detail.startswith("Von: lls@example.at")
        kinds = sorted(m.kind for m in db.query(MessageMedia).filter_by(message_id=msg_id))
        assert kinds == ["datei", "image", "pdf"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_alle_ordnet_mail_ohne_authentifizierungswerte_automatisch_zu(setup_db, anhaenge):
    org_id, _ = _org()
    inc_id = _einsatz(org_id)
    _settings(org_id, modus="alle")
    mail_id = _mail(org_id)
    assert await _lauf(mail_id) is True
    assert _zustand(mail_id)[:3] == ("zugeordnet", "auto", inc_id)


@pytest.mark.asyncio
async def test_idempotent_zweiter_lauf_erzeugt_keine_zweite_meldung(setup_db, anhaenge):
    org_id, _ = _org()
    inc_id = _einsatz(org_id)
    _settings(org_id)
    mail_id = _mail(org_id)
    assert await _lauf(mail_id) is True
    assert await _lauf(mail_id) is False
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(Message).filter(Message.incident_id == inc_id).count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_nicht_freigegebener_absender_und_modus_aus(setup_db, anhaenge):
    org_id, _ = _org()
    _einsatz(org_id)
    _settings(org_id, absender="@anders.at")
    mail_id = _mail(org_id)
    assert await _lauf(mail_id) is False
    assert _zustand(mail_id)[:4] == ("posteingang", "abgelehnt", None, None)
    _settings(org_id, modus="aus")
    mail2 = _mail(org_id)
    assert await _lauf(mail2) is False
    assert _zustand(mail2)[3] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("option,erwartet", [("posteingang", "mehrdeutig"), ("neuester", "auto")])
async def test_mehrere_aktive_einsaetze(setup_db, anhaenge, option, erwartet):
    org_id, _ = _org()
    _einsatz(org_id)
    neu = _einsatz(org_id)
    _settings(org_id, mehrere=option)
    mail_id = _mail(org_id)
    await _lauf(mail_id)
    status, art, incident_id, *_ = _zustand(mail_id)
    assert art == erwartet
    assert (incident_id == neu) if option == "neuester" else (incident_id is None and status == "posteingang")


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["posteingang", "neuester"])
async def test_leitstellennummer_ordnet_aelteren_einsatz_zu(setup_db, anhaenge, option):
    org_id, _ = _org()
    aelter = _einsatz(org_id)
    neuer = _einsatz(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(Incident, aelter).lis_operation_number = "F26009038"
        db.get(Incident, neuer).lis_operation_number = "f26009039"
        db.commit()
    finally:
        db.close()
    _settings(org_id, mehrere=option)
    mail_id = _mail(org_id, betreff="f26009038 T1 WOLFURT")
    assert await _lauf(mail_id) is True
    assert _zustand(mail_id)[:3] == ("zugeordnet", "auto", aelter)
    assert "per Leitstellennummer f26009038" in dict(_ereignisse(mail_id))["zugeordnet"]


@pytest.mark.asyncio
async def test_leitstellennummer_ohne_treffer_wartet_trotz_anderem_einsatz(setup_db, anhaenge):
    org_id, _ = _org()
    anderer = _einsatz(org_id)
    _settings(org_id, mehrere="neuester")
    mail_id = _mail(org_id, betreff="f26009038 T1 WOLFURT")
    assert await _lauf(mail_id) is False
    assert _zustand(mail_id)[:5] == ("wartend", "wartend", None, None, "Wartet auf Einsatz f26009038")
    assert anderer != _zustand(mail_id)[2]


@pytest.mark.asyncio
async def test_kein_einsatz_wartet_und_uebung_wird_nie_genutzt(setup_db, anhaenge):
    org_id, _ = _org()
    _einsatz(org_id, exercise=True)
    _einsatz(org_id, status="closed")
    _settings(org_id)
    mail_id = _mail(org_id)
    assert await _lauf(mail_id) is False
    assert _zustand(mail_id)[:4] == ("wartend", "wartend", None, None)


def _ereignisse(mail_id):
    from app.models.org_mail import OrgMailEingangEreignis

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(OrgMailEingangEreignis).filter_by(eingang_id=mail_id).order_by(OrgMailEingangEreignis.id)
        return [(e.typ, e.text) for e in rows]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_ereignisse_bei_zuordnung_und_ablehnung(setup_db, anhaenge):
    org_id, _ = _org()
    incident_id = _einsatz(org_id)
    _settings(org_id)
    ok = _mail(org_id)
    assert await _lauf(ok) is True
    typen = dict(_ereignisse(ok))
    assert typen["zugeordnet"].startswith(f"Einsatz #{incident_id} (automatisch), Meldung #")
    fremd = _mail(org_id, absender="Fremder <x@fremd.example>")
    assert await _lauf(fremd) is False
    assert [typ for typ, _ in _ereignisse(fremd)] == ["absender_abgelehnt"]


@pytest.mark.asyncio
async def test_wartend_ereignis_nur_einmal_mit_frist(setup_db, anhaenge):
    from app.models.org_mail import OrgMailEingangEreignis

    org_id, _ = _org()
    _settings(org_id)
    mail_id = _mail(org_id)
    assert await _lauf(mail_id) is False
    # Der Warte-Loop ruft ordne_zu wiederholt auf.
    assert await _lauf(mail_id) is False
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        texte = [e.text for e in db.query(OrgMailEingangEreignis).filter_by(eingang_id=mail_id, typ="wartend")]
    finally:
        db.close()
    assert len(texte) == 1
    assert texte[0].startswith("wartet auf Einsatz bis ")


@pytest.mark.asyncio
async def test_cross_org_einsatz_wird_nie_verwendet(setup_db, anhaenge):
    org_a, _ = _org()
    org_b, _ = _org()
    fremd = _einsatz(org_b)
    _settings(org_a)
    mail_id = _mail(org_a)
    assert await _lauf(mail_id) is False
    assert _zustand(mail_id)[2] is None
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert await zuordnung.uebernehme_in_einsatz(db, mail_id, fremd) is False
    finally:
        db.close()
    assert _zustand(mail_id)[3] is None


@pytest.mark.asyncio
async def test_fehlerhafter_anhang_bricht_nicht_ab(setup_db, anhaenge, monkeypatch):
    org_id, _ = _org()
    _einsatz(org_id)
    _settings(org_id)
    mail_id = _mail(org_id, anhaenge=[("a_png", "bild.png"), ("kaputt", "x.bin")])
    assert await _lauf(mail_id) is True
    status, _, _, msg_id, grund = _zustand(mail_id)
    assert status == "zugeordnet" and msg_id and "x.bin" in grund


def _aktion(client, pfad, **data):
    return client.post(pfad, data={"_csrf": client.cookies.get("ec_csrf"), **data}, follow_redirects=False)


def test_uebernehmen_und_absender_freigeben_routen(client, setup_db, anhaenge):
    org_id, admin = _org()
    inc_id = _einsatz(org_id)
    _settings(org_id, absender="")
    mail_id = _mail(org_id, absender="Neu@Beispiel.at")
    _login(client, admin)
    assert _aktion(client, f"/admin/mail/eingang/{mail_id}/absender-freigeben").status_code in (200, 303)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert "neu@beispiel.at" in db.query(OrgSettings).filter_by(org_id=org_id).one().mail_eingang_absender
    finally:
        db.close()
    assert _aktion(client, f"/admin/mail/eingang/{mail_id}/uebernehmen").status_code == 400
    assert _aktion(client, f"/admin/mail/eingang/{mail_id}/uebernehmen", incident_id=inc_id).status_code == 303
    status, art, incident_id, msg_id, _ = _zustand(mail_id)
    assert (status, art, incident_id) == ("zugeordnet", "manuell", inc_id) and msg_id


def test_routen_rechte_und_cross_org(client, setup_db, anhaenge):
    org_a, admin_a = _org()
    org_b, admin_b = _org()
    inc_b = _einsatz(org_b)
    mail_id = _mail(org_a)
    _, leser = _org("readonly")
    _login(client, leser)
    assert _aktion(client, f"/admin/mail/eingang/{mail_id}/uebernehmen", incident_id=inc_b).status_code in (401, 403)
    client.cookies.clear()
    _login(client, admin_b)
    assert _aktion(client, f"/admin/mail/eingang/{mail_id}/uebernehmen", incident_id=inc_b).status_code == 404
    assert _aktion(client, f"/admin/mail/eingang/{mail_id}/absender-freigeben").status_code == 404
    client.cookies.clear()
    _login(client, admin_a)
    assert _aktion(client, f"/admin/mail/eingang/{mail_id}/uebernehmen", incident_id=inc_b).status_code == 400
    assert _zustand(mail_id)[3] is None
