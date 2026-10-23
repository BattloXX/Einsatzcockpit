"""SEC-03: rueckwirkende Auswertung fehlgeschlagener Geraetezugriffe."""
from __future__ import annotations

import json
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import AuditLog
from app.services import geraete_zugriff_analyse as a

LOG = [
    '10.0.0.5 - - [10/Oct/2026:12:00:00 +0000] "GET /api/v1/device/duty-state HTTP/1.1" 401 20 "-" "ECP/1.2 (Android 14)"',
    '10.0.0.5 - - [10/Oct/2026:12:01:00 +0000] "GET /api/v1/device/duty-state?app_version=1.2 HTTP/1.1" 401 20 "-" "ECP/1.2 (Android 14)"',
    '10.0.0.5 - - [10/Oct/2026:12:02:00 +0000] "GET /api/v1/device/duty-state HTTP/1.1" 200 20 "-" "ECP/1.2 (Android 14)"',
    '10.0.0.9 - - [10/Oct/2026:13:00:00 +0000] "GET /login?error=device_invalid HTTP/1.1" 200 900 "-" "Mozilla"',
    '10.0.0.7 - - [10/Oct/2026:14:00:00 +0000] "GET /geraet-login?token=GEHEIM123 HTTP/1.1" 302 0 "-" "x"',
    '10.0.0.6 - - [10/Oct/2026:14:00:00 +0000] "GET /api/v1/anderes HTTP/1.1" 401 0 "-" "x"',
    "kein Logformat",
]


def test_parse_nimmt_nur_fehlzugriffe_und_verwirft_tokens():
    z = a.parse_zeilen(LOG)
    assert [(x.ip, x.status) for x in z] == [("10.0.0.5", 401), ("10.0.0.5", 401), ("10.0.0.9", 200)]
    assert "GEHEIM123" not in repr(z)


def test_gruppieren_zaehlt_und_zeitspanne():
    (g1, g2) = a.gruppiere(a.parse_zeilen(LOG))
    assert (g1.ip, g1.anzahl) == ("10.0.0.5", 2) and g1.erster < g1.letzter
    assert g1.pfade == {"/api/v1/device/duty-state"}
    assert g2.anzahl == 1


def test_zuordnung_nur_bei_eindeutiger_ip():
    gruppen = a.gruppiere(a.parse_zeilen(LOG))
    erg = {z["gruppe"].ip: z for z in a.zuordnen(gruppen, {"10.0.0.5": {"Tablet TLF"}, "10.0.0.9": {"A", "B"}})}
    assert erg["10.0.0.5"]["geraet"] == "Tablet TLF"
    assert erg["10.0.0.9"]["geraet"] is None and "nicht eindeutig" in erg["10.0.0.9"]["grund"]
    leer = a.zuordnen(gruppen, {})
    assert all(z["geraet"] is None and "keine fruehere Anmeldung" in z["grund"] for z in leer)


def test_geraete_je_ip_aus_audit():
    ip = f"192.0.2.{uuid4().int % 200 + 1}"
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.add(AuditLog(action="auth.device_login", ip=ip, payload_json=json.dumps({"label": "Tablet X"})))
        db.commit()
        assert a.geraete_je_ip(db)[ip] == {"Tablet X"}
