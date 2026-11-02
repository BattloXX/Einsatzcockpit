"""App-Imports duerfen auch in einem frischen, isolierten Prozess kein SQL ausfuehren."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.mark.parametrize("isoliert", [False, True], ids=["normal", "isoliert"])
def test_import_ohne_db(isoliert, tmp_path):
    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()  # Test-Konfiguration aus conftest, keine Produktiv-DB.
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'import.db'}"
    skript = textwrap.dedent('''
        import importlib
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path.cwd()))
        from sqlalchemy import event
        from app.config import settings
        from app.db import engine

        assert settings.TESTING
        assert engine.url.get_backend_name() == "sqlite"
        queries = []
        def zaehle(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)
        event.listen(engine, "before_cursor_execute", zaehle)
        for name in (
            "app.services.suche.indexer",
            "app.services.rettungskarten_katalog_service",
            "app.services.gefahrgut_service",
            "app.main",
        ):
            importlib.import_module(name)
        assert not queries, f"SQL beim Import: {queries}"
    ''')
    ergebnis = subprocess.run(
        [sys.executable, *(["-I"] if isoliert else []), "-c", skript],
        cwd=repo, env=env, capture_output=True, text=True, timeout=60,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
