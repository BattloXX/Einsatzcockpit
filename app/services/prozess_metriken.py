"""Billige Prozess-/Hostmessung ohne Nutzdaten; Ereignisse bleiben im Prozess."""

import math
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path


class ProzessMetriken:
    def __init__(self, zeitquelle: Callable[[], float] = time.monotonic):
        self.zeitquelle = zeitquelle
        self.sperre = threading.Lock()
        # Pro Sekunde aggregiert: auch bei vielen Queries hoechstens 901 Eintraege.
        self.verzug: deque = deque()
        self.queries: deque = deque()
        self.intervall_max = 0.0
        self.intervall_anzahl = 0
        self.stillstand: dict | None = None

    def _eintragen(self, fenster: deque, dauer_ms: float, anzahl: int) -> None:
        jetzt = self.zeitquelle()
        self._kuerzen(fenster, jetzt)
        sekunde = int(jetzt)
        if fenster and fenster[-1][0] == sekunde:
            _, maximum, bisher = fenster.pop()
            fenster.append((sekunde, max(maximum, dauer_ms), bisher + anzahl))
        else:
            fenster.append((sekunde, dauer_ms, anzahl))

    @staticmethod
    def _kuerzen(fenster: deque, jetzt: float) -> None:
        while fenster and fenster[0][0] <= jetzt - 900:
            fenster.popleft()

    def loop_verzug(self, dauer_ms: float, schwelle_ms: float) -> None:
        if not math.isfinite(dauer_ms) or dauer_ms < 0:
            return
        anzahl = int(dauer_ms > schwelle_ms)
        with self.sperre:
            self._eintragen(self.verzug, dauer_ms, anzahl)
            self.intervall_max = max(self.intervall_max, dauer_ms)
            self.intervall_anzahl += anzahl

    def langsame_query(self, dauer_ms: float) -> None:
        if not math.isfinite(dauer_ms) or dauer_ms < 0:
            return
        with self.sperre:
            self._eintragen(self.queries, dauer_ms, 1)

    def stillstand_melden(self, dauer_ms: float, ort: str | None, *, neu: bool = True) -> None:
        with self.sperre:
            if neu:
                self.stillstand = {"zeit": datetime.now(UTC).isoformat(), "dauer_ms": dauer_ms,
                                   "ort": sicherer_ort(ort)}
            elif self.stillstand is not None:
                self.stillstand = {**self.stillstand, "dauer_ms": dauer_ms}

    def snapshot(self) -> dict:
        with self.sperre:
            jetzt = self.zeitquelle()
            self._kuerzen(self.verzug, jetzt)
            self._kuerzen(self.queries, jetzt)
            ergebnis = {
                "loop_verzug": {"max_ms": self.intervall_max, "anzahl_ueber_schwelle": self.intervall_anzahl,
                                "max_ms_15min": max((e[1] for e in self.verzug), default=0),
                                "anzahl_15min": sum(e[2] for e in self.verzug)},
                "langsame_queries": {"anzahl_15min": sum(e[2] for e in self.queries),
                                     "max_ms_15min": max((e[1] for e in self.queries), default=0)},
                "letzter_stillstand": dict(self.stillstand) if self.stillstand else None,
            }
            self.intervall_max = 0.0
            self.intervall_anzahl = 0
            return ergebnis


def sicherer_ort(ort: str | None) -> str | None:
    """Nur statischer App-Pfad und Zeilennummer, keine Funktions-/Prozess-Freitexte."""
    if ort and re.fullmatch(r"app/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py:[0-9]+", ort):
        return ort
    return None


def _lesen(pfad: Path) -> str:
    try:
        return pfad.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return ""


def proc_werte(text: str) -> dict[str, float]:
    werte = {}
    for zeile in text.splitlines():
        try:
            schluessel, wert = zeile.split(":", 1)
            zahl = float(wert.split()[0])
            if math.isfinite(zahl) and zahl >= 0:
                werte[schluessel] = zahl
        except (ValueError, IndexError):
            continue
    return werte


def prozess_lesen(wurzel: Path = Path("/proc")) -> dict:
    werte = proc_werte(_lesen(wurzel / "self/status"))
    return {"rss_mb": werte["VmRSS"] / 1024 if "VmRSS" in werte else None,
            "swap_mb": werte["VmSwap"] / 1024 if "VmSwap" in werte else None,
            "threads": int(werte["Threads"]) if "Threads" in werte else None}


def psi_werte(text: str) -> dict:
    ergebnis: dict = {"some": {"avg60": None, "avg300": None},
                      "full": {"avg60": None, "avg300": None}}
    for zeile in text.splitlines():
        teile = zeile.split()
        if not teile or teile[0] not in ergebnis:
            continue
        for teil in teile[1:]:
            try:
                name, wert = teil.split("=")
                zahl = float(wert)
                if name in ("avg60", "avg300") and math.isfinite(zahl) and zahl >= 0:
                    ergebnis[teile[0]][name] = zahl
            except ValueError:
                continue
    return ergebnis


def host_lesen(wurzel: Path = Path("/proc")) -> dict:
    werte = proc_werte(_lesen(wurzel / "meminfo"))
    ergebnis: dict = {name: werte[name] / 1024 if name in werte else None
                     for name in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")}
    ergebnis["psi"] = {name: psi_werte(_lesen(wurzel / "pressure" / name))
                       for name in ("cpu", "memory", "io")}
    try:
        last = [float(wert) for wert in _lesen(wurzel / "loadavg").split()[:3]]
        ergebnis["loadavg"] = last if len(last) == 3 and all(math.isfinite(w) for w in last) else None
    except ValueError:
        ergebnis["loadavg"] = None
    ergebnis["cpu_anzahl"] = os.cpu_count() if (wurzel / "stat").exists() else None
    return ergebnis


def host_indizes(zeilen) -> dict[str, int]:
    return {host: index for index, host in enumerate(dict.fromkeys(z.hostname for z in zeilen), 1)}


def unsaubere_enden(zeilen, jetzt) -> list[dict]:
    """TTL-Enden je Host; bis zu drei Sekunden Abstand gelten als sauberer Stop."""
    hosts: dict[str, dict] = {}
    for zeile in zeilen:
        host = hosts.setdefault(zeile.hostname, {"host_index": len(hosts) + 1, "anzahl_1h": 0, "anzahl_24h": 0})
        if (zeile.expires_at > jetzt
                or zeile.expires_at - zeile.heartbeat_at <= timedelta(seconds=3)):
            continue
        for stunden in (1, 24):
            if jetzt - timedelta(hours=stunden) <= zeile.expires_at:
                host[f"anzahl_{stunden}h"] += 1
    return list(hosts.values())


sammler = ProzessMetriken()


def heartbeat_metriken() -> dict:
    return {**prozess_lesen(), **sammler.snapshot()}
