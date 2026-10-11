# Sicherheitsrichtlinie / Security Policy

## Sicherheitslücken melden

Bitte melde Sicherheitslücken **nicht** über öffentliche Issues.

- E-Mail: **oss-scanner@get.einsatzcockpit.com**
- oder über GitHub: *Security → Report a vulnerability* (Private Vulnerability Reporting)

Hilfreich sind: betroffene Version/Commit, Beschreibung, Reproduktionsschritte (gerne als
pytest-Test) und die Einschätzung der Auswirkung. Wir bestätigen den Eingang in der Regel
innerhalb weniger Tage und stimmen die Veröffentlichung nach dem Fix mit dir ab.

## Unterstützte Versionen

Sicherheitsfixes gibt es für den aktuellen Stand von `main` bzw. das jeweils neueste Release.

## Automatisierte Prüfung

Das Projekt nimmt am [Anthropic OSS Scanner](https://github.com/anthropics/oss-scanner) teil.
Build-Anleitung und Bedrohungsmodell für den Scanner liegen unter `.oss-scanner/`.
