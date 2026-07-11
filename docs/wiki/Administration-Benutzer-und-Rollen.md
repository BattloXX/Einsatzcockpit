# Benutzer und Rollen

← [Zurück zur Startseite](Home)

## Benutzer verwalten

**Admin** → **Benutzer**

### Neuen Benutzer anlegen

**+ Neuer Benutzer** → Formular:

| Feld | Beschreibung |
|------|-------------|
| Benutzername | Eindeutig, für Login (z.B. `stefan.m`) |
| Anzeigename | Wird im Board und PDF angezeigt |
| Passwort | Min. 8 Zeichen |
| Aktiv | Deaktivierte Benutzer können sich nicht einloggen |

Nach Anlage: Rollen zuweisen (siehe unten).

### Passwort zurücksetzen

Benutzer in der Liste → **Passwort zurücksetzen** → neues Passwort eingeben → **Speichern**

Oder per CLI (für Admin-Passwort ohne Login):
```bash
python -m app.cli reset-password --username admin --password neues-passwort
```

### Benutzer deaktivieren

Benutzer in der Liste → **Deaktivieren** → Bestätigen.
Der Benutzer kann sich nicht mehr einloggen. Alle historischen Einträge (Audit-Log, Einsätze) bleiben ihm zugeordnet.

## Rollen

### Rollenbeschreibung

| Rolle | Code | Beschreibung |
|-------|------|-------------|
| **Systemadministrator** | `system_admin` | Organisationsübergreifend, Zugriff auf alle Organisationen und die System-Konsole. Besteht **jede** Rollenprüfung automatisch, unabhängig von den übrigen Rollen. |
| **Administrator** | `admin` | Vollzugriff innerhalb der eigenen Organisation. Historischer Rollenname, funktional identisch mit `org_admin`. |
| **Organisations-Administrator** | `org_admin` | Vollzugriff innerhalb der eigenen Organisation. |
| **Fahrtenbuch-Administrator** | `fahrtenbuch_admin` | Fahrtenbuch-Verwaltung (Korrektur, Storno, Stammdaten) der eigenen Org, ohne sonstige Admin-Rechte. |
| **Einsatzleiter** | `incident_leader` | Einsatz/Großschadenslage führen, Ressourcen, Aufträge und Meldungen steuern. |
| **Objektverwalter** | `objekt_verwalter` | Objekte, Dokumente und Objekt-Lagekarten pflegen und freigeben. |
| **AS-Überwacher** | `breathing_supervisor` | Atemschutzüberwachung — im Atemschutz-Modul gleichberechtigt mit Einsatzleiter/Bearbeiter, außerhalb davon ohne besondere Rechte. |
| **Bearbeiter** | `recorder` | Erfasst Einträge, Meldungen, Ressourcen im laufenden Einsatz — aber keine Leitungsaktionen (Einsatz/Lage anlegen, abschließen, wiedereröffnen). |
| **Nur Lesen** | `readonly` | Rein lesender Zugriff; darf zusätzlich Journal-/Log-Notizen ergänzen. |

Ein Benutzer kann **mehrere Rollen gleichzeitig** haben (z.B. `incident_leader` + `breathing_supervisor`).
`system_admin` und `org_admin`/`admin` sind Multi-Tenancy-Rollen (ab v2.2.0). Der erste Admin-User der Organisation erhält automatisch `admin` + `org_admin`.

### Wie die Rollenprüfung funktioniert

- Jede Berechtigungsprüfung lässt zusätzlich zu den genannten Rollen immer auch `admin`/`org_admin` zu — unabhängig davon, ob diese in der jeweiligen Prüfung explizit aufgeführt sind.
- `system_admin` besteht **jede** Prüfung automatisch, auch wenn die Rolle dort nicht gelistet ist.
- `admin` und `org_admin` sind daher überall **funktional gleichwertig** — in der Matrix unten deshalb als eine Spalte geführt.
- Manche Aktionen (System-Konsole, Organisationen verwalten, Seiten-Editor, dauerhaftes Löschen von Fahrtenbuch-Einträgen, LIS-Rohdaten-Diagnose) prüfen **ausschließlich** `system_admin`, ohne die übliche Admin-Ausnahme — diese sind in der Matrix mit „nur System-Admin" markiert.

