"""Tests fuer SMS-Einsatzinfo-Dienst und SMS-Gruppen-Modul."""
from __future__ import annotations

import os
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# Test-Umgebungsvariablen vor App-Import setzen
os.environ.setdefault("SECRET_KEY", "test-secret-key-fuer-tests-mindestens-32-zeichen!")
os.environ.setdefault("DEBUG", "true")
os.environ["DATABASE_URL"] = "sqlite:///./test.db"


# ── render_template ────────────────────────────────────────────────────────────

def test_render_template_basic():
    from app.services.sms_dispatch_service import render_template
    result = render_template("Einsatz {stichwort}: {adresse}", {
        "stichwort": "B2",
        "adresse": "Hauptstrasse 1, Wolfurt",
    })
    assert result == "Einsatz B2: Hauptstrasse 1, Wolfurt"


def test_render_template_all_placeholders():
    from app.services.sms_dispatch_service import render_template
    tpl = "{stichwort} {adresse} {ort} {meldung} {einsatzgrund} {datum} {zeit}"
    ctx = {
        "stichwort": "T1",
        "adresse": "Dorfstr. 5",
        "ort": "Wolfurt",
        "meldung": "Person eingeschlossen",
        "einsatzgrund": "Unfall",
        "datum": "27.06.2026",
        "zeit": "14:30",
    }
    result = render_template(tpl, ctx)
    assert "T1" in result
    assert "Dorfstr. 5" in result
    assert "27.06.2026" in result
    assert "14:30" in result


def test_render_template_missing_key_is_empty():
    """Fehlende Platzhalter werden still durch leeren String ersetzt (kein KeyError)."""
    from app.services.sms_dispatch_service import render_template
    result = render_template("Einsatz {stichwort}: {adresse} - {unbekannt}", {
        "stichwort": "F2",
        "adresse": "Testgasse 1",
    })
    assert "F2" in result
    assert "{unbekannt}" not in result  # kein KeyError, leerer String statt Platzhalter
    assert "Testgasse 1" in result


def test_default_template_has_placeholders():
    from app.services.sms_dispatch_service import default_einsatzinfo_template
    tpl = default_einsatzinfo_template()
    assert "{stichwort}" in tpl
    assert "{adresse}" in tpl or "{meldung}" in tpl


# ── collect_einsatzinfo_recipients ─────────────────────────────────────────────

def _make_member(mid, phone="+43660123456", active=True):
    m = MagicMock()
    m.id = mid
    m.phone = phone
    m.active = active
    m.full_name = f"Test {mid}"
    return m


def _make_group(gid, members_phones):
    grp = MagicMock()
    grp.id = gid
    gms = []
    for i, phone in enumerate(members_phones):
        gm = MagicMock()
        gm.member = _make_member(100 + i, phone=phone)
        gms.append(gm)
    grp.members = gms
    return grp


def test_collect_basis_verteiler(monkeypatch):
    """Basis-Verteiler (alarm_type_id=None) gibt korrekte Mitglieder zurueck."""
    from app.services.sms_dispatch_service import collect_einsatzinfo_recipients

    member = _make_member(1, "+4366012345678")
    entry = MagicMock()
    entry.group_id = None
    entry.group = None
    entry.member_id = 1
    entry.member = member

    mock_db = MagicMock()
    mock_q = MagicMock()
    mock_db.query.return_value.filter.return_value = mock_q
    mock_q.all.return_value = [entry]

    result = collect_einsatzinfo_recipients(mock_db, org_id=1, alarm_type_id=None)
    assert len(result) == 1
    norm_phone = "+4366012345678"
    assert norm_phone in result


