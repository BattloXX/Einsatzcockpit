"""Erfasst den Hauptthread waehrend einer Event-Loop-Blockade."""

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path


class VerzugsWatchdog:
    def __init__(self, schwelle_ms: float, *, zeitquelle: Callable[[], float] = time.monotonic):
        self.schwelle_ms = schwelle_ms
        self.zeitquelle = zeitquelle
        self.letzter_puls = zeitquelle()
        self.gemeldeter_puls: float | None = None
        self.hauptthread_id = threading.main_thread().ident or 0
        self.repo = Path(__file__).resolve().parents[2]
        self.logger = logging.getLogger("einsatzleiter.loop_lag")
        self.stopp_signal = threading.Event()
        self.thread = threading.Thread(target=self._laufen, name="Loop-Verzugswatchdog", daemon=True)

    def puls(self) -> None:
        self.letzter_puls = self.zeitquelle()

    def pruefe(self) -> None:
        """Pro ausgebliebenem Puls einmal warnen; ein neuer Puls entschaerft den Vorfall."""
        puls = self.letzter_puls
        if self.schwelle_ms <= 0 or (self.zeitquelle() - puls) * 1000 <= self.schwelle_ms:
            return
        if self.gemeldeter_puls == puls:
            return
        self.gemeldeter_puls = puls
        frame = sys._current_frames().get(self.hauptthread_id)
        spitze: list[str] = []
        while frame is not None and len(spitze) < 6:
            datei = Path(frame.f_code.co_filename)
            # Auch Bibliotheken ausserhalb des Repos als relative Pfade darstellen.
            spitze.append(f"{os.path.relpath(datei, self.repo)}:{frame.f_lineno} {frame.f_code.co_name}")
            frame = frame.f_back
        self.logger.warning("Event-Loop-Watchdog: Hauptthread-Stack: %s", " | ".join(reversed(spitze)))

    def _laufen(self) -> None:
        while not self.stopp_signal.wait(0.25):
            self.pruefe()

    def starten(self) -> None:
        self.thread.start()

    def stoppen(self) -> None:
        self.stopp_signal.set()
        self.thread.join()
