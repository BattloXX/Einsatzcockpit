"""Persistenz-Helfer für den Förderstrecken-Planer (PR 5).

Reine DB-nahe Funktionen (org-gescopt) rund um Speichern, Versionieren und die
Integration in bestehende Bausteine (Relais-Standort → Wasserstelle Typ `relais`).
Die Karten-/Wizard-Routen (PR 4) orchestrieren diese Helfer.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.foerderstrecke import (
    STRECKE_STATUS_ARCHIVIERT,
    STRECKE_STATUS_ENTWURF,
    STRECKE_STATUS_FREIGEGEBEN,
    FoerderErgebnis,
    FoerderStation,
    Foerderstrecke,
)
from app.models.wasserstelle import Wasserstelle

# Erlaubte Status-Übergänge (einfacher Lebenszyklus)
_STATUS_UEBERGAENGE = {
    STRECKE_STATUS_ENTWURF: {STRECKE_STATUS_FREIGEGEBEN, STRECKE_STATUS_ARCHIVIERT},
    STRECKE_STATUS_FREIGEGEBEN: {STRECKE_STATUS_ARCHIVIERT, STRECKE_STATUS_ENTWURF},
    STRECKE_STATUS_ARCHIVIERT: {STRECKE_STATUS_ENTWURF},
}


def status_wechsel_erlaubt(von: str, nach: str) -> bool:
    return nach in _STATUS_UEBERGAENGE.get(von, set())


def setze_status(strecke: Foerderstrecke, nach: str) -> bool:
    """Setzt den Status, wenn der Übergang erlaubt ist. Rückgabe: erfolgt?"""
    if strecke.status == nach:
        return True
    if not status_wechsel_erlaubt(strecke.status, nach):
        return False
    strecke.status = nach
    return True


def ergebnis_anhaengen(
    db: Session, strecke: Foerderstrecke, ergebnis: dict, *, modus: str = "A",
) -> FoerderErgebnis:
    """Hängt ein Berechnungsergebnis versioniert an die Strecke an (nie überschreiben).

    `ergebnis` ist das Dict der Engine (q_max_l_min, stationswerte, warnungen …).
    Vergleich „geplant vs. neu gerechnet" bleibt über die Historie möglich.
    """
    row = FoerderErgebnis(
        org_id=strecke.org_id,
        strecke_id=strecke.id,
        berechnet_am=datetime.now(UTC),
        q_max_l_min=ergebnis.get("q_max_l_min"),
        modus=modus,
        stationswerte_json=json.dumps(ergebnis.get("stationswerte") or []),
        material_json=json.dumps(ergebnis.get("material") or {}),
        warnungen_json=json.dumps(ergebnis.get("warnungen") or []),
    )
    db.add(row)
    return row


def relais_als_wasserstelle(
    db: Session, station: FoerderStation, org_id: int, user_id: int | None = None,
    *, bezeichnung: str | None = None,
) -> Wasserstelle | None:
    """Persistiert einen (verschobenen) Relais-Standort als Wasserstelle Typ `relais`.

    Idempotent über `station.wasserstelle_id`: existiert bereits eine verknüpfte
    Wasserstelle, wird deren Koordinate aktualisiert statt eine zweite anzulegen.
    Gibt None zurück, wenn die Station keine Koordinaten hat.
    """
    if station.lat is None or station.lng is None:
        return None
    name = bezeichnung or f"Relais {station.typ_label}".strip()

    if station.wasserstelle_id:
        vorhanden = db.get(Wasserstelle, station.wasserstelle_id)
        if vorhanden is not None and vorhanden.org_id == org_id:
            vorhanden.lat = station.lat
            vorhanden.lng = station.lng
            vorhanden.aktualisiert_von_id = user_id
            return vorhanden

    ws = Wasserstelle(
        org_id=org_id,
        bezeichnung=name[:250],
        typ="relais",
        lat=station.lat,
        lng=station.lng,
        quelle="manuell",
        status="bereit",
        aktiv=True,
        erstellt_von_id=user_id,
        aktualisiert_von_id=user_id,
    )
    db.add(ws)
    db.flush()
    station.wasserstelle_id = ws.id
    return ws
