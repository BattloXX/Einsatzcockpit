"""Suchlaufzeiten ohne Suchtexte messen: python scripts/suche_messung.py --help."""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections import defaultdict
from contextlib import ExitStack
from functools import wraps
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

# Direktaufruf aus dem Repository, ohne Installation als Python-Modul.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.tenant import set_tenant_context  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.suche import abfrage  # noqa: E402
from app.services.suche.vertrag import SuchAnfrage  # noqa: E402


def quantil(werte: list[float], anteil: float) -> float:
    return sorted(werte)[max(0, math.ceil(len(werte) * anteil) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Suchstufen messen; Ausgabe enthaelt keine Suchtexte.")
    parser.add_argument("--org", type=int, required=True)
    parser.add_argument("--user", type=int, required=True, help="Benutzer-ID innerhalb der Organisation")
    parser.add_argument("--anfragen", type=Path, required=True, help="UTF-8-Datei, eine Anfrage pro Zeile")
    parser.add_argument("--wiederholungen", type=int, default=5)
    args = parser.parse_args()
    if args.wiederholungen < 1:
        parser.error("--wiederholungen muss mindestens 1 sein")
    anfragen = [zeile.strip() for zeile in args.anfragen.read_text(encoding="utf-8").splitlines() if zeile.strip()]
    if not anfragen or any(len(q) > 500 for q in anfragen):
        parser.error("Die Datei muss Anfragen mit jeweils 1 bis 500 Zeichen enthalten")
    # Auch SQL-/Exception-Logger duerfen keine Parameter ausgeben.
    logging.disable(logging.CRITICAL)
    messungen: dict[str, list[float]] = defaultdict(list)
    lauf: dict[str, float] = defaultdict(float)

    def messe(name, funktion):
        @wraps(funktion)
        def aufruf(*arg, **kw):
            start = perf_counter()
            try:
                return funktion(*arg, **kw)
            finally:
                lauf[name] += (perf_counter() - start) * 1000
        return aufruf

    with SessionLocal() as db, ExitStack() as stack:
        set_tenant_context(db, None)
        db.get_bind().echo = False
        user = db.scalar(select(User).where(User.id == args.user, User.org_id == args.org, User.active.is_(True)))
        if user is None:
            parser.error("Kein aktiver Benutzer mit dieser ID in der Organisation")
        for attribut, label in (("_exakt", "exakt"), ("volltext_kandidaten", "volltext_kandidaten"),
                                ("_index_treffer", "index_resolver"), ("fusioniere", "fusion")):
            stack.enter_context(patch.object(abfrage, attribut, messe(label, getattr(abfrage, attribut))))
        stack.enter_context(patch.dict(abfrage.live.ANBIETER, {
            name: messe("live_" + name, fn) for name, fn in abfrage.live.ANBIETER.items()
        }))
        for nr, q in enumerate(anfragen, 1):
            zahlen = []
            teilweise = 0
            for _ in range(args.wiederholungen):
                lauf.clear()
                start = perf_counter()
                try:
                    ergebnis = abfrage.suche(db, user, SuchAnfrage(q=q))
                except Exception:
                    raise SystemExit(f"Anfrage {nr}: Suchlauf fehlgeschlagen (Details unterdrueckt)") from None
                lauf["gesamt"] = (perf_counter() - start) * 1000
                zahlen.append(len(ergebnis.treffer))
                teilweise += int(ergebnis.teilweise)
                for stufe, dauer in lauf.items():
                    messungen[stufe].append(dauer)
            print(f"Anfrage {nr}: Treffer min={min(zahlen)} max={max(zahlen)}, teilweise={teilweise}")
    print("Stufe;Laeufe;p50_ms;p95_ms;max_ms")
    for stufe, werte in sorted(messungen.items()):
        print(f"{stufe};{len(werte)};{quantil(werte, .5):.2f};{quantil(werte, .95):.2f};{max(werte):.2f}")


if __name__ == "__main__":
    main()
