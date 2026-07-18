"""Kalibrierung der Schlauch-Verlustbeiwerte aus Übungsmessungen (PR 7).

Aus `FoerderMessung`-Werten wird je Schlauchtyp per Least-Squares ein korrigierter
k-Wert geschätzt und als `FoerderKalibrierVorschlag` in eine Review-Queue gestellt.
**Nie Auto-Apply** — der Sachbearbeiter übernimmt/verwirft (Muster ObjektSeiteKiVorschlag).

Physik: Δp_reibung = k · (Q/n/1000)² · (L/100). Aus einer Messung folgt
  y (gemessene Reibung) = (p_aus − p_ein_folge) − Δh/10
  x (k-Multiplikator)   = (Q/n/1000)² · (L/100)
Least-Squares durch den Ursprung: k = Σ(x·y) / Σ(x²).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.foerderstrecke import (
    KALIBRIER_OFFEN,
    KALIBRIER_UEBERNOMMEN,
    KALIBRIER_VERWORFEN,
    FoerderKalibrierVorschlag,
    FoerderMessung,
    FoerderSchlauchTyp,
)

# Mindest-Relativabweichung, ab der ein Vorschlag erzeugt wird (5 %)
MIN_ABWEICHUNG_REL = 0.05


def _probe_xy(m: FoerderMessung) -> tuple[float, float] | None:
    """Berechnet (x, y) einer Messung; None wenn Daten unvollständig/degeneriert."""
    if (m.q_gemessen_l_min is None or m.laenge_m is None
            or m.p_aus_bar is None or m.p_ein_folge_bar is None):
        return None
    n = max(1, int(m.n_parallel or 1))
    if m.q_gemessen_l_min <= 0 or m.laenge_m <= 0:
        return None
    x = (m.q_gemessen_l_min / n / 1000.0) ** 2 * (m.laenge_m / 100.0)
    y = (m.p_aus_bar - m.p_ein_folge_bar) - (m.delta_hoehe_m or 0.0) / 10.0
    if x <= 0:
        return None
    return x, y


def fit_k(messungen: list[FoerderMessung]) -> tuple[float, int] | None:
    """Least-Squares-Schätzung des k-Werts durch den Ursprung.

    Rückgabe: (k, n_verwertbare_messungen) oder None, wenn keine verwertbaren
    Messungen vorliegen oder das Ergebnis nicht plausibel (> 0) ist.
    """
    sxx = 0.0
    sxy = 0.0
    n = 0
    for m in messungen:
        xy = _probe_xy(m)
        if xy is None:
            continue
        x, y = xy
        sxx += x * x
        sxy += x * y
        n += 1
    if n == 0 or sxx <= 0:
        return None
    k = sxy / sxx
    if k <= 0:
        return None
    return k, n


def erzeuge_vorschlaege(db: Session, org_id: int) -> list[FoerderKalibrierVorschlag]:
    """Erzeugt/aktualisiert offene Kalibrier-Vorschläge je Schlauchtyp der Org.

    Bestehende offene Vorschläge werden vorher entfernt (frische Queue), analog
    objekt_ki_service. Ergebnisse werden nur vorgeschlagen, nie angewandt.
    """
    messungen = (
        db.query(FoerderMessung)
        .filter(FoerderMessung.org_id == org_id, FoerderMessung.schlauch_typ_id.isnot(None))
        .all()
    )
    nach_typ: dict[int, list[FoerderMessung]] = {}
    for m in messungen:
        nach_typ.setdefault(m.schlauch_typ_id, []).append(m)

    # Frische Queue: bestehende offene Vorschläge dieser Org entfernen
    for alt in (db.query(FoerderKalibrierVorschlag)
                .filter(FoerderKalibrierVorschlag.org_id == org_id,
                        FoerderKalibrierVorschlag.status == KALIBRIER_OFFEN).all()):
        db.delete(alt)

    neue: list[FoerderKalibrierVorschlag] = []
    for schlauch_id, msgs in nach_typ.items():
        ergebnis = fit_k(msgs)
        if ergebnis is None:
            continue
        k_neu, n = ergebnis
        schlauch = db.get(FoerderSchlauchTyp, schlauch_id)
        if schlauch is None or schlauch.org_id != org_id:
            continue
        k_alt = schlauch.k_verlust
        if k_alt and abs(k_neu - k_alt) / k_alt < MIN_ABWEICHUNG_REL:
            continue  # zu geringe Abweichung → kein Vorschlag
        v = FoerderKalibrierVorschlag(
            org_id=org_id,
            schlauch_typ_id=schlauch_id,
            k_alt=k_alt,
            k_neu=round(k_neu, 4),
            n_messungen=n,
            begruendung=(f"Aus {n} Messung(en) geschätzter k-Wert {k_neu:.3f} "
                         f"(bisher {k_alt:.3f})."),
            status=KALIBRIER_OFFEN,
        )
        db.add(v)
        neue.append(v)
    return neue


def vorschlag_uebernehmen(
    db: Session, vorschlag: FoerderKalibrierVorschlag, user_id: int | None,
) -> bool:
    """Übernimmt einen Vorschlag: setzt den k-Wert des Schlauchtyps. Nur wenn offen."""
    if vorschlag.status != KALIBRIER_OFFEN:
        return False
    schlauch = db.get(FoerderSchlauchTyp, vorschlag.schlauch_typ_id)
    if schlauch is None or schlauch.org_id != vorschlag.org_id:
        return False
    schlauch.k_verlust = vorschlag.k_neu
    schlauch.aktualisiert_von_id = user_id
    vorschlag.status = KALIBRIER_UEBERNOMMEN
    vorschlag.entschieden_von_id = user_id
    vorschlag.entschieden_am = datetime.now(UTC)
    return True


def vorschlag_verwerfen(
    db: Session, vorschlag: FoerderKalibrierVorschlag, user_id: int | None,
) -> bool:
    """Verwirft einen Vorschlag (kein Eingriff in den Schlauchtyp)."""
    if vorschlag.status != KALIBRIER_OFFEN:
        return False
    vorschlag.status = KALIBRIER_VERWORFEN
    vorschlag.entschieden_von_id = user_id
    vorschlag.entschieden_am = datetime.now(UTC)
    return True
