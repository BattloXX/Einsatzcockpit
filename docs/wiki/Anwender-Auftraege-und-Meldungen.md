# Aufträge und Meldungen

← [Zurück zur Startseite](Home)

## Aufträge

### Auftrag anlegen

**Option A — Sidebar:**
1. Sidebar öffnen (links, auf Tablet: Drawer-Symbol)
2. **+ Auftrag** klicken (oder `Strg + A`)
3. Titel eingeben, optional Fahrzeug zuweisen
4. **Speichern**

**Option B — Direkt am Fahrzeug:**
1. Fahrzeug-Karte anklicken
2. **+ Auftrag** im Detailbereich
3. Titel eingeben → **Speichern**

**Option C — Sprachdiktat:**
1. Mikrofon-Symbol neben dem Auftragsfeld
2. Laut sprechen: z.B. „Lage erkunden, Zugang sichern"
3. Erkannter Text erscheint im Feld
4. `Enter` oder **Speichern** drücken

### Auftrag einem Fahrzeug zuweisen

- Auftrag in Auftrags-Spalte → auf Fahrzeug-Karte ziehen, **oder**
- Auftrag anklicken → **Fahrzeug** Dropdown → Auswahl → **Speichern**

### Auftrag erledigen

Auftrag anklicken → **✓ Erledigt** → Auftrag wird archiviert, Fahrzeug-Ampel aktualisiert sich.

### Auftrag stornieren

Auftrag anklicken → **Stornieren** → Auftrag bleibt sichtbar aber durchgestrichen.

## Meldungen (zeitgesteuert)

### Was sind Meldungen?

Meldungen sind Erinnerungen, die zu einem bestimmten Zeitpunkt als Pop-up erscheinen. Sie können sich auf die Lage beziehen (z.B. „Status an Leitstelle melden") oder operativ sein (z.B. „Trinkwasser für Mannschaft").

**Automatische Meldungen** (werden beim Einsatzstart angelegt):
- **5 Minuten:** Erste Lagemeldung an Leitstelle
- **10 Minuten:** Zweite Statusmeldung

### Neue Meldung anlegen

Sidebar → **+ Meldung** (oder `Strg + M`)

- **Titel**: Kurztext (erscheint im Pop-up)
- **Detail**: Optionaler Freitext
- **Fällig in**: Minuten ab jetzt, oder absolute Uhrzeit

### Pop-up-Verhalten

Wenn eine Meldung fällig ist, erscheint ein **zentrales Modal** — nicht wegklickbar ohne Entscheidung:
- **Erledigt**: Meldung wird abgehakt
- **Verschieben**: Neue Fälligkeit eingeben

### Sprachdiktat für Meldungen

Wie bei Aufträgen: Mikrofon-Symbol neben dem Meldungsfeld.  
Die Web Speech API nutzt das Gerät-Mikrofon lokal — keine Daten verlassen das Gerät.

### Sprachdiktat — Voraussetzungen

| Browser | Unterstützung |
|---------|--------------|
| Chrome / Edge | ✓ Vollständig |
| Safari (iOS 17+) | ✓ Vollständig |
| Firefox | ✗ Nicht unterstützt |

Bei nicht unterstützten Browsern erscheint ein Hinweis-Icon statt des Mikrofon-Buttons.
