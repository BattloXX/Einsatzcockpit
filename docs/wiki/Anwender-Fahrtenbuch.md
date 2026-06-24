# Fahrtenbuch

← [Zurück zur Startseite](Home)

> URL: `/fahrtenbuch/neu`  
> Zugänglich für: alle angemeldeten Benutzer sowie anonymer Zugriff via Token/QR-Code

Das Fahrtenbuch erfasst jede Ausfahrt eines Fahrzeugs digital — Maschinist, Kilometerstand, Betriebsstunden, Seilwinde, Zielort, Zweck und optionale Schadensangabe. Alle Daten werden sofort gespeichert und sind in der Fahrten-Verwaltung abrufbar.

---

## Erfassungsformular

**Menü → Fahrtenbuch → Neue Fahrt** oder direkt `/fahrtenbuch/neu`.

### Schritt 1 – Fahrzeug wählen

Dropdown mit allen aktiven, eigenen Fahrzeugen der Organisation (keine Ad-hoc- oder Fremdfahrzeuge).

> **Doppelfahrt-Warnung**: Wurde für dasselbe Fahrzeug innerhalb des konfigurierten Zeitfensters (Standard: 10 Minuten) bereits eine Fahrt erfasst, erscheint ein gelber Warnhinweis. Die Erfassung kann trotzdem fortgesetzt werden (Bestätigungscheckbox).

### Schritt 2 – Fahrzeugspezifische Felder

Nach der Fahrzeugauswahl erscheinen automatisch die für dieses Fahrzeug relevanten Felder:

| Feld | Wann sichtbar | Beschreibung |
|------|:-------------:|-------------|
| **Maschinist** | immer | Autocomplete — Name tippen, Mitglied aus der Liste wählen |
| **km-Stand** | wenn km-Erfassung aktiv | Neuer km-Stand (Gesamtstand, nicht Differenz) |
| **Betriebsstunden** | wenn BH-Erfassung aktiv | Neuer BH-Stand (z. B. `1234.5`) |
| **2. Maschinist** | wenn 2. Maschinist konfiguriert | Autocomplete (optional) |
| **Seilwinde** | wenn Seilwinden-Abfrage aktiv | Eigener Abschnitt — siehe unten |

#### Maschinist-Suche

Im Feld „Maschinist" mindestens 2 Zeichen des Nachnamens oder Vornamens eingeben. Die Trefferliste erscheint automatisch — per Tippen auf den Namen auswählen. Der Name wird gespeichert, auch wenn das Mitglied später deaktiviert wird.

#### km-Stand / Betriebsstunden

Beide Felder erwarten den **aktuellen Zählerstand** (nicht den gefahrenen Wert). Das System berechnet den Delta automatisch.

> **Warnhinweis bei großem Sprung**: Überschreitet der Delta die konfigurierte Warnschwelle (Standard: 50 km / 10 BH), erscheint ein Warnhinweis. Mit einem Häkchen bestätigen und erneut speichern.

### Schritt 3 – Seilwinde (sofern vorhanden)

Wenn das gewählte Fahrzeug mit Seilwinden-Abfrage konfiguriert ist, erscheint der Seilwinden-Abschnitt:

| Feld | Beschreibung |
|------|-------------|
| **Seilwinden-BH-Stand** | Betriebsstunden der Seilwinde (aktueller Gesamtstand) |
| **Seilwinden-Bediener** | Autocomplete — Name des Bedieners |
| **Anzahl Züge** | Wie viele Seilwinden-Züge wurden durchgeführt (Zahl, optional) |
| **Seilwinden-Wartung durchgeführt?** | Ja / Nein (optional) |

### Schritt 4 – Zielort und Zweck

| Feld | Beschreibung |
|------|-------------|
| **Zielort** | Dropdown mit hinterlegten Zielorten — oder „Freitext" für einen eigenen Eintrag |
| **Zweck** | Dropdown: Übung, Einsatz, sonstige Kategorie |

### Schritt 5 – Zweckspezifische Felder

Je nach gewähltem Zweck erscheinen zusätzliche Pflichtfelder:

| Feld | Wann | Beschreibung |
|------|------|-------------|
| **Einsatz-Verknüpfung** | Zweck = Einsatz | Auswahl eines der letzten 20 Einsätze (48-h-Fenster) |
| **Ausbildner** | Zweck erfordert Ausbildner | Autocomplete |
| **Gruppenkommandant** | Zweck erfordert GK | Autocomplete |

### Schritt 6 – Optionale Felder

| Feld | Beschreibung |
|------|-------------|
| **Schadensangabe** | Checkbox „Schaden vorhanden" — öffnet Unterfelder für Betriebsfähigkeit und Schadensbeschreibung |
| **Bemerkung** | Freitext |
| **Nicht statistikrelevant** | Checkbox — schließt diese Fahrt aus dem Statistik-Dashboard aus |

---

## Erfassung abschließen

Auf **Speichern** tippen. Bei erfolgreicher Speicherung erscheint eine Bestätigungsseite mit der Fahrt-ID. Von dort aus kann direkt eine neue Fahrt gestartet werden.

---

## Token-/QR-Code-Zugang

Das Fahrtenbuch ist auch **ohne App-Login** erreichbar — z. B. für eine Tablet-Station im Feuerwehrhaus.

### Org-Token-Link

Der Org-Admin generiert unter **Admin → Fahrtenbuch → Token/QR** einen Token-Link. Dieser Link (`/f/{token}`) öffnet das Formular für die eigene Organisation, ohne dass ein Benutzer-Login erforderlich ist.

### Fahrzeug-QR-Code

Für jedes Fahrzeug kann ein individueller QR-Code generiert werden. Dieser QR-Code (`/f/{token}/v/{qr_token}`) öffnet das Formular mit dem entsprechenden Fahrzeug bereits vorausgewählt.

**Hinweis:** Den QR-Code ausdrucken und im/am Fahrzeug anbringen — so kann die Besatzung die Fahrt direkt nach der Rückkehr per Smartphone erfassen.

---

## Erfasste Fahrten einsehen

Angemeldete Benutzer können unter **Menü → Fahrtenbuch → Fahrten** (`/verwaltung/fahrten`) alle Fahrten der eigenen Organisation chronologisch einsehen.

Auf jede Fahrt klicken, um die Detailansicht zu öffnen. Dort sind alle erfassten Werte, Statusangaben und — sofern vorhanden — Schadensmeldungs-Benachrichtigungen sichtbar.

---

## Häufige Fragen

**Ich habe einen falschen km-Stand eingetragen — was tun?**  
Die Fahrt kann in der Verwaltung (`/verwaltung/fahrten/{id}`) unter **Korrigieren** neu erfasst werden. Die ursprüngliche Fahrt wird als „ersetzt" markiert; Zählerstände werden automatisch neu berechnet.

**Das Fahrzeug erscheint nicht in der Auswahlliste.**  
Nur aktive, eigene Fahrzeuge (keine Ad-hoc- und keine Fremdfahrzeuge) werden angezeigt. Wendet euch an den Org-Admin, um die Fahrzeugkonfiguration zu prüfen.

**Warum sehe ich keine Seilwinden-Felder?**  
Die Seilwinden-Abfrage muss vom Org-Admin je Fahrzeug aktiviert werden (Admin → Fahrtenbuch → Fahrzeuge → entsprechendes Fahrzeug → „Seilwinde erfassen").
