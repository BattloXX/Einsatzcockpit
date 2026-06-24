# Fahrtenbuch – Administration

← [Zurück zur Startseite](Home)

> URL-Bereich: `/admin/fahrtenbuch/` und `/verwaltung/fahrten/`  
> Zugänglich für: `org_admin`, `admin`

---

## Übersicht Admin-Bereich

| URL | Funktion |
|-----|---------|
| `/admin/fahrtenbuch/fahrzeuge` | Fahrzeug-Konfiguration (km/BH/Seilwinde, Warnschwellen, QR) |
| `/admin/fahrtenbuch/zwecke` | Fahrtzwecke anlegen/bearbeiten |
| `/admin/fahrtenbuch/zielorte` | Zielorte anlegen/bearbeiten |
| `/admin/fahrtenbuch/token` | Org-Token und Fahrzeug-QR-Codes |
| `/admin/fahrtenbuch/einstellungen` | Schadensmeldung, Doppelfahrt-Erkennung |
| `/verwaltung/fahrten` | Fahrten-Liste (Verwaltung) |

---

## Fahrzeug-Konfiguration

**Admin → Fahrtenbuch → Fahrzeuge** (`/admin/fahrtenbuch/fahrzeuge`)

Liste aller Fahrzeuge. Jedes Fahrzeug aufklappen, um die Fahrtenbuch-Einstellungen zu bearbeiten.

### Erfassungs-Optionen

| Option | Beschreibung |
|--------|-------------|
| **Kennzeichen** | Amtliches Kennzeichen — erscheint in der Fahrten-Liste |
| **km erfassen** | Aktiviert das km-Stand-Feld im Erfassungsformular |
| **Betriebsstunden erfassen** | Aktiviert das BH-Stand-Feld |
| **2. Maschinist Pflicht** | Zeigt das Feld für den 2. Maschinisten |
| **Seilwinde abfragen** | Aktiviert den Seilwinden-Abschnitt (BH, Bediener, Züge, Wartung) |

### Warnschwellen

Wenn der erfasste Zähler-Delta diese Schwelle überschreitet, erscheint im Erfassungsformular ein Warnhinweis — der Benutzer muss mit einer Checkbox bestätigen, bevor gespeichert wird.

| Feld | Standard | Beschreibung |
|------|:--------:|-------------|
| **Warnschwelle km** | 50 | Delta-km, ab dem eine Warnung erscheint |
| **Warnschwelle BH** | 10 | Delta-Betriebsstunden |
| **Seilwinde BH** | 10 | Fest codiert (nicht pro Fahrzeug konfigurierbar) |

### Schadensmeldung (je Fahrzeug)

Optional können pro Fahrzeug abweichende Empfänger für Schadensmeldungen hinterlegt werden. Diese überschreiben die org-weiten Einstellungen.

| Feld | Beschreibung |
|------|-------------|
| **Schaden-Mail Override** | E-Mail-Adresse (z. B. Gerätewart des Fahrzeugs) |
| **Teams-Webhook Override** | Incoming-Webhook-URL für fahrzeugspezifischen Teams-Channel |

### Zählerstände einsehen und korrigieren

Unter jedem Fahrzeug werden die aktuellen Zählerstände angezeigt:
- km-Stand, Betriebsstunden (h), Seilwinde BH (h)

Diese Werte werden automatisch bei jeder Fahrterfassung aktualisiert. Bei Fehleingaben kann ein Admin den Zählerstand über **„Zähler korrigieren"** direkt überschreiben (ohne Plausibilitätsprüfung — nur für Admin/Leitung).

### Fahrzeug-Reihenfolge

Die Reihenfolge im Erfassungsformular kann per **Drag & Drop** (⋮⋮ Griffpunkte) geändert werden. Die neue Reihenfolge wird automatisch gespeichert.

---

## Fahrtzwecke

**Admin → Fahrtenbuch → Fahrtzwecke** (`/admin/fahrtenbuch/zwecke`)

Zwecke bestimmen, welche Zusatzfelder im Erfassungsformular erscheinen und in welche Kategorie (Einsatz / Übung / Sonstige) eine Fahrt eingeordnet wird.

### Neuen Zweck anlegen

| Feld | Pflicht | Beschreibung |
|------|:-------:|-------------|
| **Name** | ✓ | Bezeichnung, z. B. „Übungsfahrt", „Einsatzfahrt B" |
| **Kategorie** | ✓ | `Einsatz` / `Übung` / `Sonstige` — steuert Statistik-Zuordnung |
| **Reihenfolge** | | Sortierziffer (kleiner = weiter oben) |
| **Ausbildner erforderlich** | | Zeigt Ausbildner-Autocomplete im Formular |
| **GK erforderlich** | | Zeigt Gruppenkommandant-Autocomplete im Formular |

> **Kategorie „Einsatz"**: Zusätzlich erscheint eine Auswahl der letzten Einsätze (48-h-Fenster), damit die Fahrt direkt mit einem laufenden Einsatz verknüpft werden kann.

Bestehende Zwecke können über das Bearbeiten-Symbol umbenannt, deaktiviert oder gelöscht werden. Deaktivierte Zwecke erscheinen nicht mehr im Erfassungsformular, bleiben aber in historischen Fahrten erhalten.

---

## Zielorte

**Admin → Fahrtenbuch → Zielorte** (`/admin/fahrtenbuch/zielorte`)

Vordefinierte Zielorte, die im Erfassungsformular als Dropdown erscheinen. Ergänzend gibt es immer ein Freitextfeld für nicht gelistete Ziele.

