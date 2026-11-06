"""Schlichtes Logging und MCP-Import in einem frischen Prozess absichern."""

import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.logging_setup import einrichten, rich_handler_entfernen


@pytest.fixture
def root_logger():
    root = logging.getLogger()
    handler, level = root.handlers[:], root.level
    root.handlers = []
    try:
        yield root
    finally:
        for neu in root.handlers:
            if neu not in handler:
                neu.close()
        root.handlers = handler
        root.setLevel(level)


def test_einrichten_idempotent_mit_standard_traceback(root_logger, capsys):
    # pytest haengt beim Wechsel von Setup zu Call eigene Capture-Handler an.
    root_logger.handlers.clear()
    puffer = logging.NullHandler()
    root_logger.addHandler(puffer)
    einrichten()
    einrichten()
    streams = [h for h in root_logger.handlers if isinstance(h, logging.StreamHandler)]
    assert len(streams) == 1
    assert type(streams[0]) is logging.StreamHandler
    assert streams[0].stream is sys.stderr
    assert puffer in root_logger.handlers
    assert root_logger.level == logging.INFO
    try:
        raise ValueError("Probe")
    except ValueError:
        logging.getLogger("probe").exception("Fehler")
    ausgabe = capsys.readouterr().err
    assert ausgabe.startswith("ERROR probe: Fehler\nTraceback (most recent call last):\n")
    assert ausgabe.endswith("ValueError: Probe\n")


def test_vorhandenen_stream_handler_beibehalten(root_logger):
    root_logger.handlers.clear()
    handler = logging.StreamHandler()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)
    einrichten()
    assert root_logger.handlers == [handler]
    assert root_logger.level == logging.DEBUG


def test_rich_handler_entfernen_idempotent(root_logger):
    root_logger.handlers.clear()
    # Ein Testdouble mit derselben Klassenherkunft vermeidet Rich-Rendering.
    rich_handler_klasse = type("RichHandler", (logging.Handler,), {"__module__": "rich.logging"})
    puffer = logging.NullHandler()
    root_logger.addHandler(puffer)
    root_logger.addHandler(rich_handler_klasse())
    rich_handler_entfernen()
    rich_handler_entfernen()
    assert len(root_logger.handlers) == 2
    assert puffer in root_logger.handlers
    assert sum(type(h) is logging.StreamHandler for h in root_logger.handlers) == 1


def test_main_import_mit_plain_logging(tmp_path):
    skript = textwrap.dedent('''
        import logging
        from app import log_buffer
        log_buffer.setup()
        import app.main
        from rich.logging import RichHandler
        root = logging.getLogger()
        streams = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        assert len(streams) == 1
        assert type(streams[0]) is logging.StreamHandler
        assert not any(isinstance(h, RichHandler) for h in root.handlers)
        assert log_buffer._handler in root.handlers
        for name in ("uvicorn.error", "gunicorn.error"):
            logging.getLogger(name).error("Sichtbare Meldung")
        assert len(log_buffer.get_entries()) >= 2
    ''')
    ergebnis = subprocess.run(
        [sys.executable, "-c", skript], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'import.db'}"},
        capture_output=True, text=True, timeout=60,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "ERROR uvicorn.error: Sichtbare Meldung" in ergebnis.stderr
    assert "ERROR gunicorn.error: Sichtbare Meldung" in ergebnis.stderr
