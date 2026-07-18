"""PR 2: Hydraulik-Engine — reine Funktionen gegen Literatur-Referenzfälle.

Referenzen (Feuerwehr-Fachliteratur):
- B-75 (k=1,56) @ 800 l/min, 100 m → ≈ 1,0 bar; Doppel-B ≈ 0,25 bar.
- 10 m Höhe = 1,0 bar.
- Saughöhe: Seehöhen-Korrektur ≈ −0,5 m bei 430 m.
"""
from app.services.foerderstrecke_service import (
    Abschnitt,
    Ansaugpunkt,
    PumpenStation,
    barometrische_saughoehe_m,
    behaelter_standzeit_min,
    berechne_modus_a,
    hoehenverlust_bar,
    interpoliere_hoehe,
    kennlinie_max_q,
    reibungsverlust_bar,
    skaliere_kennlinie,
    verfuegbare_saughoehe_m,
)

# Kennlinien für Tests
KL_HLP16 = [[0, 53], [5000, 48], [8300, 42], [10800, 36], [13300, 28], [15800, 18]]
KL_FOX3 = [[0, 165], [1000, 150], [1600, 100], [2000, 30]]
K_B75 = 1.56
K_F150 = 0.049


# ── Reibung ─────────────────────────────────────────────────────────────────────

def test_reibung_b75_referenz_1bar_bei_800():
    dp = reibungsverlust_bar(K_B75, 800, 100)
    assert abs(dp - 1.0) < 0.02


def test_reibung_b75_400_lmin():
    # 0,25 bar @ 400 l/min (∝ Q²)
    dp = reibungsverlust_bar(K_B75, 400, 100)
    assert abs(dp - 0.25) < 0.02


def test_doppel_b_viertel_verlust():
    einzeln = reibungsverlust_bar(K_B75, 800, 100, n_parallel=1)
    doppelt = reibungsverlust_bar(K_B75, 800, 100, n_parallel=2)
    assert abs(doppelt - einzeln / 4.0) < 1e-6
    assert abs(doppelt - 0.25) < 0.02


def test_armaturenzuschlag():
    ohne = reibungsverlust_bar(K_B75, 800, 100, armaturen_zuschlag=0.0)
    mit = reibungsverlust_bar(K_B75, 800, 100, armaturen_zuschlag=0.05)
    assert abs(mit - ohne * 1.05) < 1e-9


def test_f150_deutlich_geringer_als_b75():
    # F-150 ≈ B-75/32
    assert reibungsverlust_bar(K_F150, 1000, 100) < reibungsverlust_bar(K_B75, 1000, 100) / 20


# ── Höhe ────────────────────────────────────────────────────────────────────────

def test_hoehe_10m_ist_1bar():
    assert abs(hoehenverlust_bar(10) - 1.0) < 1e-9
    assert abs(hoehenverlust_bar(-20) + 2.0) < 1e-9


# ── Kennlinie ────────────────────────────────────────────────────────────────────

def test_interpolation_stuetzpunkt_und_zwischen():
    assert interpoliere_hoehe(KL_HLP16, 5000) == 48
    # zwischen 5000(48) und 8300(42): bei 6650 ≈ 45
    h = interpoliere_hoehe(KL_HLP16, 6650)
    assert 44 < h < 46


def test_interpolation_klemmt_an_raendern():
    assert interpoliere_hoehe(KL_HLP16, -100) == 53
    assert interpoliere_hoehe(KL_HLP16, 999999) == 18
    assert interpoliere_hoehe([], 100) is None


def test_affinitaet_skalierung():
    kl = [[1000, 100]]
    hoch = skaliere_kennlinie(kl, 2000, 4000)  # doppelte Drehzahl
    assert hoch[0][0] == 2000        # Q ∝ n
    assert hoch[0][1] == 400         # H ∝ n²


def test_kennlinie_max_q():
    assert kennlinie_max_q(KL_FOX3) == 2000


# ── Saugseite ────────────────────────────────────────────────────────────────────

def test_seehoehenkorrektur_wolfurt():
    # 430 m → ca. 0,5 m weniger als 10,3 m
    reduktion = 10.3 - barometrische_saughoehe_m(430)
    assert 0.4 < reduktion < 0.65


def test_saughoehe_reserve_positiv_und_negativ():
    # geringe geodätische Höhe → Reserve positiv
    r_ok = verfuegbare_saughoehe_m(430, 3.0, 0.23, 1000)
    assert r_ok > 0
    # geodätische Höhe über der barometrischen Grenze → Reserve negativ
    r_bad = verfuegbare_saughoehe_m(430, 10.5, 0.23, 1000)
    assert r_bad < 0