def test_collect_deduplicates_by_phone(monkeypatch):
    """Gleiche Telefonnummer aus Gruppe und Einzeleintrag wird nur einmal gesendet."""
    from app.services.sms_dispatch_service import collect_einsatzinfo_recipients

    phone = "+4366012345678"
    member1 = _make_member(1, phone)
    member2 = _make_member(2, phone)  # gleiche Nummer

    grp = _make_group(10, [phone])

    entry_group = MagicMock()
    entry_group.group_id = 10
    entry_group.group = grp
    entry_group.member_id = None
    entry_group.member = None

    entry_single = MagicMock()
    entry_single.group_id = None
    entry_single.group = None
    entry_single.member_id = 2
    entry_single.member = member2

    mock_db = MagicMock()
    mock_q = MagicMock()
    mock_db.query.return_value.filter.return_value = mock_q
    mock_q.all.return_value = [entry_group, entry_single]

    result = collect_einsatzinfo_recipients(mock_db, org_id=1, alarm_type_id=None)
    # Trotz zwei Eintraegen: nur eine Nummer nach Dedup
    assert len(result) == 1


def test_collect_skips_inactive():
    """Inaktive Mitglieder werden nicht in die Empfaengerliste aufgenommen."""
    from app.services.sms_dispatch_service import collect_einsatzinfo_recipients

    member = _make_member(1, "+4366012345678", active=False)
    entry = MagicMock()
    entry.group_id = None
    entry.group = None
    entry.member_id = 1
    entry.member = member

    mock_db = MagicMock()
    mock_q = MagicMock()
    mock_db.query.return_value.filter.return_value = mock_q
    mock_q.all.return_value = [entry]

    result = collect_einsatzinfo_recipients(mock_db, org_id=1, alarm_type_id=None)
    assert len(result) == 0


def test_collect_skips_no_phone():
    """Mitglieder ohne Telefonnummer werden uebersprungen."""
    from app.services.sms_dispatch_service import collect_einsatzinfo_recipients

    member = _make_member(1, phone=None)
    entry = MagicMock()
    entry.group_id = None
    entry.group = None
    entry.member_id = 1
    entry.member = member

    mock_db = MagicMock()
    mock_q = MagicMock()
    mock_db.query.return_value.filter.return_value = mock_q
    mock_q.all.return_value = [entry]

    result = collect_einsatzinfo_recipients(mock_db, org_id=1, alarm_type_id=None)
    assert len(result) == 0


def test_collect_group_expansion():
    """Gruppe wird zu Mitgliedern expandiert."""
    from app.services.sms_dispatch_service import collect_einsatzinfo_recipients

    grp = _make_group(10, ["+43111111", "+43222222"])

    entry = MagicMock()
    entry.group_id = 10
    entry.group = grp
    entry.member_id = None
    entry.member = None

    mock_db = MagicMock()
    mock_q = MagicMock()
    mock_db.query.return_value.filter.return_value = mock_q
    mock_q.all.return_value = [entry]

    result = collect_einsatzinfo_recipients(mock_db, org_id=1, alarm_type_id=None)
    assert len(result) == 2


