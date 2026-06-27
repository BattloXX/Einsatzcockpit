"""UAS-Compliance-Logik: Pilot-Freigabe, Geräte-Einsatzbereitschaft, Wartungs-Ampel.

Alle Funktionen sind reine Service-Logik ohne HTTP-Abhängigkeiten.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from sqlalchemy.orm import Session

# ── Datentypen ────────────────────────────────────────────────────────────────

AmpelStatus = Literal["gruen", "gelb", "rot"]

CURRENCY_DAYS = 90          # Währungsfenster RL 4.1: 3 Monate
CURRENCY_MINCOUNT = 3       # Mind. 3 Flugbewegungen
ABLAUF_WARNUNG_TAGE = 30    # Ampel "gelb" ab 30 Tage vor Ablauf


@dataclass
class PilotFreigabe:
    status: AmpelStatus
    fehlende: list[str] = field(default_factory=list)
    naechster_ablauf: date | None = None


@dataclass
class DeviceStatus:
    einsatzbereit: bool
    gruende: list[str] = field(default_factory=list)


@dataclass
class WartungAmpel:
    status: AmpelStatus
    naechste_faellig: date | None = None
    tage_bis_faellig: int | None = None


# ── Pilot-Freigabe ────────────────────────────────────────────────────────────

def pilot_freigabe_status(pilot, db: Session) -> PilotFreigabe:
    """Prüft alle Voraussetzungen eines Piloten (RL 4.1).

    Ampel-Logik:
    - rot: eine oder mehrere Voraussetzungen nicht erfüllt
    - gelb: alles erfüllt, aber ein Ablaufdatum in ≤30 Tagen
    - gruen: alles erfüllt und kein baldiger Ablauf
    """
    from app.models.uas import UASFlugbewegung

    heute = date.today()
    fehlende: list[str] = []
    ablaufdaten: list[date] = []

    # Alter ≥ 18 Jahre (RL 4.1)
    if pilot.geburtsdatum:
        alter = (heute - pilot.geburtsdatum).days // 365
        if alter < 18:
            fehlende.append("Mindestalter 18 Jahre nicht erreicht")
    else:
        fehlende.append("Geburtsdatum fehlt (Altersnachweis erforderlich)")

    # Truppführer (RL 4.1)
    if not pilot.ist_truppfuehrer:
        fehlende.append("Qualifikation Truppführer fehlt")

    # A1/A3-Zertifikat (RL 4.1, Anh. 8.6)
    if not pilot.a1a3_id:
        fehlende.append("A1/A3-Zertifikat fehlt (ID nicht eingetragen)")
    elif pilot.a1a3_gueltig_bis:
        if pilot.a1a3_gueltig_bis < heute:
            fehlende.append(f"A1/A3-Zertifikat abgelaufen ({pilot.a1a3_gueltig_bis})")
        else:
            ablaufdaten.append(pilot.a1a3_gueltig_bis)

    # A2-Zertifikat (RL 4.1, Anh. 8.7)
    if not pilot.a2_id:
        fehlende.append("A2-Zertifikat fehlt (ID nicht eingetragen)")
    elif pilot.a2_gueltig_bis:
        if pilot.a2_gueltig_bis < heute:
            fehlende.append(f"A2-Zertifikat abgelaufen ({pilot.a2_gueltig_bis})")
        else:
            ablaufdaten.append(pilot.a2_gueltig_bis)

    # BOS-Ausbildung Stufe ≥ 1 (RL 4.1, Anh. 8.8)
    if pilot.bos_stufe == "0":
        fehlende.append("BOS-Ausbildung Stufe I fehlt")

    # BOS-Rezertifizierung (alle 5 Jahre, Anh. 8.8)
    if pilot.bos_rezert_bis:
        if pilot.bos_rezert_bis < heute:
            fehlende.append(f"BOS-Rezertifizierung überfällig (fällig war: {pilot.bos_rezert_bis})")
        else:
            ablaufdaten.append(pilot.bos_rezert_bis)

    # LFV-Zulassung (RL 4.1)
    if not pilot.lfv_zugelassen:
        fehlende.append("LFV-Zulassung fehlt")

    # Currency: ≥3 Flugbewegungen in letzten 90 Tagen (RL 4.1)
    fenster_von = heute - timedelta(days=CURRENCY_DAYS)
    count = (
        db.query(UASFlugbewegung)
        .filter(
            UASFlugbewegung.pilot_id == pilot.id,
            UASFlugbewegung.datum >= fenster_von,
        )
        .count()
    )
    if count < CURRENCY_MINCOUNT:
        fehlende.append(
            f"Currency nicht erfüllt ({count}/{CURRENCY_MINCOUNT} Flugbewegungen in 90 Tagen)"
        )

    # Ampel bestimmen
    if fehlende:
        return PilotFreigabe(status="rot", fehlende=fehlende)

    naechster_ablauf = min(ablaufdaten) if ablaufdaten else None
    if naechster_ablauf and (naechster_ablauf - heute).days <= ABLAUF_WARNUNG_TAGE:
        return PilotFreigabe(status="gelb", fehlende=[], naechster_ablauf=naechster_ablauf)

    return PilotFreigabe(status="gruen", fehlende=[], naechster_ablauf=naechster_ablauf)


# ── Geräte-Einsatzbereitschaft ────────────────────────────────────────────────

def device_einsatzbereit(device) -> DeviceStatus:
    """Prüft ob ein Gerät einsatzbereit ist (RL 4.1/4.8, Anh. 8.5)."""
    from app.models.uas import UASDeviceStatus, UASWartungErgebnis

    heute = date.today()
    gruende: list[str] = []

    if device.status != UASDeviceStatus.aktiv.value:
        gruende.append(f"Gerät ist nicht aktiv (Status: {device.status})")
        return DeviceStatus(einsatzbereit=False, gruende=gruende)

    if not device.registriernummer:
        gruende.append("Registriernummer/eID fehlt (RL 4.1)")

    if not device.versicherung_polizze:
        gruende.append("Versicherungsnachweis fehlt (RL 4.8)")
    elif device.versicherung_gueltig_bis and device.versicherung_gueltig_bis < heute:
        gruende.append(f"Versicherung abgelaufen ({device.versicherung_gueltig_bis})")

    # Offene nio-Wartung
    if device.wartungen:
        for w in device.wartungen:
            if w.ergebnis == UASWartungErgebnis.nio.value:
                gruende.append(f"Offener Wartungsmangel vom {w.datum} (nio)")
                break

    return DeviceStatus(einsatzbereit=len(gruende) == 0, gruende=gruende)


# ── Wartungs-Fälligkeit ────────────────────────────────────────────────────────

def wartung_faelligkeit(device) -> WartungAmpel:
    """Berechnet die nächste Wartungsfälligkeit (Anh. 8.5).

    Monatliche Sichtkontrolle: innerhalb der letzten 35 Tage (etwas Puffer)
    Jahresservice: innerhalb des letzten Jahres
    """
    from app.models.uas import UASWartungArt

    heute = date.today()
    letzte_monatlich: date | None = None
    letzte_jaehrlich: date | None = None

    for w in (device.wartungen or []):
        if w.art == UASWartungArt.monatliche_sichtkontrolle.value:
            if letzte_monatlich is None or w.datum > letzte_monatlich:
                letzte_monatlich = w.datum
        if w.art in (
            UASWartungArt.jahresservice.value,
            UASWartungArt.monatliche_sichtkontrolle.value,
        ):
            if letzte_jaehrlich is None or w.datum > letzte_jaehrlich:
                letzte_jaehrlich = w.datum

    # Monatliche Sichtkontrolle
    if letzte_monatlich is None:
        return WartungAmpel(status="rot", tage_bis_faellig=0)
    tage_seit = (heute - letzte_monatlich).days
    if tage_seit > 35:
        return WartungAmpel(status="rot", naechste_faellig=letzte_monatlich + timedelta(days=30),
                            tage_bis_faellig=-(tage_seit - 30))
    naechste = letzte_monatlich + timedelta(days=30)
    tage_bis = (naechste - heute).days
    if tage_bis <= 7:
        return WartungAmpel(status="gelb", naechste_faellig=naechste, tage_bis_faellig=tage_bis)
    return WartungAmpel(status="gruen", naechste_faellig=naechste, tage_bis_faellig=tage_bis)


# ── Dashboard-Übersicht ───────────────────────────────────────────────────────

def compliance_dashboard(org_id: int, db: Session) -> dict:
    """Fasst alle Compliance-Warnungen für das Dashboard zusammen."""
    from app.models.uas import UASDevice, UASDeviceStatus, UASPilot

    heute = date.today()
    grenze_30 = heute + timedelta(days=ABLAUF_WARNUNG_TAGE)

    # Ablaufende Zertifikate
    piloten = db.query(UASPilot).filter(
        UASPilot.org_id == org_id, UASPilot.aktiv == True  # noqa: E712
    ).all()
    ablaufende_zerts: list[dict] = []
    nicht_current: list[dict] = []
    for p in piloten:
        freigabe = pilot_freigabe_status(p, db)
        if freigabe.status == "rot":
            nicht_current.append({"pilot": p, "freigabe": freigabe})
        elif freigabe.status == "gelb":
            ablaufende_zerts.append({"pilot": p, "ablauf": freigabe.naechster_ablauf})

    # Fällige/überfällige Wartungen
    devices = db.query(UASDevice).filter(
        UASDevice.org_id == org_id,
        UASDevice.status == UASDeviceStatus.aktiv.value,
    ).all()
    wartungen_ampel: list[dict] = []
    versicherung_ablauf: list[dict] = []
    for d in devices:
        ampel = wartung_faelligkeit(d)
        if ampel.status in ("rot", "gelb"):
            wartungen_ampel.append({"device": d, "ampel": ampel})
        if d.versicherung_gueltig_bis and d.versicherung_gueltig_bis <= grenze_30:
            versicherung_ablauf.append({
                "device": d,
                "gueltig_bis": d.versicherung_gueltig_bis,
                "ueberfaellig": d.versicherung_gueltig_bis < heute,
            })

    return {
        "ablaufende_zerts": ablaufende_zerts,
        "nicht_current_piloten": nicht_current,
        "wartungen_ampel": wartungen_ampel,
        "versicherung_ablauf": versicherung_ablauf,
    }
