"""Strukturierte Fehler externer Dienste fuer MCP und UI."""


class ExternerDienstFehler(Exception):
    def __init__(self, *, code: str, meldung: str, retryable: bool, details: dict | None = None):
        super().__init__(meldung)
        self.code = code
        self.meldung = meldung
        self.retryable = retryable
        self.details = details or {}
