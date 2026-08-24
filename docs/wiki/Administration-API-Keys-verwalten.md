# API-Keys verwalten

← [Zurück zur Startseite](Home)

API-Keys ermöglichen externen Systemen (z.B. dem Alarmierungssystem) die automatische Anlage von Einsätzen über die REST-API, ohne einen Benutzernamen/Passwort zu verwenden.

## API-Keys anzeigen

**Admin** → **API-Keys**

Liste aller Keys mit:
- Label (z.B. „Alarmierungssystem FWWO")
- Erstellt von
- Erstellt am
- Zuletzt verwendet
- Status (Aktiv / Widerrufen / Abgelaufen)

## Neuen API-Key erstellen

### Via Web-Interface

**Admin** → **API-Keys** → **+ Neuer API-Key**

| Feld | Beschreibung |
|------|-------------|
| Label | Beschreibung wofür der Key ist |
| Ablaufdatum | Optional, für zeitlich begrenzte Keys |

Nach dem Erstellen wird der Key **einmalig im Klartext angezeigt**. Sofort kopieren!

### Via CLI

```bash
python -m app.cli create-api-key --label "Alarmierungssystem FWWO"
# Ausgabe: fwwo_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Mit Ablaufdatum:
```bash
python -m app.cli create-api-key --label "Test-Key" --expires "2026-12-31"
```

## API-Key in der Anfrage verwenden

```http
POST /api/v1/einsatz HTTP/1.1
Host: einsatzleiter.feuerwehr-wolfurt.at
X-API-Key: fwwo_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Content-Type: application/json

{ "Key": "...", ... }
```

## API-Key widerrufen

**Admin** → **API-Keys** → Key auswählen → **Widerrufen**

Oder per CLI:
```bash
python -m app.cli revoke-api-key --id 1
```

Widerrufene Keys werden **sofort** abgelehnt. Sie bleiben in der Liste sichtbar (für Audit-Zwecke).

## API-Key rotieren (Austausch)

Best Practice: Alle 6–12 Monate Keys rotieren.

1. Neuen Key erstellen
2. Neuen Key im Alarmierungssystem hinterlegen
3. Alten Key widerrufen (erst nach Bestätigung dass der neue funktioniert)

## API-Key-Format

Alle Keys beginnen mit dem Präfix `fwwo_` und bestehen aus URL-sicheren Zeichen.  
In der Datenbank wird nur der **SHA-256-Hash** des Keys gespeichert — auch ein kompromittierter Datenbank-Dump gibt keine echten Keys preis.

## Audit-Protokoll

Jede Verwendung eines API-Keys wird im Audit-Log festgehalten:
- Zeitstempel
- IP-Adresse des Anrufenden
- Aktion (z.B. `incident.created`)
- Einsatz-ID (falls relevant)

Details: [Audit-Log und Zeitreise](Administration-Audit-Log-und-Zeitreise)
