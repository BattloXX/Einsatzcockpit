"""Tests fuer SMS-Empfang: Log (SmsInbox) und Weiterleitungsregeln (SmsForwardRule)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Test-Umgebungsvariablen vor App-Import setzen
os.environ.setdefault("SECRET_KEY", "test-secret-key-fuer-tests-mindestens-32-zeichen!")
os.environ.setdefault("DEBUG", "true")
os.environ["DATABASE_URL"] = "sqlite:///./test.db"


def _make_member(mid, phone="+43660123456", active=True):
    m = MagicMock()
    m.id = mid
    m.phone = phone
    m.active = active
    m.full_name = f"Test {mid}"
    return m


def _make_rule_group(phones):
    grp = MagicMock()
    gms = []
    for i, phone in enumerate(phones):
        gm = MagicMock()
        gm.member = _make_member(200 + i, phone=phone)
        gms.append(gm)
    grp.members = gms
    rg = MagicMock()
    rg.group = grp
    rg.group_id = 10
    return rg


def _make_rule(
    match_type="exact", match_number="+43664111", forward_teams=False,
    teams_webhook_url=None, forward_adhoc_numbers=None, prepend_sender=True,
    groups=None, members=None, enabled=True, rule_id=1, name="Regel",
):
    rule = MagicMock()
    rule.id = rule_id
    rule.name = name
    rule.enabled = enabled
    rule.match_type = match_type
    rule.match_number = match_number
    rule.forward_teams = forward_teams
    rule.teams_webhook_url = teams_webhook_url
    rule.forward_adhoc_numbers = forward_adhoc_numbers
    rule.prepend_sender = prepend_sender
    rule.groups = groups or []
    rule.members = members or []
    return rule


# ── _normalize_phone / _rule_matches ──────────────────────────────────────────

def test_normalize_phone_strips_formatting():
    from app.services.sms_inbox_service import _normalize_phone
    assert _normalize_phone(" +43 (664) 111-222 ") == "+43664111222"


def test_rule_matches_exact():
    from app.services.sms_inbox_service import _rule_matches
    rule = _make_rule(match_type="exact", match_number="+43 664 111")
    assert _rule_matches(rule, "+43664111") is True
    assert _rule_matches(rule, "+43664112") is False


def test_rule_matches_prefix():
    from app.services.sms_inbox_service import _rule_matches
    rule = _make_rule(match_type="prefix", match_number="+4366")
    assert _rule_matches(rule, "+43664111222") is True
    assert _rule_matches(rule, "+43111") is False


def test_rule_matches_empty_pattern_never_matches():
    from app.services.sms_inbox_service import _rule_matches
    rule = _make_rule(match_number="   ")
    assert _rule_matches(rule, "+43664111") is False


# ── record_inbound_sms ─────────────────────────────────────────────────────────

def test_record_inbound_sms_creates_log_entry():
    from app.models.sms import SmsInbox
    from app.services import sms_inbox_service as svc

    added = {}
    mock_db = MagicMock()

    def fake_add(obj):
        added["entry"] = obj

    def fake_refresh(obj):
        obj.id = 42

    mock_db.add.side_effect = fake_add
    mock_db.refresh.side_effect = fake_refresh

    with patch("app.services.sms_inbox_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_inbox_service.set_tenant_context"):
        inbox_id = svc.record_inbound_sms(
            org_id=7, gateway_token_id=3, from_number=" +43664111 ", text="Hallo",
        )

    assert inbox_id == 42
    entry = added["entry"]
    assert isinstance(entry, SmsInbox)
    assert entry.org_id == 7
    assert entry.from_number == "+43664111"
    assert entry.text == "Hallo"
    assert entry.gateway_token_id == 3
    mock_db.commit.assert_called_once()


# ── process_inbound_sms ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_inbound_sms_disabled_org_only_logs():
    """Empfang org-weit deaktiviert -> nur geloggt, keine Regel ausgewertet."""
    from app.models.sms import SmsInbox
    from app.services import sms_inbox_service as svc

    entry = SmsInbox(org_id=1, from_number="+43664111", text="hi")
    entry.id = 5

    org_settings = MagicMock()
    org_settings.sms_receive_enabled = False

    mock_db = MagicMock()
    mock_db.get.return_value = entry
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings

    with patch("app.services.sms_inbox_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_inbox_service.set_tenant_context"):
        await svc.process_inbound_sms(5)

    assert entry.processed is True
    assert "deaktiviert" in entry.forward_summary
    assert entry.matched_rule_id is None
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_process_inbound_sms_no_rule_match():
    """Empfang aktiv, aber keine Regel matcht die Absendernummer."""
    from app.models.sms import SmsInbox
    from app.services import sms_inbox_service as svc

    entry = SmsInbox(org_id=1, from_number="+43699000000", text="hi")
    entry.id = 6

    org_settings = MagicMock()
    org_settings.sms_receive_enabled = True

    other_rule = _make_rule(match_type="exact", match_number="+43664111")

    mock_db = MagicMock()
    mock_db.get.return_value = entry
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [other_rule]

    with patch("app.services.sms_inbox_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_inbox_service.set_tenant_context"):
        await svc.process_inbound_sms(6)

    assert entry.processed is True
    assert entry.forward_summary == "Keine Regel getroffen"
    assert entry.matched_rule_id is None


@pytest.mark.asyncio
async def test_process_inbound_sms_forwards_teams_and_sms():
    """Regel mit Teams-Webhook (Org-Default) + Mitglied + Ad-hoc-Nummern (dedupliziert)."""
    from app.models.sms import SmsInbox
    from app.services import sms_inbox_service as svc

    entry = SmsInbox(org_id=1, from_number="+43664111", text="Alarm!")
    entry.id = 9

    org_settings = MagicMock()
    org_settings.sms_receive_enabled = True
    org_settings.sms_receive_teams_webhook_url = "https://org-default"

    member = _make_member(1, "+43660123456")
    rule_member = MagicMock()
    rule_member.member = member

    rule = _make_rule(
        match_type="exact", match_number="+43664111",
        forward_teams=True, teams_webhook_url=None,
        members=[rule_member], forward_adhoc_numbers="+43111,+43222,+43111",
        rule_id=7, name="Alarmzentrale",
    )

    mock_db = MagicMock()
    mock_db.get.return_value = entry
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [rule]

    sent_jobs = []

    async def fake_send_bulk(org_id, jobs):
        sent_jobs.extend(jobs)
        return len(jobs), len(jobs)

    with patch("app.services.sms_inbox_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_inbox_service.set_tenant_context"), \
         patch("app.services.sms_dispatch_service.send_bulk", side_effect=fake_send_bulk), \
         patch("app.services.teams_service.post_teams_karte",
               new_callable=AsyncMock, return_value=True) as mock_teams, \
         patch("app.services.sms_inbox_service.write_audit"):
        await svc.process_inbound_sms(9)

    # Teams: kein eigener Webhook an der Regel -> Org-Default verwendet
    mock_teams.assert_called_once()
    assert mock_teams.call_args[0][0] == "https://org-default"

    # SMS: 1 Mitglied + 2 eindeutige Ad-hoc-Nummern (Duplikat entfernt) = 3 Ziele
    assert len(sent_jobs) == 3
    assert entry.matched_rule_id == 7
    assert "Teams ✓" in entry.forward_summary
    assert "3/3 SMS" in entry.forward_summary


@pytest.mark.asyncio
async def test_process_inbound_sms_teams_without_webhook_configured():
    """Regel will an Teams weiterleiten, aber weder Regel noch Org haben einen Webhook."""
    from app.models.sms import SmsInbox
    from app.services import sms_inbox_service as svc

    entry = SmsInbox(org_id=1, from_number="+43664111", text="Alarm!")
    entry.id = 11

    org_settings = MagicMock()
    org_settings.sms_receive_enabled = True
    org_settings.sms_receive_teams_webhook_url = None

    rule = _make_rule(
        match_type="exact", match_number="+43664111",
        forward_teams=True, teams_webhook_url=None,
        rule_id=8,
    )

    mock_db = MagicMock()
    mock_db.get.return_value = entry
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [rule]

    with patch("app.services.sms_inbox_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_inbox_service.set_tenant_context"), \
         patch("app.services.teams_service.post_teams_karte",
               new_callable=AsyncMock) as mock_teams, \
         patch("app.services.sms_inbox_service.write_audit"):
        await svc.process_inbound_sms(11)

    mock_teams.assert_not_called()
    assert "kein Webhook" in entry.forward_summary


@pytest.mark.asyncio
async def test_process_inbound_sms_group_expansion_dedup():
    """Mitglied aus Gruppe UND Ad-hoc mit gleicher Nummer wird nur einmal angeschrieben."""
    from app.models.sms import SmsInbox
    from app.services import sms_inbox_service as svc

    entry = SmsInbox(org_id=1, from_number="+43664111", text="Alarm!")
    entry.id = 12

    org_settings = MagicMock()
    org_settings.sms_receive_enabled = True
    org_settings.sms_receive_teams_webhook_url = None

    same_phone = "+43660999999"
    rule_group = _make_rule_group([same_phone])

    rule = _make_rule(
        match_type="exact", match_number="+43664111",
        forward_teams=False, groups=[rule_group],
        forward_adhoc_numbers=same_phone,
        rule_id=9,
    )

    mock_db = MagicMock()
    mock_db.get.return_value = entry
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [rule]

    sent_jobs = []

    async def fake_send_bulk(org_id, jobs):
        sent_jobs.extend(jobs)
        return len(jobs), len(jobs)

    with patch("app.services.sms_inbox_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_inbox_service.set_tenant_context"), \
         patch("app.services.sms_dispatch_service.send_bulk", side_effect=fake_send_bulk), \
         patch("app.services.sms_inbox_service.write_audit"):
        await svc.process_inbound_sms(12)

    assert len(sent_jobs) == 1
    assert "1/1 SMS" in entry.forward_summary
