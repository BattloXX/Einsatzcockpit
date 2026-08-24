# Erste Schritte

← [Zurück zur Startseite](Home)

## Login

URL: `https://einsatzleiter.feuerwehr-wolfurt.at/login`

Benutzername und Passwort eingeben → **Anmelden**.

Bei Problemen: [Troubleshooting](Installation-Troubleshooting)

## Übersicht der Bereiche

Nach dem Login erscheint die **Startseite** mit:

| Bereich | Erreichbar über | Beschreibung |
|---------|----------------|-------------|
| **Aktive Einsätze** | Startseite / `/` | Alle laufenden Einsätze als Kacheln |
| **Einsatz-Board** | Klick auf Einsatz | Kanban-Board mit Fahrzeugen und Aufträgen |
| **Atemschutz** | Board → Tab „Atemschutz" | AS-Überwachung für diesen Einsatz |
| **Archiv** | Menü → Archiv | Abgeschlossene Einsätze, PDF-Download |
| **Statistik** | Menü → Statistik | Auswertungen (nur Admin/EL) |
| **Admin** | Menü → Admin | Benutzer, Fahrzeuge, API-Keys (nur Admin) |

## Rollen und was sie dürfen

| Rolle | Kann sehen | Kann ändern |
|-------|------------|-------------|
| **Admin** | Alles | Alles |
| **Einsatzleiter** | Alles | Einsatz, Atemschutz |
| **AS-Überwacher** | Alles | Nur Atemschutz |
| **Schriftführer** | Alles | Journal und Meldungen |
| **Readonly** | Alles | Nichts |

## Tastatur-Shortcuts

| Shortcut | Aktion |
|----------|--------|
| `Strg + M` | Neue Meldung |
| `Strg + A` | Neuen Auftrag |
| `Strg + U` | Neuen Umstand / Notiz |
| `Strg + B` | Neuen Einsatz beginnen (nur Startseite) |

## Dark Mode / Light Mode

Über den Schalter oben rechts in der Navigation umschalten.

## Auf mehreren Geräten gleichzeitig

Mehrere Benutzer können denselben Einsatz auf verschiedenen Geräten gleichzeitig öffnen. Alle Änderungen werden in Echtzeit synchronisiert (WebSockets). Es ist kein Reload nötig.

## QR-Code für schnellen Zugriff

Im laufenden Einsatz: **QR-Code anzeigen** → mit einem zweiten Gerät scannen → direkt eingeloggt ohne Passwort.  
Details: [QR-Code Schnellzugriff](Anwender-QR-Code-Schnellzugriff)
