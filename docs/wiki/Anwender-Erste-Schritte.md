# Erste Schritte

← [Zurück zur Startseite](Home)

## Login

URL: `https://einsatzcockpit.com/login`

Benutzername und Passwort eingeben → **Anmelden**.

Bei Problemen: [Troubleshooting](Installation-Troubleshooting)

## Übersicht der Bereiche

Nach dem Login erscheint die **Übersicht** mit den laufenden Einsätzen und darunter den Kacheln
aller Module, die für dich freigeschaltet sind.

**Kopfzeile am PC:** Suche · Konsole · Lage · **Module ▾** · Wetter · Archiv, rechts Verwaltung
(nur Admins), App installieren und dein Profil (Profil bearbeiten, Hilfe, Benutzer wechseln, Abmelden).

**Am Handy:** Die Leiste unten führt zu Übersicht, Lage, neuem Einsatz, **Menü** und Profil. Im
Menü stehen alle Bereiche und Module, im Profil die Kontoaktionen.

Die Module sind im Menü und auf den Kacheln gleich geordnet, zuerst das, was man im Einsatz braucht:

| Gruppe | Module |
|---|---|
| **Im Einsatz** | [Katastrophenpläne](Anwender-Katastrophenplaene), [Nachschlagewerke](Anwender-Nachschlagewerke), [Kontakte](Anwender-Kontaktverwaltung), [Straßensperren](Anwender-Strassensperren), [Förderstrecke](Anwender-Foerderstrecken-Planer) |
| **Einsatzbegleitend** | [Fahrtenbuch](Anwender-Fahrtenbuch), [Atemschutz-Geräteprüfung](Anwender-Atemschutz-Geraetepruefung), [Drohne / UAS](Anwender-Drohne-UAS) |
| **Verwaltung & Planung** | [Objekte](Anwender-Objekte), [Probenplan](Anwender-Probenplan), [Planspiele](Anwender-Planspiele), [Mailing](Anwender-Mailing-Kampagnen) |
| **Auswertung** | [Statistik](Administration-Statistik-Dashboard) |

Welche Module du siehst, hängt davon ab, was deine Organisation aktiviert hat und welche Rollen du
hast. Ein laufender Einsatz öffnet sich mit einem Klick auf seine Kachel im
[Einsatzboard](Anwender-Kanban-Board-bedienen); die [Atemschutzüberwachung](Anwender-Atemschutzueberwachung)
ist dort ein eigener Reiter.

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
