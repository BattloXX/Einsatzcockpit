"""SEC-03: fehlgeschlagene Geraetezugriffe aus dem Access-Log auswerten (nur lesend).

Aufruf (Beispiel):
    journalctl -u einsatzleiter --since "2026-10-01" | .venv/bin/python scripts/geraete_zugriffe_auswerten.py
    .venv/bin/python scripts/geraete_zugriffe_auswerten.py access.log

Zuordnung zu Geraeten nur, wenn die IP in frueheren erfolgreichen Anmeldungen genau einem Geraet gehoerte.
Alles andere wird als "nicht zuordenbar" ausgegeben.
"""
from __future__ import annotations

import sys

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.services import geraete_zugriff_analyse as analyse


def main(argv: list[str]) -> int:
    quelle = open(argv[1], encoding="utf-8", errors="replace") if len(argv) > 1 else sys.stdin  # noqa: SIM115
    gruppen = analyse.gruppiere(analyse.parse_zeilen(quelle))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zeilen = analyse.zuordnen(gruppen, analyse.geraete_je_ip(db))
    finally:
        db.close()
    out = sys.stdout.write
    out(f"{len(gruppen)} Quellen mit fehlgeschlagenen Geraetezugriffen\n")
    for z in zeilen:
        g = z["gruppe"]
        out(f"{g.anzahl:6d}x  {g.ip:<39}  {z['geraet'] or 'nicht zuordenbar':<24}  "
            f"{g.erster:%Y-%m-%d %H:%M} bis {g.letzter:%Y-%m-%d %H:%M}  UA={g.user_agent or '-'}\n"
            f"        {', '.join(sorted(g.pfade))}  ({z['grund']})\n" if g.erster and g.letzter else
            f"{g.anzahl:6d}x  {g.ip}  {z['geraet'] or 'nicht zuordenbar'}  ({z['grund']})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
