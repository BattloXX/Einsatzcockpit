"""Rollierende Metriken und Linux-Fixtures ohne Messwerte des Testhosts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

from app.models.admin_security import utcnow
from app.services.prozess_metriken import (
    ProzessMetriken,
    host_lesen,
    prozess_lesen,
    sicherer_ort,
    unsaubere_enden,
)


def test_fenster_und_heartbeat_reset():
    uhr = [0.0]
    sammler = ProzessMetriken(lambda: uhr[0])
    sammler.loop_verzug(5000, 1000)
    sammler.loop_verzug(100, 1000)
    sammler.langsame_query(800)
    uhr[0] = 899
    sammler.langsame_query(900)
    werte = sammler.snapshot()
    assert werte["loop_verzug"] == {
        "max_ms": 5000, "anzahl_ueber_schwelle": 1, "max_ms_15min": 5000, "anzahl_15min": 1}
    assert werte["langsame_queries"] == {"anzahl_15min": 2, "max_ms_15min": 900}
    assert sammler.snapshot()["loop_verzug"]["max_ms"] == 0
    uhr[0] = 900
    werte = sammler.snapshot()
    assert werte["loop_verzug"]["max_ms_15min"] == 0
    assert werte["langsame_queries"] == {"anzahl_15min": 1, "max_ms_15min": 900}
    uhr[0] = 1799
    assert sammler.snapshot()["langsame_queries"]["anzahl_15min"] == 0


def test_threads_und_begrenzter_speicher():
    sammler = ProzessMetriken(lambda: 1)

    def schreiben(_):
        for _ in range(200):
            sammler.langsame_query(1000)
            sammler.loop_verzug(5000, 1000)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(schreiben, range(4)))
    werte = sammler.snapshot()
    assert werte["langsame_queries"]["anzahl_15min"] == 800
    assert werte["loop_verzug"]["anzahl_ueber_schwelle"] == 800
    assert len(sammler.queries) == len(sammler.verzug) == 1


def test_proc_fixtures(tmp_path, monkeypatch):
    (tmp_path / "self").mkdir()
    (tmp_path / "self/status").write_text(
        "Name: GEHEIM\nVmRSS: 614400 kB\nVmSwap: 2048 kB\nThreads: 7\nKaputt: x\n")
    assert prozess_lesen(tmp_path) == {"rss_mb": 600, "swap_mb": 2, "threads": 7}
    (tmp_path / "meminfo").write_text(
        "MemTotal: 4194304 kB\nMemAvailable: 153600 kB\nSwapTotal: 1048576 kB\nSwapFree: 102400 kB\n")
    (tmp_path / "pressure").mkdir()
    (tmp_path / "pressure/memory").write_text(
        "some avg10=7.00 avg60=6.00 avg300=5.00 total=123\nfull avg60=4.00 avg300=3.00\n")
    (tmp_path / "pressure/io").write_text("full avg60=kaputt avg300=nan\nkaputt\n")
    (tmp_path / "loadavg").write_text("1.25 2.50 3.75 1/100 42\n")
    (tmp_path / "stat").touch()
    monkeypatch.setattr("app.services.prozess_metriken.os.cpu_count", lambda: 4)
    host = host_lesen(tmp_path)
    assert host["MemTotal"] == 4096 and host["MemAvailable"] == 150
    assert host["SwapTotal"] == 1024 and host["SwapFree"] == 100
    assert host["psi"]["memory"]["some"] == {"avg60": 6, "avg300": 5}
    assert host["psi"]["memory"]["full"] == {"avg60": 4, "avg300": 3}
    assert host["psi"]["io"]["full"] == {"avg60": None, "avg300": None}
    assert host["cpu_anzahl"] == 4 and host["loadavg"] == [1.25, 2.5, 3.75]


def test_proc_fehlt_oder_unlesbar(tmp_path):
    assert prozess_lesen(tmp_path) == {"rss_mb": None, "swap_mb": None, "threads": None}
    host = host_lesen(tmp_path)
    assert host["MemTotal"] is None and host["loadavg"] is None and host["cpu_anzahl"] is None
    (tmp_path / "self").mkdir()
    (tmp_path / "self/status").write_bytes(b"\xff")
    assert prozess_lesen(tmp_path)["rss_mb"] is None


def test_stillstand_nur_app_ort():
    sammler = ProzessMetriken()
    sammler.stillstand_melden(1000, "app/services/test.py:123")
    sammler.stillstand_melden(60000, None, neu=False)
    wert = sammler.snapshot()["letzter_stillstand"]
    assert wert["ort"] == "app/services/test.py:123" and wert["dauer_ms"] == 60000
    assert wert["zeit"]
    for ort in ("/home/GEHEIM/app/test.py:12", "app/../secret.py:1", "app/test.py:1 GEHEIM", None):
        assert sicherer_ort(ort) is None


def test_unsaubere_enden_fenster_host_und_toleranz():
    jetzt = utcnow()

    def zeile(host, alter_s, ttl_s):
        ende = jetzt - timedelta(seconds=alter_s)
        return SimpleNamespace(hostname=host, expires_at=ende, heartbeat_at=ende - timedelta(seconds=ttl_s))

    zeilen = [zeile("geheim", 0, 20), zeile("geheim", 3600, 20), zeile("geheim", 3601, 20),
              zeile("geheim", 86400, 20), zeile("geheim", 86401, 20), zeile("geheim", -1, 20),
              zeile("geheim", 100, 0), zeile("geheim", 100, 3), zeile("anderer", 100, 20)]
    assert unsaubere_enden(zeilen, jetzt) == [
        {"host_index": 1, "anzahl_1h": 2, "anzahl_24h": 4},
        {"host_index": 2, "anzahl_1h": 1, "anzahl_24h": 1}]
