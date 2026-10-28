# Optionale Multi-Worker-Startkonfiguration

Diese Dateien sind Vorschläge; keine wird vom bestehenden Systemd-Dienst oder
Basis-Compose automatisch aktiviert. Standard bleibt ein Worker. Keine
Produktivfreigabe ohne die dokumentierte Geräteabnahme.

- [Abnahme und Grenzen](../../../docs/multi-worker/multi-worker-phase6.md)
- [Rollout, Konfigurationsprüfung und Rollback](../../../docs/multi-worker/multi-worker-rollout.md)

Gemeinsame Parameter: `ECP_GUNICORN_WORKERS=1|2|3|4`, bei mehreren Workern
erforderliches `REDIS_URL`, ein gemeinsamer absoluter `LEADER_LOCK_PATH`.
Systemd-Pfade/EnvironmentFile, Docker-Image-Inhalt und Stopfristen vor Installation
prüfen. Keine zusätzlichen App-Container oder Hosts mit getrennten Locks starten.
