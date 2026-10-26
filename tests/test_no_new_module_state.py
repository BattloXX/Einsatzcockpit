"""Wächter gegen neuen, versehentlich pro Worker gehaltenen Modulzustand."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST_PATH = Path(__file__).parent / "data" / "module_state_allowlist.txt"
CONSTRUCTORS = frozenset({
    "dict", "set", "list", "defaultdict", "OrderedDict", "deque", "WeakKeyDictionary", "WeakSet",
})
MUTABLE_LITERALS = (ast.Dict, ast.Set, ast.List, ast.DictComp, ast.SetComp, ast.ListComp)


def _module_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Auch if/try/with auf Modulebene prüfen, Funktions-/Klassenkörper auslassen."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _module_nodes(child)


def _non_empty_literal(value: ast.AST) -> bool:
    if isinstance(value, ast.Dict):
        return bool(value.keys)
    if isinstance(value, (ast.Set, ast.List)):
        return bool(value.elts)
    return isinstance(value, (ast.DictComp, ast.SetComp, ast.ListComp))


def _mutable_assignments(tree: ast.Module) -> set[str]:
    nodes = list(_module_nodes(tree))
    constructors = set(CONSTRUCTORS)
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            constructors.update(alias.asname or alias.name for alias in node.names if alias.name in CONSTRUCTORS)
    names = set()
    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        # Nicht-leere Literale sind statische Tabellen; Zustand beginnt leer.
        mutable = isinstance(value, MUTABLE_LITERALS) and not _non_empty_literal(value)
        if isinstance(value, ast.Call):
            if isinstance(value.func, ast.Name):
                mutable = value.func.id in constructors
            elif isinstance(value.func, ast.Attribute):
                mutable = value.func.attr in CONSTRUCTORS
        if mutable:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name) and target.id != "__all__")
    return names


def _module_state_entries() -> set[str]:
    entries = set()
    for directory in (ROOT / "app" / "routers", ROOT / "app" / "services"):
        for path in sorted(directory.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            entries.update(f"{module}:{name}" for name in _mutable_assignments(tree))
    return entries


def _allowlist_entries() -> set[str]:
    return {
        line.strip()
        for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_no_new_module_state() -> None:
    new_entries = _module_state_entries() - _allowlist_entries()
    assert not new_entries, (
        f"Neue prozesslokale Modulzustände: {sorted(new_entries)}. "
        "Workerübergreifender Zustand gehört in DB/Redis. "
        "Caches müssen bewusst in tests/data/module_state_allowlist.txt eingetragen werden."
    )


def test_allowlist_contains_only_existing_module_state() -> None:
    obsolete_entries = _allowlist_entries() - _module_state_entries()
    assert not obsolete_entries, (
        f"Veraltete Allowlist-Einträge: {sorted(obsolete_entries)}. "
        "Nicht mehr vorhandene Modulzustände aus tests/data/module_state_allowlist.txt entfernen."
    )
