"""Förderstrecken-Planer — Modul-Service + Hydraulik-Engine.

Zwei Teile:
1. Feature-Flag-Logik (zweistufig, Muster UAS) und Kennlinien-Validierung.
2. Hydraulik-Engine als **reine Funktionen** (keine DB-/HTTP-Abhängigkeit, unit-testbar):
   Reibungsverlust, Höhen-/Drucklinie mit Hochpunkt-Prüfung, Saugseite, Kennlinien-
   Interpolation, Stationsbilanz und Modus A (max. Fördermenge Q per Bisektion).

Physik-Grundlagen (Feuerwehr-Fachliteratur):
- Reibung quadratisch: Δp = k · (Q_leitung/1000)² · (L/100), Q_leitung = Q/n_parallel.
  k kalibriert: B-75 = 1,56 → 1,0 bar/100 m @ 800 l/min; Doppel-B ≈ 0,25.
- Höhe: 10 m = 1,0 bar.
- Saughöhe: barometrisch (Seehöhen-Korrektur) − geodätische Höhe − Reibung − NPSHr.

Effektive Aktivierung: System-Flag (SystemSettings key "foerderstrecke_module_enabled"
== "true") UND Org-Flag (OrgSettings.foerderstrecke_module_enabled == True).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

SYSTEM_FLAG_KEY = "foerderstrecke_module_enabled"


def foerderstrecke_system_enabled(db: Session) -> bool:
    """Systemweiter Förderstrecke-Flag aus SystemSettings. Fehlender Key → False."""
    from app.models.master import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == SYSTEM_FLAG_KEY).first()
    return row is not None and row.value == "true"


def foerderstrecke_effective_enabled(org_id: int | None, db: Session) -> bool:
    """Förderstrecke effektiv aktiv ⟺ System-Flag AN und Org-Flag AN.

    Gibt False wenn org_id None (system_admin ohne Impersonation).
    """
    if org_id is None:
        return False
    if not foerderstrecke_system_enabled(db):
        return False
    from app.models.master import OrgSettings
    org_s = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return bool(org_s and org_s.foerderstrecke_module_enabled)


# ── Kennlinien-Validierung ───────────────────────────────────────────────────────

def normalisiere_kennlinie_punkte(
    q_werte: list[str] | list[float], h_werte: list[str] | list[float]
) -> tuple[list[list[float]], list[str]]:
    """Baut aus parallelen Q-/H-Eingabelisten eine bereinigte Punktliste [[Q, H], …].

    - Leere Zeilen (beide Felder leer) werden übersprungen.
    - Q ≥ 0 und H ≥ 0 erzwungen (sonst Fehlermeldung).
    - Nach Q aufsteigend sortiert; H sollte dabei monoton fallen (Pumpenkennlinie) —
      Verstöße werden als Warnung/Fehler gemeldet, aber nicht automatisch korrigiert.

    Rückgabe: (punkte, fehler). Bei nicht-leerem `fehler` ist die Kennlinie ungültig.
    """
    fehler: list[str] = []
    punkte: list[list[float]] = []
    for roh_q, roh_h in zip(q_werte, h_werte):
        s_q = str(roh_q).strip().replace(",", ".")
        s_h = str(roh_h).strip().replace(",", ".")
        if not s_q and not s_h:
            continue
        try:
            q = float(s_q)
            h = float(s_h)
        except ValueError:
            fehler.append(f"Ungültiger Kennlinienpunkt: Q='{roh_q}', H='{roh_h}'")
            continue
        if q < 0 or h < 0:
            fehler.append(f"Q und H müssen ≥ 0 sein (Q={q}, H={h})")
            continue
        punkte.append([q, h])

    punkte.sort(key=lambda p: p[0])
    # Doppelte Q-Werte sind mehrdeutig für die Interpolation
    q_seen: set[float] = set()
    for q, _h in punkte:
        if q in q_seen:
            fehler.append(f"Doppelter Q-Wert {q} — je Q nur ein Punkt")
        q_seen.add(q)
    # Monotonie-Prüfung (H fallend mit steigendem Q)
    for (q1, h1), (q2, h2) in zip(punkte, punkte[1:]):
        if h2 > h1:
            fehler.append(
                f"Kennlinie nicht monoton fallend: bei Q={q2} steigt H auf {h2} "
                f"(vorher {h1} bei Q={q1})"
            )
            break
    return punkte, fehler


# ══════════════════════════════════════════════════════════════════════════════
# Hydraulik-Engine (reine Funktionen — keine DB/HTTP, unit-testbar)
# ══════════════════════════════════════════════════════════════════════════════

# Physik-/Planungs-Konstanten (Defaults; in der Strecke konfigurierbar)
P_LUFT_MEERESHOEHE_HPA = 1013.25
METER_PRO_BAR = 10.0                 # 10 m Höhendifferenz = 1,0 bar
BAROMETRISCHE_SAUGHOEHE_M = 10.3     # theoretische Saughöhe auf Meereshöhe
SEGMENT_M = 25.0                     # Auflösung der Drucklinie
HOCHPUNKT_MIN_BAR = 0.5             # unter diesem Druck droht Strömungsabriss
PUFFER_MIN_EINLAUF_BAR = 0.2        # freier Einlauf in Faltbehälter/Fahrzeugtank
DEFAULT_MIN_EINGANGSDRUCK_BAR = 1.5
DBV_ZUSCHLAG_BAR = 0.5              # DBV = Eingangsdruck Folgepumpe + 0,5 bar


# ── Primitive ─────────────────────────────────────────────────────────────────

def reibungsverlust_bar(
    k: float, q_l_min: float, laenge_m: float,
    n_parallel: int = 1, armaturen_zuschlag: float = 0.0,
) -> float:
    """Reibungsdruckverlust einer (ggf. parallelen) Leitung in bar.

    Δp = k · (Q_leitung/1000)² · (L/100), Q_leitung = Q_gesamt/n_parallel.
    `armaturen_zuschlag` (z. B. 0.05 = +5 %) deckt Kupplungen/Sammelstücke ab.
    Referenz: B-75 (k=1,56), 800 l/min, 100 m, n=1 → ≈ 1,0 bar; Doppel-B ≈ 0,25.
    """
    n = max(1, int(n_parallel))
    q_leitung = q_l_min / n
    delta = k * (q_leitung / 1000.0) ** 2 * (laenge_m / 100.0)
    return delta * (1.0 + armaturen_zuschlag)


def hoehenverlust_bar(delta_hoehe_m: float) -> float:
    """Druckänderung durch Höhendifferenz (+ bergauf = Verlust). 10 m = 1 bar."""
    return delta_hoehe_m / METER_PRO_BAR


def luftdruck_hpa(seehoehe_m: float) -> float:
    """Barometrischer Luftdruck auf Seehöhe (internationale Höhenformel)."""
    return P_LUFT_MEERESHOEHE_HPA * (1.0 - seehoehe_m / 44330.0) ** 5.255


def barometrische_saughoehe_m(seehoehe_m: float) -> float:
    """Theoretisch verfügbare Saughöhe auf Seehöhe (Seehöhen-Korrektur).

    ≈ −0,5 m in Wolfurt (430 m), ≈ −1 m je 900 m.
    """
    return BAROMETRISCHE_SAUGHOEHE_M * (luftdruck_hpa(seehoehe_m) / P_LUFT_MEERESHOEHE_HPA)


def interpoliere_hoehe(kennlinie: list[list[float]], q_l_min: float) -> float | None:
    """Förderhöhe H [m] bei Q [l/min] durch lineare Interpolation der Kennlinie.

    Außerhalb des Stützstellenbereichs wird auf den Randwert geklemmt. Leere
    Kennlinie → None.
    """
    if not kennlinie:
        return None
    pkt = sorted(kennlinie, key=lambda p: p[0])
    if q_l_min <= pkt[0][0]:
        return float(pkt[0][1])
    if q_l_min >= pkt[-1][0]:
        return float(pkt[-1][1])
    for (q1, h1), (q2, h2) in zip(pkt, pkt[1:]):
        if q1 <= q_l_min <= q2:
            if q2 == q1:
                return float(h1)
            t = (q_l_min - q1) / (q2 - q1)
            return float(h1 + t * (h2 - h1))
    return float(pkt[-1][1])


def skaliere_kennlinie(kennlinie: list[list[float]], n_ist: float, n_ziel: float) -> list[list[float]]:
    """Skaliert eine Kennlinie per Affinitätsgesetz auf eine andere Drehzahl.

    Q ∝ n, H ∝ n². Für n_ziel==n_ist unverändert.
    """
    if n_ist <= 0:
        return [list(p) for p in kennlinie]
    fq = n_ziel / n_ist
    fh = fq * fq
    return [[q * fq, h * fh] for q, h in kennlinie]


def kennlinie_max_q(kennlinie: list[list[float]]) -> float:
    """Größte Fördermenge der Kennlinie (obere Grenze der Pumpe)."""
    return max((p[0] for p in kennlinie), default=0.0)


def verfuegbare_saughoehe_m(
    seehoehe_m: float, geodaetische_saughoehe_m: float, saug_k: float,
    q_l_min: float, saugleitung_laenge_m: float = 0.0, saug_n_parallel: int = 1,
    npshr_m: float = 0.0,
) -> float:
    """Saughöhen-Reserve in Metern (positiv = machbar).

    = barometrische Saughöhe − geodätische Höhe − Reibung(Saugleitung) − NPSHr.
    Die Reibung wird über METER_PRO_BAR von bar in Meter umgerechnet.
    """
    reib_bar = reibungsverlust_bar(saug_k, q_l_min, saugleitung_laenge_m, saug_n_parallel)
    reib_m = reib_bar * METER_PRO_BAR
    return (
        barometrische_saughoehe_m(seehoehe_m)
        - geodaetische_saughoehe_m
        - reib_m
        - npshr_m
    )


def behaelter_standzeit_min(volumen_l: float, q_zulauf_l_min: float, q_ablauf_l_min: float) -> float | None:
    """Standzeit eines Puffers bei Q-Ungleichgewicht (Minuten).

    Leert sich (Ablauf > Zulauf) → positive Restzeit; füllt/ausgeglichen → None
    (unbegrenzt bzw. Überlauf getrennt zu prüfen).
    """
    delta = q_ablauf_l_min - q_zulauf_l_min
    if delta <= 0 or volumen_l <= 0:
        return None
    return volumen_l / delta


# ── Strecken-Datenstrukturen ──────────────────────────────────────────────────

@dataclass
class Ansaugpunkt:
    seehoehe_m: float = 430.0
    geodaetische_saughoehe_m: float = 3.0     # Pumpe über Wasserspiegel
    saug_k: float = 0.23                       # A-110 Default
    saug_n_parallel: int = 1
    saugleitung_laenge_m: float = 0.0
    max_ansaughoehe_m: float = 7.5
    npshr_m: float = 0.0


@dataclass
class Abschnitt:
    """Leitungsabschnitt hinter einer Station (bis zur nächsten Station/Auslass)."""
    schlauch_k: float
    laenge_m: float
    n_parallel: int = 1
    delta_hoehe_m: float = 0.0                 # Zielhöhe − Starthöhe (+ bergauf)
    max_betriebsdruck_bar: float | None = None
    # Optionale Geländehöhen (relativ zur Starthöhe) je SEGMENT_M — für Hochpunkte.
    hoehen_stuetzpunkte: list[float] | None = None


@dataclass
class PumpenStation:
    kennlinie: list[list[float]]               # [[Q,H],…] der gewählten Drehzahlstufe
    typ: str = "verstaerker"                   # quellpumpe|verstaerker|puffer|uebergabe
    max_ausgangsdruck_bar: float | None = None
    min_eingangsdruck_bar: float = DEFAULT_MIN_EINGANGSDRUCK_BAR
    behaelter_volumen_l: float | None = None   # nur puffer/uebergabe
    name: str = ""
    abschnitt_danach: Abschnitt | None = None


def _pumpe_p_aus(station: PumpenStation, q_l_min: float) -> float:
    """Ausgangsdruck einer Station bei Q: min(H(Q)/10, max_ausgangsdruck)."""
    h = interpoliere_hoehe(station.kennlinie, q_l_min) or 0.0
    p = h / METER_PRO_BAR
    if station.max_ausgangsdruck_bar is not None:
        p = min(p, station.max_ausgangsdruck_bar)
    return p


def _abschnitt_profil(
    p_start_bar: float, abschnitt: Abschnitt, q_l_min: float,
    s0_m: float, armaturen_zuschlag: float,
) -> list[tuple[float, float]]:
    """Drucklinie p(s) über einen Abschnitt in SEGMENT_M-Schritten.

    Rückgabe: Liste (s_absolut_m, p_bar) inkl. Endpunkt. Berücksichtigt Reibung
    (gleichmäßig verteilt) und Höhe (aus hoehen_stuetzpunkte oder linear).
    """
    laenge = max(abschnitt.laenge_m, 0.0)
    reib_gesamt = reibungsverlust_bar(
        abschnitt.schlauch_k, q_l_min, laenge, abschnitt.n_parallel, armaturen_zuschlag)

    n_seg = max(1, int(round(laenge / SEGMENT_M))) if laenge > 0 else 0
    profil: list[tuple[float, float]] = [(s0_m, p_start_bar)]
    if n_seg == 0:
        return profil

    def hoehe_bei(frac: float) -> float:
        stz = abschnitt.hoehen_stuetzpunkte
        if stz:
            idx = min(len(stz) - 1, int(round(frac * (len(stz) - 1))))
            return stz[idx]
        return abschnitt.delta_hoehe_m * frac

    for i in range(1, n_seg + 1):
        frac = i / n_seg
        reib_bis = reib_gesamt * frac
        hoehe_bis = hoehe_bei(frac)
        p = p_start_bar - reib_bis - hoehenverlust_bar(hoehe_bis)
        profil.append((s0_m + frac * laenge, p))
    return profil


@dataclass
class _Auswertung:
    machbar: bool
    druckprofil: list[tuple[float, float]]
    stationswerte: list[dict]
    warnungen: list[str]
    p_auslass_bar: float


def _auswertung_bei_q(
    ansaug: Ansaugpunkt, stationen: list[PumpenStation], q_l_min: float,
    *, ziel_druck_bar: float, armaturen_zuschlag: float, hochpunkt_min_bar: float,
) -> _Auswertung:
    """Berechnet Drucklinie, Stationssollwerte und Machbarkeit bei gegebenem Q."""
    warnungen: list[str] = []
    machbar = True
    druckprofil: list[tuple[float, float]] = []
    stationswerte: list[dict] = []

    # Saugseite der Quellpumpe
    reserve = verfuegbare_saughoehe_m(
        ansaug.seehoehe_m, ansaug.geodaetische_saughoehe_m, ansaug.saug_k, q_l_min,
        ansaug.saugleitung_laenge_m, ansaug.saug_n_parallel, ansaug.npshr_m)
    if ansaug.geodaetische_saughoehe_m > ansaug.max_ansaughoehe_m:
        machbar = False
        warnungen.append(
            f"Geodätische Saughöhe {ansaug.geodaetische_saughoehe_m:.1f} m über "
            f"Grenze {ansaug.max_ansaughoehe_m:.1f} m.")
    if reserve < 0:
        machbar = False
        warnungen.append(f"Saughöhen-Bilanz negativ (Reserve {reserve:.1f} m) bei {q_l_min:.0f} l/min.")

    s = 0.0
    p_ein_aktuell: float | None = None   # Eingangsdruck der aktuellen Station (aus Vorstrecke)
    for i, station in enumerate(stationen):
        p_aus = _pumpe_p_aus(station, q_l_min)
        werte = {
            "index": i, "name": station.name, "typ": station.typ,
            "p_aus_bar": round(p_aus, 2),
            "p_ein_bar": round(p_ein_aktuell, 2) if p_ein_aktuell is not None else None,
            "dbv_bar": None,
        }
        stationswerte.append(werte)
        if not druckprofil:
            druckprofil.append((s, p_aus))

        abschnitt = station.abschnitt_danach
        if abschnitt is None:
            continue
        teil = _abschnitt_profil(p_aus, abschnitt, q_l_min, s, armaturen_zuschlag)
        # Ersten Punkt (Duplikat der Station) nur beim allerersten Abschnitt behalten
        druckprofil.extend(teil[1:])
        s = teil[-1][0]
        p_ende = teil[-1][1]

        # Grenzen entlang des Abschnitts prüfen
        for s_i, p_i in teil:
            if abschnitt.max_betriebsdruck_bar is not None and p_i > abschnitt.max_betriebsdruck_bar:
                machbar = False
                warnungen.append(
                    f"Betriebsdruck {p_i:.1f} bar > Grenze "
                    f"{abschnitt.max_betriebsdruck_bar:.1f} bar bei km {s_i/1000:.2f}.")
                break
        # Hochpunkt-/Abriss-Prüfung an Zwischenpunkten
        for s_i, p_i in teil[1:-1]:
            if p_i < hochpunkt_min_bar:
                machbar = False
                warnungen.append(
                    f"Hochpunkt bei km {s_i/1000:.2f}: Druck {p_i:.2f} bar < "
                    f"{hochpunkt_min_bar:.1f} bar (Abrissgefahr).")
                break

        # Eingangsdruck der Folgestation / Auslass
        ist_letzte = (i == len(stationen) - 1)
        if ist_letzte:
            if p_ende < ziel_druck_bar:
                machbar = False
                warnungen.append(
                    f"Auslassdruck {p_ende:.2f} bar < Ziel {ziel_druck_bar:.1f} bar.")
        else:
            folge = stationen[i + 1]
            min_ein = (PUFFER_MIN_EINLAUF_BAR
                       if folge.typ in ("puffer", "uebergabe")
                       else folge.min_eingangsdruck_bar)
            werte["p_ein_folge_bar"] = round(p_ende, 2)
            werte["dbv_bar"] = round(p_ende + DBV_ZUSCHLAG_BAR, 2)
            p_ein_aktuell = p_ende   # wird beim nächsten Schleifendurchlauf als p_ein gesetzt
            if p_ende < min_ein:
                machbar = False
                warnungen.append(
                    f"Eingangsdruck Station {i+2} ({p_ende:.2f} bar) < "
                    f"Mindestwert {min_ein:.1f} bar.")

    p_auslass = druckprofil[-1][1] if druckprofil else 0.0
    return _Auswertung(machbar, druckprofil, stationswerte, warnungen, p_auslass)


def berechne_modus_a(
    ansaug: Ansaugpunkt, stationen: list[PumpenStation], *,
    ziel_druck_bar: float = 0.0, armaturen_zuschlag: float = 0.05,
    hochpunkt_min_bar: float = HOCHPUNKT_MIN_BAR, q_toleranz: float = 10.0,
) -> dict:
    """Modus A: maximale Fördermenge Q der Kette per Bisektion.

    Sucht das größte Q in [0, Q_engpass], bei dem alle Stationsbilanzen, die
    Saugseite und die Drucklinie eingehalten werden. Q_engpass = kleinste
    Kennlinien-Obergrenze aller Pumpen (schwächste Pumpe = Engpass).

    Rückgabe-Dict: q_max_l_min, machbar, druckprofil, stationswerte, warnungen,
    engpass (Name/Index der schwächsten Pumpe).
    """
    if not stationen:
        return {"q_max_l_min": 0.0, "machbar": False, "druckprofil": [],
                "stationswerte": [], "warnungen": ["Keine Pumpenstation definiert."],
                "engpass": None}

    q_grenzen = [(kennlinie_max_q(st.kennlinie), i, st) for i, st in enumerate(stationen)]
    q_engpass, engpass_idx, engpass_st = min(q_grenzen, key=lambda t: t[0])

    def machbar_bei(q: float) -> _Auswertung:
        return _auswertung_bei_q(
            ansaug, stationen, q, ziel_druck_bar=ziel_druck_bar,
            armaturen_zuschlag=armaturen_zuschlag, hochpunkt_min_bar=hochpunkt_min_bar)

    # Selbst bei kleinem Q nicht machbar? → Strecke unmöglich.
    probe = machbar_bei(min(q_toleranz, q_engpass))
    if not probe.machbar:
        voll = machbar_bei(0.0)
        return {"q_max_l_min": 0.0, "machbar": False, "druckprofil": voll.druckprofil,
                "stationswerte": voll.stationswerte, "warnungen": probe.warnungen,
                "engpass": {"index": engpass_idx, "name": engpass_st.name,
                            "q_max_l_min": q_engpass}}

    # Bisektion: lo machbar, hi (noch) nicht bekannt
    lo, hi = 0.0, q_engpass
    if machbar_bei(q_engpass).machbar:
        lo = q_engpass
    else:
        iterationen = 0
        while hi - lo > q_toleranz and iterationen < 40:
            mid = (lo + hi) / 2.0
            if machbar_bei(mid).machbar:
                lo = mid
            else:
                hi = mid
            iterationen += 1

    ausw = machbar_bei(lo)
    warnungen = list(ausw.warnungen)
    if lo >= q_engpass:
        warnungen.append(
            f"Engpass: {engpass_st.name or ('Station ' + str(engpass_idx + 1))} "
            f"begrenzt auf {q_engpass:.0f} l/min (schwächste Pumpe).")
    return {
        "q_max_l_min": round(lo, 1),
        "machbar": lo > 0,
        "druckprofil": [(round(s, 1), round(p, 3)) for s, p in ausw.druckprofil],
        "stationswerte": ausw.stationswerte,
        "warnungen": warnungen,
        "engpass": {"index": engpass_idx, "name": engpass_st.name, "q_max_l_min": q_engpass},
    }
