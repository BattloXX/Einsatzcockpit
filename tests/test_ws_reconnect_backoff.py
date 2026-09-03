"""Statische Regressionstests fuer Reconnect-Backoff und Ping-Aufraeumen."""
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relpath", ["app/static/js/app.js", "app/static/js/lage_board.js"])
def test_erster_reconnect_startet_ohne_festen_backoff(relpath):
    source = (ROOT / relpath).read_text()
    match = re.search(
        r"const backoff = reconnectAttempt === 0\s*\? 0\s*:\s*"
        r"Math\.min\(1000 \* 2 \*\* \(reconnectAttempt - 1\), 15000\);\s*reconnectAttempt\+\+;",
        source,
    )
    assert match, "Die Backoff-Folge muss mit 0 ms beginnen und erst danach hochzaehlen"


@pytest.mark.parametrize("relpath", ["app/static/js/app.js", "app/static/js/lage_board.js"])
def test_onclose_raeumt_ping_timer_auf(relpath):
    source = (ROOT / relpath).read_text()
    close_handler = re.search(r"(?:onclose =|addEventListener\('close',)[\s\S]{0,300}clearInterval\(pingInterval\)", source)
    assert close_handler
