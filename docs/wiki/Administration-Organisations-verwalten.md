# Organisationen verwalten (Multi-Org)

← [Zurück zur Startseite](Home)

> Verfügbar ab **Version 2.2.0**. Organisations-Verwaltung erfordert die Rolle `system_admin`, eigene Org-Einstellungen erfordern `org_admin` oder `admin`.

## Konzept

In einer Instanz dieser App können mehrere Feuerwehren (Organisationen) verwaltet werden. Jede Organisation:

- hat eigene Benutzer, Mitglieder, Fahrzeuge und Alarm-Stichwörter
- kann ihre eigene Org selbst verwalten (Org-Admin)
- kann bei Einsätzen als Kollaborator hinzugefügt werden
- hat eigene KI-Einstellungen, Speicherkontingent und Auto-Schließ-Parameter

## Organisations-Übersicht aufrufen

**Admin-Menü** → **Organisationen** (nur `system_admin`)  
URL: `/admin/system/orgs`

Die System-Konsole zeigt KPIs pro Org: aktive Einsätze, Benutzer, Speicherverbrauch.

## Neue Organisation anlegen

**+ Neue Organisation** → Formular:

| Feld | Beschreibung |
|------|-------------|
| Kürzel (slug) | Eindeutig, nur Kleinbuchstaben/Ziffern/Bindestriche (z.B. `lauterach`) |
| Name | Vollständiger Ortsname (z.B. `FF Lauterach`) |
| Farbe | Hex-Farbcode — wird als linker Streifen auf Fahrzeugkarten angezeigt |
| Short-Code | Bis zu 3 Zeichen (z.B. `LAU`) — für kompakte Anzeigen |
| Kontakt E-Mail | Optional |
| Seed-Profil | Vorlagen-Satz für Stammdaten (Fahrzeuge, Stichwörter, Vorschläge) |

### Seed-Profile

Nach dem Anlegen können Stammdaten aus einem vordefinierten Profil eingespielt werden. Verfügbare Profile stehen in der Tabelle `seed_template` und werden über `Admin → Stammdaten → Seed einspielen` angewendet. Das Profil `wolfurt` enthält die Wolfurter Fahrzeuge, Alarmstichwörter und Auftragsvorschläge.

## Org-Admin einrichten

### Via Einladungslink (empfohlen)

1. Im Benutzer-Panel: **Org-Admin-Einladung versenden**
2. E-Mail-Adresse des künftigen Admins eingeben
3. Die Person erhält einen Einladungslink per E-Mail
4. Nach Klick auf den Link: selbst Benutzer und Passwort wählen — Rolle `org_admin` wird automatisch vergeben

### Manuell

1. `/admin/benutzer` → **+ Neuer Benutzer**
2. `org_id` der neuen Organisation auswählen
3. Rolle `org_admin` zuweisen

Org-Admins können sich einloggen und ihre Organisation selbst verwalten (Mitglieder, Fahrzeuge, Einstellungen, eigene Einladungen verschicken), haben aber keinen Zugriff auf andere Organisationen.

## Rollen und Zugriff

| Rolle | Zugriff |
|-------|---------|
| `system_admin` | Alle Organisationen, alle Einsätze, System-Einstellungen, System-Konsole |
| `org_admin` / `admin` | Nur eigene Organisation |
| Andere Rollen | Eigene Org + Einsätze, an denen ihre Org beteiligt ist |

## Multi-Org-Einsatz (Kollaboration)

Wenn Org A einen Einsatz erstellt, kann sie Org B als Kollaborator hinzufügen:

1. Im Einsatz-Board: **Org hinzufügen** (Einsatzleiter oder Admin)
2. Org aus Liste auswählen
3. Benutzer von Org B sehen den Einsatz sofort (WebSocket-Benachrichtigung)
4. Fahrzeuge von Org B sind im Board sichtbar (mit Org-B-Farbe)

Kollaborative Einsätze zählen im Statistik-Dashboard der beteiligten Org mit. In der System-Konsole werden sie aber nur unter `primary_org_id` gezählt.

## Organisation deaktivieren

In der Organisations-Liste → **Deaktivieren**

Deaktivierte Orgs können sich nicht einloggen, ihre Daten bleiben erhalten. Die Heimwehr kann nicht deaktiviert werden.

## Heimwehr

Die Organisation, die die App-Instanz betreibt, ist als **Heimwehr** markiert (`is_home_org = true`). Sie kann nicht deaktiviert werden und ist bei der API-Einsatzanlage ohne explizite Org-Angabe die Standard-Organisation.

## Speicherkontingent

Pro Org kann ein Speicherlimit für Medien-Uploads festgelegt werden:

**Organisations-Details** → **Speicherkontingent** → Wert in MB/GB eingeben  
NULL = unbegrenzt (Standard)

Der aktuelle Verbrauch ist in der System-Konsole sichtbar.
