# Datenbank: Snapshot-Isolation

Seit MariaDB 11.6.2 ist `innodb_snapshot_isolation` standardmäßig eingeschaltet.
Eine Transaktion liest dabei einen bestimmten Datenstand (Snapshot). Hat eine
andere Transaktion eine Zeile inzwischen geändert, kann MariaDB einen späteren
sperrenden Zugriff (`SELECT ... FOR UPDATE`) oder eine Änderung mit Fehler 1020
abweisen. Das kann etwa beim gleichzeitigen Bearbeiten oder Abholen von Jobs
auftreten. SQLite bildet dieses Verhalten nicht ab.

## Zentrale Regel der Anwendung

Die App setzt beim Aufbau jeder neuen Anwendungsverbindung auf MariaDB ab
11.6.2 `SET SESSION innodb_snapshot_isolation=OFF`. Sperrende Zugriffe können
damit wieder den aktuellen Zeilenstand lesen, wie vor dieser MariaDB-Änderung.
Die globale Servereinstellung und die normale Transaktionsisolation bleiben
unverändert. Bestehende Sessions für SMS und Routing mit `READ COMMITTED`
behalten ihre Einstellung.

Der Umgebungsschalter `DB_SNAPSHOT_ISOLATION_OFF` ist standardmäßig `true`.
Er gilt für neue Verbindungen; nach einer Konfigurationsänderung müssen alle
App-Worker neu gestartet werden, damit auch deren Verbindungspools erneuert
werden. MySQL, SQLite und ältere MariaDB-Versionen werden nicht umgestellt.

Die zentrale Retry-Hilfe erkennt Fehler 1020, Lock-Timeout 1205 und Deadlock
1213. Geeignete reine DB-Transaktionen werden begrenzt wiederholt. Versand,
Dateiübergaben und andere Außenwirkungen dürfen dadurch nicht wiederholt werden.
Ein Verbindungsabbruch mit unbekanntem Commit-Ausgang ist kein solcher Retry-Fall.

## Diagnose und Zähler

Die [Systemdiagnose](Administration-Systemdiagnose) enthält im Modul
`datenbank` den lesenden Check `db.snapshot_isolation`. Er ist auch über die
vorhandenen [Admin-MCP-Diagnosewerkzeuge](Administration-System-Admin-MCP)
erreichbar. Das Laufdetail zeigt bei MariaDB ab 11.6.2 die Version,
`innodb_snapshot_isolation` für GLOBAL und SESSION, `transaction_isolation`
und den App-Schalter. Die Diagnose baut eine eigene Verbindung mit derselben
zentralen Session-Regel auf; sie prüft keine einzelnen bereits offenen
Poolverbindungen. Andere Datenbanken und ältere Versionen werden als
übersprungen gemeldet.

- Schalter `true`, SESSION `ON`: Warnung, weil die zentrale Regel nicht greift.
- Schalter `false`: Info über das Risiko von Fehler 1020, auch wenn die
  Servervorgabe die Diagnose-Session bereits auf `OFF` hält.
- GLOBAL `ON`, SESSION `OFF`: erwarteter Zustand bei aktivierter App-Regel.

Im Laufdetail stehen außerdem die Zähler für 1020, 1205 und 1213 mit der PID
des ausführenden App-Workers. Sie zählen erkannte Konflikte seit Prozessstart,
auch wenn kein weiterer Versuch erlaubt war; sie zählen weder erfolgreiche
Retries noch endgültig fehlgeschlagene Aufträge. Bei insgesamt 1 bis 9
Konflikten entsteht ein Info-Befund, ab 10 eine Warnung. Vereinzelte Konflikte
sind bei parallelem Betrieb erwartbar; die Warnschwelle soll zur Prüfung der
Entwicklung und der Logs anregen. Sie ist eine kumulative Beobachtungsschwelle,
keine Fehlerrate und kein Beleg für einen aktuellen Ausfall.

Die Zähler sind **prozesslokal, pro Worker**: kein Gesamtwert aller Worker,
keine dauerhafte Metrik. Ein Neustart setzt sie zurück. Der kurzlebige
Diagnoseprozess erhält den Zählerstand des ausführenden Workers. Ein Diagnose-
Lauf erfasst damit nicht automatisch die übrigen Worker. Die Logs enthalten
je erkanntem Konflikt den numerischen Fehlercode ohne SQL-Parameter.

## Notfall-Rücknahme

Um die App-Regel zurückzunehmen, `DB_SNAPSHOT_ISOLATION_OFF=false` in der
Deployment-Konfiguration setzen und alle App-Worker neu starten. Danach lässt
die App die Servervorgabe unangetastet; `false` erzwingt kein `ON`. Bei einer
Servervorgabe `ON` sind Fehler 1020 wieder möglich. Anschließend Diagnose und
Konfliktlogs prüfen. Zur erneuten Aktivierung `true` setzen und wiederum alle
Worker neu starten. Der Diagnose-Check selbst ändert keine Einstellungen.

Als Alternative können Betreiber auf unterstützten MariaDB-Versionen
`innodb_snapshot_isolation=OFF` dauerhaft in der Serverkonfiguration setzen.
Das betrifft auch andere Clients, etwa Skripte und Migrationen. Eine globale
Laufzeitänderung allein ist nicht dauerhaft und verändert keine bereits offenen
Sessions. Serverkonfiguration, Neustart und weitere DB-Clients müssen deshalb
aufeinander abgestimmt werden. Die App-Regel benötigt keine globale Änderung.