# ── Puffer-Standzeit ─────────────────────────────────────────────────────────────

def test_puffer_standzeit():
    # 2000 l, Ablauf 500 l/min mehr als Zulauf → 4 min
    assert abs(behaelter_standzeit_min(2000, 1500, 2000) - 4.0) < 1e-9
    # ausgeglichen/füllend → None
    assert behaelter_standzeit_min(2000, 2000, 2000) is None
    assert behaelter_standzeit_min(2000, 2500, 2000) is None


# ── Modus A: Bisektion & Engpass ─────────────────────────────────────────────────

def _einfache_strecke(kennlinie, laenge_m=500, k=K_F150, delta_h=0.0):
    ansaug = Ansaugpunkt(seehoehe_m=430, geodaetische_saughoehe_m=2.0, saug_k=0.23)
    quelle = PumpenStation(
        kennlinie=kennlinie, typ="quellpumpe", name="Quelle",
        abschnitt_danach=Abschnitt(schlauch_k=k, laenge_m=laenge_m, delta_hoehe_m=delta_h),
    )
    return ansaug, [quelle]


def test_modus_a_liefert_positives_q():
    ansaug, stationen = _einfache_strecke(KL_HLP16, laenge_m=500, k=K_F150)
    res = berechne_modus_a(ansaug, stationen)
    assert res["machbar"] is True
    assert res["q_max_l_min"] > 1000
    assert res["druckprofil"]


def test_modus_a_engpass_durch_schwaechste_pumpe():
    # Starke Quelle → schwache FOX-3-Verstärkerpumpe begrenzt Q ≤ 2000
    ansaug = Ansaugpunkt(seehoehe_m=430, geodaetische_saughoehe_m=2.0, saug_k=0.23)
    quelle = PumpenStation(
        kennlinie=KL_HLP16, typ="quellpumpe", name="HLP 16.000",
        abschnitt_danach=Abschnitt(schlauch_k=K_F150, laenge_m=300),
    )
    fox = PumpenStation(
        kennlinie=KL_FOX3, typ="verstaerker", name="FOX 3",
        abschnitt_danach=Abschnitt(schlauch_k=K_B75, laenge_m=300, n_parallel=2),
    )
    res = berechne_modus_a(ansaug, [quelle, fox])
    assert res["q_max_l_min"] <= 2000 + 1e-6
    assert res["engpass"]["name"] == "FOX 3"


def test_modus_a_bisektion_konvergiert_und_ist_monoton():
    # Sehr lange, dünne Leitung senkt Q gegenüber kurzer Leitung
    ansaug, kurz = _einfache_strecke(KL_HLP16, laenge_m=200, k=K_B75)
    ansaug2, lang = _einfache_strecke(KL_HLP16, laenge_m=3000, k=K_B75)
    q_kurz = berechne_modus_a(ansaug, kurz)["q_max_l_min"]
    q_lang = berechne_modus_a(ansaug2, lang)["q_max_l_min"]
    assert q_lang < q_kurz


def test_modus_a_saugseite_unmoeglich():
    # geodätische Saughöhe über Grenze → nicht machbar
    ansaug = Ansaugpunkt(seehoehe_m=430, geodaetische_saughoehe_m=9.0, max_ansaughoehe_m=7.5)
    quelle = PumpenStation(kennlinie=KL_HLP16, typ="quellpumpe",
                           abschnitt_danach=Abschnitt(schlauch_k=K_F150, laenge_m=200))
    res = berechne_modus_a(ansaug, [quelle])
    assert res["machbar"] is False
    assert res["q_max_l_min"] == 0.0


def test_modus_a_hochpunkt_warnung():
    # Steiler Anstieg auf kurzer Leitung → Druck bricht am Hochpunkt ein
    ansaug = Ansaugpunkt(seehoehe_m=430, geodaetische_saughoehe_m=2.0)
    quelle = PumpenStation(
        kennlinie=KL_FOX3, typ="quellpumpe", name="TS",
        abschnitt_danach=Abschnitt(schlauch_k=K_B75, laenge_m=500, delta_hoehe_m=140),
    )
    res = berechne_modus_a(ansaug, [quelle])
    # Entweder Q stark reduziert oder Hochpunkt-Warnung vorhanden
    assert any("Hochpunkt" in w or "Auslass" in w for w in res["warnungen"]) or res["q_max_l_min"] < 1600
