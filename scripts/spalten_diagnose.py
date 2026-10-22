"""Nur lesend: findet Karten ohne gueltige Spalte und zeigt Spalten-Umbenennungen (ECP-02).

Aufruf:
    .venv/bin/python scripts/spalten_diagnose.py [incident_id]

Es wird nichts veraendert. Karten (Fahrzeuge, Auftraege, Meldungen) mit column_id NULL
oder mit Spalte eines anderen Einsatzes werden mit dem letzten bekannten Spaltenwechsel
(IncidentChange) ausgegeben, damit die Zuordnung manuell wiederhergestellt werden kann.
"""
from __future__ import annotations

import sys

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentChange, IncidentColumn, IncidentVehicle, Message, Task


def main(incident_id: int | None) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        q = db.query(Incident)
        if incident_id:
            q = q.filter(Incident.id == incident_id)
        gefunden = 0
        for inc in q.all():
            spalten = {c.id: c for c in db.query(IncidentColumn).filter_by(incident_id=inc.id)}
            umbenannt = (
                db.query(IncidentChange)
                .filter(IncidentChange.incident_id == inc.id, IncidentChange.action == "column.title_set")
                .count()
            )
            for modell, name in ((IncidentVehicle, "Fahrzeug"), (Task, "Auftrag"), (Message, "Meldung")):
                for k in db.query(modell).filter(modell.incident_id == inc.id):
                    if modell is IncidentVehicle and k.removed_at is not None:
                        continue
                    if k.column_id is None or k.column_id not in spalten:
                        gefunden += 1
                        sys.stdout.write(f"Einsatz {inc.id}: {name} {k.id} ohne gueltige Spalte "
                                         f"(column_id={k.column_id}), Umbenennungen im Einsatz: {umbenannt}\n")
        sys.stdout.write(f"{gefunden} verwaiste Karten gefunden\n")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else None))
