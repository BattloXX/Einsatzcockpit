"""Vollständiges Höhenprofil in der Förderleistung: Zwischen-Hochpunkte (Damm)
druck- und saugseitig berücksichtigen — nicht nur Anfang/Ende."""
from app.routers.ui_foerderstrecke import _baue_eingabe
from app.services.foerderstrecke_service import (
    Abschnitt,
    Ansaugpunkt,
    PumpenStation,
    abschnitt_hoehen_stuetzpunkte,
    berechne_modus_a,
)

KL = [[0, 53], [8000, 42], [16000, 18]]


# ── Profil-Extraktion ───────────────────────────────────────────────────────────

def test_stuetzpunkte_erfassen_zwischen_hochpunkt():
    profil = [[0, 430], [250, 480], [500, 440]]   # Damm +50 m in der Mitte
    stz = abschnitt_hoehen_stuetzpunkte(profil, 0, 500, segment_m=125)
    assert stz[0] == 0.0
    assert max(stz) >= 49          # Damm-Scheitel relativ ~ +50 m sichtbar
    assert stz[-1] == 10.0         # Endhöhe 440-430


def test_stuetzpunkte_teilabschnitt():
    profil = [[0, 400], [500, 400], [1000, 460]]
    stz = abschnitt_hoehen_stuetzpunkte(profil, 500, 1000, segment_m=250)
    assert stz[0] == 0.0 and stz[-1] == 60.0


def test_leeres_profil_gibt_none():
    assert abschnitt_hoehen_stuetzpunkte([], 0, 100) is None
    assert abschnitt_hoehen_stuetzpunkte([[0, 400]], 0, 100) is None


# ── Druckseite: Damm reduziert Förderleistung / warnt ────────────────────────────

def _quelle(stuetz=None, delta=10.0):
    ansaug = Ansaugpunkt(seehoehe_m=430, geodaetische_saughoehe_m=2.0)
    quelle = PumpenStation(
        kennlinie=KL, typ="quellpumpe", name="Quelle",
        abschnitt_danach=Abschnitt(schlauch_k=1.56, laenge_m=500, n_parallel=1,
                                   delta_hoehe_m=delta, hoehen_stuetzpunkte=stuetz),
    )
    return ansaug, [quelle]


def test_damm_wird_beruecksichtigt_nicht_nur_endpunkte():
    # Ohne Zwischenprofil (nur +10 m Endpunkt) vs. mit Damm +80 m in der Mitte
    ansaug_flach, st_flach = _quelle(stuetz=None, delta=10.0)
    ansaug_damm, st_damm = _quelle(stuetz=[0, 40, 80, 40, 10], delta=10.0)
    ohne = berechne_modus_a(ansaug_flach, st_flach)
    mit = berechne_modus_a(ansaug_damm, st_damm)
    # Der Damm ist relevant: entweder weniger Q oder eine Hochpunkt-Warnung
    hat_hochpunkt = any("Hochpunkt" in w for w in mit["warnungen"])
    assert hat_hochpunkt or mit["q_max_l_min"] < ohne["q_max_l_min"]


# ── Saugseite: Scheitel der Saugleitung bindet ───────────────────────────────────

def test_saug_scheitel_bindet_wie_heber():
    a = Ansaugpunkt(geodaetische_saughoehe_m=2.0, saug_scheitel_m=6.0)
    assert a.effektive_saughoehe_m == 6.0

    # Scheitel 9 m > Grenze 7,5 m → nicht machbar, obwohl Pumpe nur 2 m über Wasser
    ansaug = Ansaugpunkt(geodaetische_saughoehe_m=2.0, saug_scheitel_m=9.0, max_ansaughoehe_m=7.5)
    quelle = PumpenStation(kennlinie=KL, typ="quellpumpe",
                           abschnitt_danach=Abschnitt(schlauch_k=0.049, laenge_m=200))
    res = berechne_modus_a(ansaug, [quelle])
    assert res["machbar"] is False
    assert any("Saughöhe" in w for w in res["warnungen"])


# ── Endpoint-Aufbau: Gesamtprofil wird je Abschnitt zerlegt ─────────────────────

def test_baue_eingabe_zerlegt_gesamtprofil_je_abschnitt():
    daten = {
        "ansaug": {"seehoehe_m": 430, "geodaetische_saughoehe_m": 2, "saug_scheitel_m": 4},
        "hoehenprofil": [[0, 400], [250, 470], [500, 410], [750, 415], [1000, 420]],
        "stationen": [
            {"typ": "quellpumpe", "kennlinie": KL,
             "abschnitt": {"schlauch_k": 1.56, "laenge_m": 500, "n_parallel": 1}},
            {"typ": "verstaerker", "kennlinie": KL,
             "abschnitt": {"schlauch_k": 1.56, "laenge_m": 500, "n_parallel": 1}},
        ],
    }
    ansaug, stationen, _material = _baue_eingabe(daten, db=None, org_id=1)
    # Saugscheitel übernommen
    assert ansaug.saug_scheitel_m == 4
    # Erster Abschnitt (0–500 m) enthält den Damm-Scheitel (~ +70 m relativ)
    stz0 = stationen[0].abschnitt_danach.hoehen_stuetzpunkte
    assert stz0 is not None and max(stz0) >= 60
    # Zweiter Abschnitt (500–1000 m) startet bei 410 → Endhöhe +10 m
    stz1 = stationen[1].abschnitt_danach.hoehen_stuetzpunkte
    assert stz1 is not None and abs(stz1[-1] - 10.0) < 0.5
