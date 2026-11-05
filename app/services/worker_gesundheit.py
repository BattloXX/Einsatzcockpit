"""Gemeinsame Bewertung abgelaufener Heartbeats trotz Deploy-Retention."""
from datetime import timedelta

WORKER_AUSFALL_FENSTER = timedelta(minutes=10)


def worker_ausfaelle(registrierte, jetzt):
    """Juengste Ausfaelle ohne spaeter gestarteten aktiven Ersatz am selben Host."""
    aktive = [w for w in registrierte if w.expires_at > jetzt]
    return [w for w in registrierte if jetzt - WORKER_AUSFALL_FENSTER <= w.expires_at <= jetzt
            and not any(a.hostname == w.hostname and a.started_at > w.started_at for a in aktive)]
