# Audit-Log und Zeitreise

← [Zurück zur Startseite](Home)

## Zwei Ebenen des Audit-Logs

### System-Audit-Log (`audit_log`)

Systemweite Ereignisse ohne direkten Einsatzbezug:

| Ereignis | Beispiel |
|----------|---------|
| Login / Logout | `user.login` von IP 192.168.1.100 |
| Benutzer angelegt | `user.created` (admin) |
| API-Key erstellt | `api_key.created` |
| API-Key verwendet | `api_key.used` + Einsatz-ID |
| Stammdaten geändert | `vehicle.updated` |
| Zugriffsverstoß | `permission.denied` |

Aufruf: **Admin** → **System-Audit-Log**

### Einsatz-Änderungslog (`incident_change`)

Jede einzelne Änderung an einem Einsatz und allen zugehörigen Entitäten:

| Aktion | Beschreibung |
|--------|-------------|
| `task.created` | Neuer Auftrag angelegt |
| `task.assigned` | Auftrag einem Fahrzeug zugewiesen |
| `task.done` | Auftrag als erledigt markiert |
| `vehicle.moved` | Fahrzeug in andere Spalte |
| `troop.status_changed` | AS-Trupp-Status geändert |
| `pressure.logged` | Druckwert protokolliert |
| `person.created` | Person erfasst |
| `incident.closed` | Einsatz abgeschlossen |

**Jede** Änderung enthält:
- Zeitstempel (`ts`)
- Benutzer oder API-Key
- Entitätstyp und ID
- **Vorher-JSON** (`before_json`)
- **Nachher-JSON** (`after_json`)
- IP-Adresse

Aufruf: **Einsatz-Board** → **Historie** oder `/einsatz/{id}/historie`

## System-Audit-Log aufrufen

**Admin** → **Audit-Log**

Filter:
- Zeitraum (von/bis)
- Benutzer
- Aktionstyp
- IP-Adresse

## Einsatz-Historie aufrufen

Im Board eines abgeschlossenen (oder aktiven) Einsatzes: Tab **Historie**

Zeigt chronologisch alle Änderungen mit Vorher/Nachher-Vergleich.

## Zeitreise — Stand zu beliebigem Zeitpunkt

Das Einsatz-Änderungslog erlaubt die vollständige Rekonstruktion jedes Einsatz-Zustands zu jedem vergangenen Zeitpunkt.

### Via Web-Interface

**Admin** → **Archiv** → Einsatz öffnen → **Zeitreise** → Zeitpunkt wählen

Das Board zeigt den rekonstruierten Zustand zum gewählten Zeitpunkt (read-only).

### Via URL

```
/admin/einsatz/{id}/zeitreise?at=2026-05-22T21:30:00
```

Der Parameter `at` ist ein ISO-8601-Zeitstempel.

## Datenschutz und Aufbewahrung

- Audit-Log-Einträge werden **nicht automatisch gelöscht**
- IP-Adressen werden gespeichert (für Forensik bei Störfällen)
- Abgeschlossene Einsätze sollten nach der internen Aufbewahrungsfrist (Empfehlung: 5 Jahre) archiviert/gelöscht werden
- DSGVO: Alle im Log enthaltenen Personendaten (Benutzernamen) sind Mitglieder der eigenen Organisation

## Unterschied zu Snapshots (HTML-Version)

Die HTML-Version speicherte alle 2 Minuten einen vollständigen Snapshot. Dieser Ansatz hatte Lücken (Aktionen zwischen Snapshots gingen verloren).

Das neue System schreibt bei **jeder** Änderung sofort einen Eintrag mit Vorher/Nachher. Damit ist lückenlose Rekonstruktion möglich — besser und speichereffizienter als Snapshots.
