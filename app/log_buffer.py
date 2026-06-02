"""In-Memory-Log-Ringpuffer – hält die letzten N Log-Einträge aller Logger.

Wird einmalig per setup() in main.py registriert und kann danach von jedem
Modul über get_entries() abgefragt werden (z.B. für den Admin-Log-Viewer).
"""
import collections
import logging
import threading
import time

MAX_ENTRIES = 2000
_FORMATTER = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

LEVEL_ORDER = {
    "DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50,
}


class _MemoryLogHandler(logging.Handler):
    def __init__(self, maxlen: int = MAX_ENTRIES) -> None:
        super().__init__()
        self._buffer: collections.deque = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            entry = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "name": record.name,
                "msg": msg,
            }
            with self._lock:
                self._buffer.append(entry)
        except Exception:
            self.handleError(record)

    def get_entries(self, n: int = 500, min_level_name: str = "DEBUG") -> list[dict]:
        min_lvl = LEVEL_ORDER.get(min_level_name.upper(), 0)
        with self._lock:
            filtered = [e for e in self._buffer if LEVEL_ORDER.get(e["level"], 0) >= min_lvl]
        return filtered[-n:]

    def as_text(self, n: int = 500, min_level_name: str = "DEBUG") -> str:
        return "\n".join(e["msg"] for e in self.get_entries(n, min_level_name))


_handler = _MemoryLogHandler()
_handler.setFormatter(_FORMATTER)
_initialized = False


def setup() -> None:
    """Root-Logger um den In-Memory-Handler erweitern. Einmalig beim App-Start aufrufen."""
    global _initialized
    if _initialized:
        return
    _initialized = True
    _handler.setFormatter(_FORMATTER)
    root = logging.getLogger()
    if _handler not in root.handlers:
        root.addHandler(_handler)


def get_entries(n: int = 500, min_level_name: str = "DEBUG") -> list[dict]:
    return _handler.get_entries(n, min_level_name)


def get_text(n: int = 500, min_level_name: str = "DEBUG") -> str:
    return _handler.as_text(n, min_level_name)
