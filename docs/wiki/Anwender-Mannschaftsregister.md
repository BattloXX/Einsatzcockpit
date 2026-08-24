# Mannschaftsregister

← [Zurück zur Startseite](Home)

Das Mannschaftsregister enthält alle Mitglieder mit ihren Qualifikationen. Es wird bei der Atemschutzüberwachung und bei der Kommandanten-Zuweisung verwendet.

## Mitglieder verwalten

**Admin** → **Mitglieder**

### Mitglied anlegen

**+ Neues Mitglied** → Formular ausfüllen:

| Feld | Pflicht | Beschreibung |
|------|---------|-------------|
| Nachname | ✓ | Familienname |
| Vorname | ✓ | |
| Telefon | – | Für Benachrichtigungen |
| E-Mail | – | Für Push-Benachrichtigungen |
| Aktiv | ✓ | Inaktive Mitglieder erscheinen nicht in Auswahllisten |

### Mitglied bearbeiten

Mitglied in der Liste anklicken → **Bearbeiten**

### Mitglied deaktivieren

Mitglied bearbeiten → **Aktiv** deaktivieren → **Speichern**  
Das Mitglied bleibt in der Datenbank (historische Einsätze bleiben korrekt), erscheint aber nicht mehr in Auswahllisten.

## Qualifikationen

### Verfügbare Qualifikationen

| Code | Bezeichnung |
|------|-------------|
| AGT | Atemschutzgeräteträger |
| MA | Maschinist |
| GK | Gruppenkommandant |
| ZK | Zugskommandant |
| EL | Einsatzleiter |
| TF | Truppführer |
| TM | Truppmann |
| JF | Jugendfeuerwehr |

### Qualifikation zuweisen

Mitglied öffnen → **+ Qualifikation** → Qualifikation auswählen → optionales **Ablaufdatum** (z.B. für AGT-Tauglichkeitsuntersuchung) → **Speichern**

### Ablaufdatum überwachen

In der Mitgliederliste wird ein **oranges Warnsymbol** angezeigt, wenn eine Qualifikation innerhalb der nächsten 30 Tage abläuft, und **rot** wenn sie bereits abgelaufen ist.

Bei der Trupp-Anlage in der Atemschutzüberwachung erscheint ebenfalls eine Warnung, wenn ein Mitglied keine gültige AGT-Qualifikation hat.

## Suche und Filter

In der Mitgliederliste:
- **Suchfeld**: Suche nach Name
- **Filter**: Nur Aktive / Nur mit AGT / Alle

## Verwendung im Einsatz

- **Atemschutzüberwachung**: Trupp-Mitglieder aus dem Mannschaftsregister auswählen
- **Kommandant zuweisen**: Bei Fahrzeug-Karten im Kanban-Board
- **PDF-Bericht**: Kommandanten-Namen erscheinen im Bericht