# ── dispatch_einsatzinfo Gating ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_skips_when_disabled():
    """Kein Versand wenn einsatzinfo_sms_enabled == False."""
    from app.services import sms_dispatch_service as svc

    org_settings = MagicMock()
    org_settings.einsatzinfo_sms_enabled = False

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings

    # Gateway verbunden simulieren: patch an der Quelle (app.routers.ws)
    with patch("app.routers.ws.is_sms_gateway_connected", return_value=True), \
         patch("app.services.sms_dispatch_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_dispatch_service.set_tenant_context"), \
         patch("app.services.sms_dispatch_service.send_bulk", new_callable=AsyncMock) as mock_send, \
         patch("app.services.sms_dispatch_service.write_audit"):
        await svc.dispatch_einsatzinfo(
            org_id=1, alarm_type_code="B2",
            address="Teststr. 1", ort="Wolfurt",
            meldung="Test", einsatzgrund=None,
            is_exercise=False,
        )
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_skips_exercise_when_not_configured():
    """Kein Versand bei Uebung wenn einsatzinfo_sms_send_exercise == False."""
    from app.services import sms_dispatch_service as svc

    org_settings = MagicMock()
    org_settings.einsatzinfo_sms_enabled = True
    org_settings.einsatzinfo_sms_send_exercise = False

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings

    with patch("app.routers.ws.is_sms_gateway_connected", return_value=True), \
         patch("app.services.sms_dispatch_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_dispatch_service.set_tenant_context"), \
         patch("app.services.sms_dispatch_service.send_bulk", new_callable=AsyncMock) as mock_send, \
         patch("app.services.sms_dispatch_service.write_audit"):
        await svc.dispatch_einsatzinfo(
            org_id=1, alarm_type_code="T1",
            address="Test", ort="Wolfurt",
            meldung=None, einsatzgrund=None,
            is_exercise=True,
        )
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_skips_no_gateway():
    """Kein Versand wenn kein Gateway verbunden."""
    from app.services import sms_dispatch_service as svc

    # Kein Gateway → fruehzeitiger Ausstieg, SessionLocal wird nicht aufgerufen
    with patch("app.routers.ws.is_sms_gateway_connected", return_value=False), \
         patch("app.services.sms_dispatch_service.send_bulk", new_callable=AsyncMock) as mock_send, \
         patch("app.services.sms_dispatch_service.SessionLocal") as mock_session:
        await svc.dispatch_einsatzinfo(
            org_id=1, alarm_type_code="F1",
            address="Test", ort="Wolfurt",
            meldung=None, einsatzgrund=None,
            is_exercise=False,
        )
        mock_send.assert_not_called()
        mock_session.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_exercise_sends_when_configured():
    """Bei Uebung und send_exercise=True wird Text mit [UEBUNG]-Prafix gesendet."""
    from app.services import sms_dispatch_service as svc

    org_settings = MagicMock()
    org_settings.einsatzinfo_sms_enabled = True
    org_settings.einsatzinfo_sms_send_exercise = True
    org_settings.einsatzinfo_sms_template = None

    alarm_type = MagicMock()
    alarm_type.id = 42
    alarm_type.einsatzinfo_sms_template = None

    member = _make_member(1, "+4366099999")

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.side_effect = [org_settings, alarm_type]
    mock_db.get.return_value = MagicMock(timezone="Europe/Vienna")

    sent_texts: list[str] = []

    async def fake_send_bulk(org_id, jobs):
        for _, text in jobs:
            sent_texts.append(text)
        return len(jobs), len(jobs)

    with patch("app.routers.ws.is_sms_gateway_connected", return_value=True), \
         patch("app.services.sms_dispatch_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_dispatch_service.set_tenant_context"), \
         patch("app.services.sms_dispatch_service.collect_einsatzinfo_recipients",
               return_value={"+4366099999": member}), \
         patch("app.services.sms_dispatch_service.send_bulk", side_effect=fake_send_bulk), \
         patch("app.services.sms_dispatch_service.write_audit"), \
         patch("app.services.sms_dispatch_service.SmsLog"):
        await svc.dispatch_einsatzinfo(
            org_id=1, alarm_type_code="T1",
            address="Hauptstr. 1", ort="Wolfurt",
            meldung="Test", einsatzgrund=None,
            is_exercise=True,
        )

    assert any("[UEBUNG]" in t for t in sent_texts), f"Kein [UEBUNG]-Prafix in: {sent_texts}"


# ── send_bulk ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_bulk_counts_successes():
    """send_bulk zaehlt Erfolge korrekt."""
    from app.services.sms_dispatch_service import send_bulk

    call_results = [True, False, True]
    idx = {"i": 0}

    async def fake_send_sms(org_id, to, text):
        result = call_results[idx["i"]]
        idx["i"] += 1
        return result

    # send_sms ist lazy importiert in send_bulk — Patch an der Quelle
    with patch("app.services.sms_service.send_sms", side_effect=fake_send_sms):
        total, success = await send_bulk(1, [("+43111", "txt"), ("+43222", "txt"), ("+43333", "txt")])

    assert total == 3
    assert success == 2


@pytest.mark.asyncio
async def test_send_bulk_handles_exception():
    """Ausnahmen beim einzelnen Versand blockieren nicht den Rest."""
    from app.services.sms_dispatch_service import send_bulk

    async def boom_sms(org_id, to, text):
        raise RuntimeError("Gateway-Fehler")

    with patch("app.services.sms_service.send_sms", side_effect=boom_sms):
        total, success = await send_bulk(1, [("+43111", "txt")])

    assert total == 1
    assert success == 0
