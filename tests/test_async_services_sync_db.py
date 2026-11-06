"""Waechter fuer SessionLocal in async-Services.

Baseline nach einem Umbau regenerieren (nur entfernte Eintraege uebernehmen):
    .venv/bin/python -c 'import runpy; m = runpy.run_path("tests/test_async_services_sync_db.py"); \
    m["BASELINE_PATH"].write_text("".join(x + "\\n" for x in m["_eintraege"]()))'
"""
from __future__ import annotations

import ast
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
BASELINE_PATH = WURZEL / "tests/data/async_services_sync_db_baseline.txt"


class _SessionBesucher(ast.NodeVisitor):
    def __init__(self) -> None:
        self.funktion: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        self.treffer: set[str] = set()

    def _funktion(self, knoten: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Defaults und Dekoratoren werden im aeusseren Kontext ausgewertet.
        for dekorator in knoten.decorator_list:
            self.visit(dekorator)
        self.visit(knoten.args)
        vorher = self.funktion
        self.funktion = knoten
        for anweisung in knoten.body:
            self.visit(anweisung)
        self.funktion = vorher

    def visit_FunctionDef(self, knoten: ast.FunctionDef) -> None:
        self._funktion(knoten)

    def visit_AsyncFunctionDef(self, knoten: ast.AsyncFunctionDef) -> None:
        self._funktion(knoten)

    def visit_Call(self, knoten: ast.Call) -> None:
        name = getattr(knoten.func, "id", None) or getattr(knoten.func, "attr", None)
        if name == "SessionLocal" and isinstance(self.funktion, ast.AsyncFunctionDef):
            self.treffer.add(self.funktion.name)
        self.generic_visit(knoten)


def _eintraege() -> list[str]:
    treffer: set[str] = set()
    for datei in sorted((WURZEL / "app/services").rglob("*.py")):
        besucher = _SessionBesucher()
        besucher.visit(ast.parse(datei.read_text(encoding="utf-8")))
        treffer.update(f"{datei.relative_to(WURZEL)}:{name}" for name in besucher.treffer)
    return sorted(treffer)


def _baseline() -> set[str]:
    return {zeile for zeile in BASELINE_PATH.read_text(encoding="utf-8").splitlines()
            if zeile and not zeile.startswith("#")}


def test_keine_neuen_async_services_mit_sync_db() -> None:
    neue = set(_eintraege()) - _baseline()
    assert not neue, (
        f"Neue async-Services mit synchroner DB: {sorted(neue)}. "
        "DB-Arbeit in def _..._sync mit eigener Session auslagern und mit "
        "await asyncio.to_thread(_..._sync, ...) aufrufen; nur einfache Werte "
        "uebergeben, Async-I/O auf dem Event-Loop lassen."
    )


def test_baseline_enthaelt_nur_vorhandene_eintraege() -> None:
    veraltet = _baseline() - set(_eintraege())
    assert not veraltet, f"Entfernte async+DB-Stellen aus der Baseline streichen: {sorted(veraltet)}"


def test_verschachtelte_funktionskontexte() -> None:
    besucher = _SessionBesucher()
    besucher.visit(ast.parse('''
async def aussen():
    SessionLocal()
    modul.SessionLocal()
    def synchron():
        SessionLocal()
        async def innen():
            modul.SessionLocal()
    async def verschachtelt():
        SessionLocal()
    def nur_thread():
        def weiter():
            SessionLocal()
async def nur_threadpool():
    def laden():
        SessionLocal()
    await asyncio.to_thread(laden)
'''))
    assert besucher.treffer == {"aussen", "innen", "verschachtelt"}
