"""Tests für bma_client.py::parse_cookie_paste() — baut aus dem im Admin-UI
eingefügten Text (settings_bma_import.html) einen fertigen `Cookie:`-Header-
String. Reine Parsing-Funktion, kein Netzwerk-/DB-Zugriff nötig."""
from app.services.bma_import.bma_client import parse_cookie_paste


def test_parses_devtools_table_paste():
    """Chrome-DevTools Anwendung/Application -> Cookies: Tab-getrennte Spalten,
    nur Name (Spalte 1) und Value (Spalte 2) werden übernommen."""
    raw = (
        "21de5dbe426da45f214b68e2b60599f1\t72552727d85292892d527c12c735e7fa\t"
        "dibos.lwz-vorarlberg.at\t/\tSession\t64\t✓\t✓\tNone\t\t\tMedium\n"
        "TS018670a0\tabc123def456\t.dibos.lwz-vorarlberg.at\t/\tSession\t212\t✓\t\t\t\t\tMedium"
    )
    result = parse_cookie_paste(raw)
    assert result == (
        "21de5dbe426da45f214b68e2b60599f1=72552727d85292892d527c12c735e7fa; "
        "TS018670a0=abc123def456"
    )


def test_skips_devtools_header_row():
    raw = (
        "Name\tValue\tDomain\tPath\tExpires / Max-Age\tSize\tHttpOnly\tSecure\tSameSite\n"
        "sid\twert1\tdibos.lwz-vorarlberg.at\t/\tSession\t10\t✓\t✓\tNone"
    )
    result = parse_cookie_paste(raw)
    assert result == "sid=wert1"


def test_preserves_equals_sign_in_value():
    """Base64-Padding ('=') in einem Token-Wert darf nicht abgeschnitten werden —
    str.partition("=") statt split("=") trennt nur am ERSTEN Gleichheitszeichen."""
    raw = "PORTALTOKEN\tabc123deF+G==\tdibos.lwz-vorarlberg.at\t/\tSession\t100"
    result = parse_cookie_paste(raw)
    assert result == "PORTALTOKEN=abc123deF+G=="


def test_parses_classic_semicolon_format_unchanged():
    """Klassischer Cookie-Header von Hand bleibt weiterhin gültig (Rückwärtskompatibilität)."""
    assert parse_cookie_paste("sid=super-secret-cookie") == "sid=super-secret-cookie"
    assert parse_cookie_paste("name1=wert1; name2=wert2") == "name1=wert1; name2=wert2"


def test_mixed_table_and_classic_lines():
    raw = "a\tb\tdomain\t/\tSession\n" "c=d; e=f"
    result = parse_cookie_paste(raw)
    assert result == "a=b; c=d; e=f"


def test_ignores_blank_lines_and_whitespace():
    raw = "\n  \nsid\twert1\tdomain\t/\tSession\n\n"
    assert parse_cookie_paste(raw) == "sid=wert1"


def test_empty_input_returns_empty_string():
    assert parse_cookie_paste("") == ""
    assert parse_cookie_paste("   \n  \n") == ""