### Berechtigungsmatrix

✓ = erlaubt · – = nicht erlaubt. Spalte **org_admin/admin** deckt beide Rollencodes ab (siehe oben).

| Funktion | system_admin | org_admin/admin | fahrtenbuch_admin | incident_leader | objekt_verwalter | breathing_supervisor | recorder | readonly |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **— Einsatzführung / Kanban-Board —** | | | | | | | | |
| Einsatz anlegen (inkl. Übungsmodus) | ✓ | ✓ | – | ✓ | – | – | – | – |
| Einsatz abschließen | ✓ | ✓ | – | ✓ | – | – | – | – |
| Einsatz wiedereröffnen | ✓ | ✓ | – | – | – | – | – | – |
| Ressourcen zuweisen/verschieben | ✓ | ✓ | – | ✓ | – | – | ✓ | – |
| Aufträge/Meldungen anlegen/bearbeiten | ✓ | ✓ | – | ✓ | – | – | ✓ | – |
| Personen im Einsatz erfassen | ✓ | ✓ | – | ✓ | – | – | ✓ | – |
| Journal-/Log-Notiz ergänzen | ✓ | ✓ | – | ✓ | – | – | ✓ | ✓ |
| QR-Code-/PIN-Gästezugang einrichten | ✓ | ✓ | – | ✓ | – | – | – | – |
| KI-Einsatzbericht erzeugen/speichern | ✓ | ✓ | – | ✓ | – | – | ✓ | – |
| Archiv/PDF einsehen & herunterladen | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Einsatz/Archiveintrag dauerhaft löschen | ✓ (nur System-Admin*) | – | – | – | – | – | – | – |
| **— Großschadenslage (GSL/Stab) —** | | | | | | | | |
| Lage einsehen | ✓ | ✓ | – | ✓ | – | – | ✓ | ✓ |
| Lage bearbeiten (Kräfte, Aufträge, Kontrollen) | ✓ | ✓ | – | ✓ | – | – | ✓ | – |
| Lage anlegen / abschließen | ✓ | ✓ | – | ✓ | – | – | – | – |
| Lage wiedereröffnen | ✓ | ✓ | – | – | – | – | – | – |
| **— Atemschutzüberwachung —** | | | | | | | | |
| Atemschutz überwachen (Trupp, Druck, Meldung) | ✓ | ✓ | – | ✓ | – | ✓ | ✓ | – |
| Atemschutz-Prüfungsstammdaten pflegen | ✓ | ✓ | – | – | – | – | – | – |
| **— Fahrtenbuch —** | | | | | | | | |
| Fahrt erfassen | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (auch ohne Login per QR/Token) |
| Fahrtenbuch verwalten (Korrektur, Storno, Stammdaten) | ✓ | ✓ | ✓ | – | – | – | – | – |
| Fahrten dauerhaft löschen | ✓ (nur System-Admin) | – | – | – | – | – | – | – |
| **— Objektverwaltung —** | | | | | | | | |
| Objekte/Dokumente/Objekt-Lagekarte einsehen | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Objekte/Dokumente/Objekt-Lagekarte bearbeiten | ✓ | ✓ | – | – | ✓ | – | – | – |
| Objekt löschen / Kataloge (Kategorien, Gefahren) pflegen | ✓ | ✓ | – | – | – | – | – | – |
| Einsatz-Objekt-Verknüpfung bestätigen | ✓ | ✓ | – | ✓ | ✓ | – | – | – |
| **— Geräteverleih —** | | | | | | | | |
| Ausleihen/Rückgabe (PIN/SMS) | ✓ | ✓ | – | – | – | – | ✓ | – |
| Artikelstammdaten pflegen | ✓ | ✓ | – | – | – | – | – | – |
| **— Drohne/UAS —** | | | | | | | | |
| Flugbetrieb, Checklisten, Einsatz-Verknüpfung | ✓ | ✓ | – | – | – | – | ✓ | – |
| Geräte-/Piloten-Stammdaten pflegen | ✓ | ✓ | – | – | – | – | – | – |
| **— Wetter —** | | | | | | | | |
| Wetter-Panels einsehen | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Wetterstation/Warnungen konfigurieren | ✓ | ✓ | – | – | – | – | – | – |
| **— Mannschaftsregister —** | | | | | | | | |
| Mitglieder-Stammdaten pflegen (inkl. Excel-Import) | ✓ | ✓ | – | – | – | – | – | – |
| **— Verwaltung (Admin-Bereich) —** | | | | | | | | |
| Benutzer/Rollen verwalten | ✓ | ✓ | – | – | – | – | – | – |
| Geräte-Login (Device-Tokens) | ✓ | ✓ | – | – | – | – | – | – |
| Push-Nachrichten, API-Keys, Lagekarte-Tokens | ✓ | ✓ | – | – | – | – | – | – |
| Audit-Log einsehen | ✓ | ✓ | – | – | – | – | – | – |
| SMS senden / SMS-Empfang & Weiterleitung | ✓ | ✓ | – | – | – | – | – | – |
| Stammdaten (Fahrzeuge, Qualifikationen, Alarmstichwörter) | ✓ | ✓ | – | – | – | – | – | – |
| Teams-Alarmierung, SSO, LIS-Konfiguration, Wasserstellen | ✓ | ✓ | – | – | – | – | – | – |
| Organisationseinstellungen | ✓ | ✓ | – | – | – | – | – | – |
| Organisationen verwalten (Multi-Tenancy) | ✓ (nur System-Admin) | – | – | – | – | – | – | – |
| System-Konsole (Update, Backup, Quotas, Server-Log) | ✓ (nur System-Admin) | – | – | – | – | – | – | – |
| Landingpage/Seiten-Editor (CMS) | ✓ (nur System-Admin) | – | – | – | – | – | – | – |
| LIS-Rohdaten-Diagnose | ✓ (nur System-Admin) | – | – | – | – | – | – | – |

