"""FOR-UPDATE-Inventur: Baseline darf nur schrumpfen, niemals pauschal wachsen."""

import ast
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTUR = ROOT / "tests/data/for_update_inventur.txt"


def _count(source: str) -> int:
    tree = ast.parse(source)
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
        and (node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id) == "with_for_update"
        for node in ast.walk(tree)
    ) + sum(
        len(re.findall(r"\bFOR\s+UPDATE\b", node.value, re.IGNORECASE))
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _occurrences() -> dict[str, int]:
    counts = {str(path.relative_to(ROOT)): _count(path.read_text(encoding="utf-8"))
              for path in sorted((ROOT / "app").rglob("*.py"))}
    return {path: count for path, count in counts.items() if count}


def _inventory() -> dict[str, int]:
    entries = {}
    for line in INVENTUR.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        path, count, reason = line.split(" | ", 2)
        assert path not in entries, f"Doppelter Inventureintrag: {path}"
        assert reason.strip() and int(count) > 0, f"Begruendung/Anzahl fehlt: {line}"
        entries[path] = int(count)
    return entries


def test_no_new_for_update():
    added = Counter(_occurrences()) - Counter(_inventory())
    assert not added, (
        f"Neue/geaenderte FOR-UPDATE-Vorkommen: {dict(added)}. "
        "MariaDB-Konflikttest ergaenzen und Retry-Entscheidung dokumentieren. "
        "tests/data/for_update_inventur.txt darf als Baseline nur schrumpfen."
    )


def test_inventory_contains_only_existing_for_update():
    obsolete = Counter(_inventory()) - Counter(_occurrences())
    assert not obsolete, f"Veraltete FOR-UPDATE-Inventur reduzieren/entfernen: {dict(obsolete)}"


def test_counter_detects_calls_and_sql_without_comments():
    assert _count('''
# FOR UPDATE / .with_for_update()
a = query.with_for_update()
b = text("select id from job FOR\\nUPDATE")
c = select(Job).with_for_update(skip_locked=True)
''') == 3