| Feld | Beschreibung |
|------|-------------|
| **Name** | Bezeichnung, z. B. „Einsatzgebiet", „Übungsgelände Lauterach" |
| **Reihenfolge** | Sortierziffer |

---

## Token und QR-Codes

**Admin → Fahrtenbuch → Token/QR** (`/admin/fahrtenbuch/token`)

### Organisations-Token

Der Org-Token ermöglicht die Fahrterfassung **ohne Benutzer-Login** (z. B. auf einem Tablet im Feuerwehrhaus oder im Fahrzeug).

1. **Token generieren** — erzeugt einen zufälligen Token-Link: `https://einsatz.example.com/f/{token}`
2. Den Link ausdrucken, als QR-Code anzeigen oder per Messenger teilen.
3. Jeder mit diesem Link kann Fahrten für die eigene Organisation erfassen.

> **Token rotieren** macht den alten Link sofort ungültig. Alle ausgegebenen Links müssen danach neu verteilt werden. Nur rotieren, wenn der Token kompromittiert wurde.

### Fahrzeug-QR-Codes

Unter **Admin → Fahrtenbuch → Fahrzeuge** kann je Fahrzeug ein QR-Code als PNG heruntergeladen werden. Dieser QR-Code öffnet das Formular mit dem Fahrzeug bereits vorausgewählt.

**Empfohlene Vorgehensweise:**
1. Org-Token generieren (Voraussetzung für Fahrzeug-QR-Codes).
2. Für jedes Fahrzeug den QR-Code herunterladen.
3. QR-Code laminiert im Fahrzeug anbringen (Armaturenbrett, Ablage).
4. Besatzung scannt nach der Rückkehr den QR-Code und erfasst die Fahrt direkt.

---

## Allgemeine Einstellungen

**Admin → Fahrtenbuch → Einstellungen** (`/admin/fahrtenbuch/einstellungen`)

### Schadensmeldung (org-weit)

Wird bei jeder Fahrt mit aktiviertem Schadensflag ausgelöst, sofern kein fahrzeugspezifischer Override gesetzt ist.

| Feld | Beschreibung |
|------|-------------|
| **Schaden-E-Mail** | E-Mail-Adresse des Gerätewarts / der Gerätewartung |
| **Teams-Webhook** | Incoming-Webhook-URL für einen Microsoft-Teams-Channel |

Beide Felder sind optional. Wenn beide gesetzt sind, werden beide Kanäle gleichzeitig benachrichtigt.

### Doppelfahrt-Erkennung

| Feld | Standard | Beschreibung |
|------|:--------:|-------------|
| **Zeitfenster (Minuten)** | 10 | Wird für dasselbe Fahrzeug innerhalb dieses Zeitraums eine weitere Fahrt erfasst, erscheint ein Warnhinweis im Formular |

Der Warnhinweis ist kein Blocker — der Benutzer kann mit einer Bestätigungscheckbox fortfahren.

---

## Fahrten-Verwaltung

**Menü → Fahrtenbuch → Fahrten** (`/verwaltung/fahrten`)

Chronologische Liste aller Fahrten der Organisation. Statusbadge:

| Status | Bedeutung |
|--------|----------|
| **aktiv** | Reguläre Fahrt |
| **ersetzt** | Durch eine Korrektur überschrieben |
| **storniert** | Manuell storniert |

### Fahrten-Detail

Klick auf eine Fahrt öffnet die Detailansicht (`/verwaltung/fahrten/{id}`).

Dort sind alle erfassten Felder sichtbar, inklusive:
- Schadensmeldungs-Benachrichtigungsprotokoll (Kanal, Empfänger, Status, Zeitstempel)
- Revisionskette (Korrektur-Verknüpfung)

### Aktionen (nur bei Status „aktiv")

| Aktion | Beschreibung |
|--------|-------------|
| **Korrigieren** | Erstellt eine neue Fahrt als Korrektur; die ursprüngliche wird auf „ersetzt" gesetzt. Zählerstände werden neu berechnet. |
| **Statistik de-/aktivieren** | Schaltet das „nicht statistikrelevant"-Flag um. |
| **Stornieren** | Setzt die Fahrt auf „storniert". Zählerstände werden auf den höchsten verbleibenden aktiven Wert zurückgesetzt. Ein Storno-Grund ist anzugeben. |
| **Schaden erneut senden** | Löst die Schadensmeldungs-Benachrichtigung erneut aus (wenn der erste Versand fehlschlug). |

### Korrektur-Workflow

1. Fahrt öffnen → **Korrigieren**
2. Formular wird mit den Original-Werten vorausgefüllt
3. Korrekte Werte eingeben und speichern
4. Die Korrektur erhält eine neue Fahrt-ID; die Originalfahrt zeigt einen Link auf die Korrektur
5. Zählerstände werden automatisch neu berechnet

---

## Schadensmeldung – Technischer Ablauf

Wenn eine Fahrt mit aktivierter Schadensangabe gespeichert wird:

1. Benachrichtigung wird an konfigurierte E-Mail-Adresse und/oder Teams-Webhook gesendet.
2. Jeder Versuch wird in der Tabelle `fahrt_benachrichtigung` protokolliert (Kanal, Empfänger, Status, Fehlertext).
3. Fehlgeschlagene Versuche können in der Detailansicht über **„Erneut senden"** wiederholt werden.

Der Benachrichtigungs-Status ist in der Detailansicht sichtbar.