\* Technischer Hinweis: Bei zwei als „nur System-Admin" gedachten Aktionen (dauerhaftes Löschen von Einsätzen/Archiveinträgen) greift intern eine allgemeine Rollenprüfung, die aktuell auch `org_admin`/`admin` durchlässt. Organisatorisch sollten diese Aktionen dennoch System-Administratoren vorbehalten bleiben.

### Rollen zuweisen

Benutzer in der Liste → **Rollen** → gewünschte Rollen aktivieren → **Speichern**

Ein Benutzer kann mehrere Rollen haben (z.B. `incident_leader` + `breathing_supervisor`).

## Hinweise

- Der erste Admin-User wird automatisch beim App-Start aus `.env` (`BOOTSTRAP_ADMIN_*`) angelegt und erhält die Rollen `admin` und `org_admin`.
- Mindestens ein aktiver Admin-User muss immer vorhanden sein.
- Die Anzahl der Benutzer ist nicht begrenzt.
- Benutzer ohne `org_id` (NULL) sind System-Administratoren und sehen alle Organisationen.
- `org_admin` kann Einladungen an neue Org-Admins versenden: [Organisationen verwalten](Administration-Organisations-verwalten).
- Zwei Funktionen sind bewusst **nicht** rollenbasiert, sondern über eigene, anonyme Mechanismen geregelt: der **QR-/PIN-Gästezugang** zu einem Einsatz (signierter Einmal-Token bzw. ratenbegrenzte PIN, kein Login) und die **Fahrtenbuch-Erfassung per öffentlichem Link/QR** (für Besatzungsmitglieder ohne eigenen Account).
- Geräte-Logins (fest gekoppelte Tablets/Handys, z.B. für Board-Anzeige oder SMS-Gateway) sind keine „Rolle" im obigen Sinn, sondern ein eigener Login-Mechanismus über Geräte-Token/PIN: [Geräteverleih](Anwender-Geraeteverleih), [SMS-Gateway installieren](Installation-SMS-Gateway).
