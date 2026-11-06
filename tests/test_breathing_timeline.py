"""Fristen fuer den aktuellen Einsatzzyklus, ohne Datenbankzugriff."""
from datetime import UTC, datetime, timedelta

import pytest

from app.models.breathing import BreathingKontrolle, BreathingTroop, TroopMember
from app.services.breathing_service import troop_timeline


def _troop(minutes=11):
    return BreathingTroop(
        status="im_einsatz", entry_at=datetime.now(UTC) - timedelta(minutes=minutes),
        planned_duration_min=30,
    )


def test_ready_has_no_timeline():
    assert troop_timeline(BreathingTroop(status="bereit")) == []


@pytest.mark.parametrize("minutes,expected", [(5, "offen"), (11, "faellig"), (31, "faellig")])
def test_deadlines(minutes, expected):
    troop = _troop(minutes)
    events = {e["label"]: e for e in troop_timeline(troop)}
    assert events["Eintritt"]["ist_at"] == troop.entry_at
    assert events["1/3"]["status"] == expected
    assert events["1/3"]["soll_at"] == troop.entry_at + timedelta(minutes=10)
    assert events["2/3"]["status"] == ("faellig" if minutes >= 20 else "offen")
    assert events["Max"]["soll_at"] == troop.entry_at + timedelta(minutes=30)
    assert events["Max"]["status"] == ("faellig" if minutes >= 30 else "offen")


def test_completed_control_ignores_radio_loss_and_archived_cycle():
    troop = _troop()
    done = troop.entry_at + timedelta(minutes=10)
    troop.kontrollen = [
        BreathingKontrolle(stufe="one_third", art="funk", erledigt_at=done, niedrigster_druck=230),
        BreathingKontrolle(stufe="one_third", art="kein_kontakt", erledigt_at=done + timedelta(seconds=1)),
        BreathingKontrolle(stufe="two_third", art="funk", erledigt_at=done, niedrigster_druck=150, deployment_id=1),
    ]
    events = {e["label"]: e for e in troop_timeline(troop)}
    assert events["1/3"]["status"] == "erledigt"
    assert events["1/3"]["ist_at"] == done
    assert events["1/3"]["detail"] == "230 bar"
    assert events["2/3"]["status"] == "offen"
    troop.kontrollen = troop.kontrollen[1:]
    assert troop_timeline(troop)[1]["status"] == "faellig"


@pytest.mark.parametrize("status", ["rueckzug", "zurueck", "erholt"])
def test_objective_withdrawal_and_return(status):
    troop = _troop(40)
    troop.status = status
    troop.withdraw_at = troop.entry_at + timedelta(minutes=15)
    member = TroopMember(free_text_name="Person A", objective_press=210,
                         objective_press_at=troop.entry_at + timedelta(minutes=12))
    troop.members = [member]
    if status != "rueckzug":
        troop.back_at = troop.entry_at + timedelta(minutes=20)
    events = {e["label"]: e for e in troop_timeline(troop)}
    assert events["Einsatzziel"]["detail"] == "Person A (210 bar)"
    assert events["Einsatzziel"]["ist_at"] == member.objective_press_at
    assert events["Rückzug"]["ist_at"] == troop.withdraw_at
    assert ("Zurück" in events) is (status != "rueckzug")
    if troop.back_at:
        assert events["Zurück"]["ist_at"] == troop.back_at
        assert events["Max"]["status"] == "erledigt"
