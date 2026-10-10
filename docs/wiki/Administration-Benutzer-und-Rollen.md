# Benutzer und Rollen

← [Zurück zur Startseite](Home)

Die Rollen `objekt_verwalter` und `fahrtenbuch_admin` steuern auch die sichtbaren Werkzeuge des [MCP-Servers](Administration-MCP-Server); Org-Admin und Admin sind jeweils ebenfalls berechtigt.

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
| **— Großschadenslage (GSL/Stab) —** (Details zu Ressourcen, Einheitenmodus und Zugängen: Abschnitt „Großschadenslage: Ressourcen, Einheitenmodus und Zugänge“) | | | | | | | | |
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

## Großschadenslage: Ressourcen, Einheitenmodus und Zugänge

Neben den Benutzerrollen gibt es in der Großschadenslage (GSL) weitere **Zugangsarten**, die nicht über Rollen, sondern über eigene Mechanismen berechtigt werden. Jede Zugangsart sieht und darf nur das, was in den Tabellen unten steht; alle Prüfungen laufen serverseitig und sind auf die eigene Organisation und Lage begrenzt.

### Zugangsarten

| Zugangsart | Wie angemeldet | Sieht/darf grundsätzlich |
|---|---|---|
| **Benutzer mit Rolle** | Login (Passwort/SSO) | Führungsoberfläche je nach Rolle (siehe Tabelle unten). |
| **Einheiten-Tablet** | Gerät mit Profil „Einheit“ (Admin → Geräte-Login), an ein Fahrzeug gekoppelt | Die eigene Einheit: eigene Aufträge, Status, Lagemeldung, Maßnahme, Foto, Personal/Ausstattung der eigenen Einheit. Die Gesamtansicht der Lage nur **lesend** (feste Liste erlaubter Ansichten). |
| **Gerät „Führung“** | Gerät mit Profil „Führung“ (oder ohne Profil) | Verhält sich wie der Geräte-Benutzer mit den bei der Anlage vergebenen Rollen. Die Verwaltung von Zugängen, QR-Codes und Ausdrucken bleibt Geräten grundsätzlich verwehrt. |
| **Gruppenkommandant (SMS-Link)** | Persönlicher Link per SMS/Kopie, Sitzungs-Cookie `ec_gk`, optional SMS-Code | Ausschließlich die **eigene Einheit** im Einheitenmodus; an Person, Telefonnummer und aktuellen Gruppenkommandanten gebunden. |
| **QR-Zugang (Ausdruck)** | Ausgedruckter QR-Code, Cookie `ec_qr`, optional PIN | Ausschließlich die **eigene Einheit**; an die Einheit gebunden, nicht an eine Person. |
| **Admin-Simulation** | Administrator öffnet „Als Einheit ansehen“ | Einheitenansicht einer Einheit. In Übungslagen schreibend, in Echtlagen nur lesend. |
| **MCP (KI-Assistent)** | OAuth-Token eines Benutzers | Rechte des Benutzers; Geräte-Benutzer, GK-Links und QR-Zugänge sind **nie** MCP-berechtigt. |

### Führungsoberfläche: wer darf was?

Spalten: **Admin** = `org_admin`/`admin` (`system_admin` darf zusätzlich organisationsübergreifend), **EL** = `incident_leader`, **Bearb.** = `recorder`, **Lesen** = `readonly`. `breathing_supervisor`, `objekt_verwalter`, `fahrtenbuch_admin` haben in der GSL-Ressourcenverwaltung keine eigenen Rechte. Benutzer mit eingeschränkter Ansicht (Einheiten-Tablet in der Gesamtansicht) sind wie „Lesen“ ohne Karte/Zugang zu behandeln.

| Funktion | Admin | EL | Bearb. | Lesen |
|---|:---:|:---:|:---:|:---:|
| Ressourcenübersicht, Kräfteübersicht, Ressourcenkarte ansehen | ✓ | ✓ | ✓ | ✓ (Mobilnummern maskiert) |
| Einheit hinzufügen (inkl. Gruppenkommandant, Telefon, Stärke) | ✓ | ✓ | ✓ | – |
| Stammdaten der Einheit ändern (Funkruf, Org, BOS, Bereitstellungsraum, Menge) | ✓ | ✓ | ✓ | – |
| Gruppenkommandant/Stellvertreter setzen, wechseln, entfernen | ✓ | ✓ | ✓ | – |
| Einheit disponieren, Auftrag ändern, Einheit abziehen (löst ggf. SMS/Push aus) | ✓ | ✓ | ✓ | – |
| Status der Einheit stellvertretend erfassen („per Funk“) | ✓ | ✓ | ✓ | – |
| Ressourcenjournal ansehen | ✓ | ✓ | ✓ | ✓ |
| Manuellen Journaleintrag erfassen / stornieren | ✓ | ✓ | ✓ | – |
| Personal/Ausstattung pflegen (setzen, hinzufügen, verstärken, ablösen, umbuchen, Vorlage) | ✓ | ✓ | ✓ | – |
| Verband bilden/auflösen, Einheit aufteilen | ✓ | ✓ | ✓ | – |
| Persönlichen Zugang senden, Nachricht/Link kopieren, verlängern, widerrufen, Sitzungen beenden | ✓ | ✓ | ✓ | – |
| QR-Zugang ausstellen, anzeigen (inkl. PIN), drucken, erneuern, widerrufen | ✓ | ✓ | ✓ | – |
| Auftrags-SMS erneut senden | ✓ | ✓ | ✓ | – |
| Lage anlegen / abschließen | ✓ | ✓ | – | – |
| Lage wiedereröffnen | ✓ | – | – | – |
| Journal-/Log-Notiz in der Lage ergänzen | ✓ | ✓ | ✓ | ✓ |
| GSL-Einstellungen (GK-Zugang, QR, Auftrags-SMS, Vorlagen, Limits) | ✓ | – | – | – |
| Notbremse: alle Zugänge der Organisation widerrufen | ✓ | – | – | – |
| Druckregeln (auch „GSL – Neue Einheit / QR-Einheitenzugang“) und Drucker verwalten | ✓ | – | – | – |
| Geräte-Logins und Geräteprofile (Einheit/Führung) verwalten | ✓ | – | – | – |
| Admin-Simulation einer Einheit | ✓ | – | – | – |

**Zugangs-, QR- und Druckfunktionen** sind zusätzlich gesperrt für: Geräte-Logins, Gäste-Sitzungen per QR/PIN der Lage und die eingeschränkte Gesamtansicht des Einheiten-Tablets – auch wenn die Rolle des Geräte-Benutzers sonst „Bearbeiter“ wäre. Zusatzbedingungen: Der Org-Schalter „Zugang aktiv“ bzw. „QR-Zugang aktiv“ muss gesetzt sein; für den Ausdruck braucht es einen gekoppelten, aktiven Drucker der eigenen Organisation. Ausstellen ist nur in einer aktiven Lage und für nicht abgerückte Einheiten möglich.

### Einheitenmodus: wer darf was in der eigenen Einheit?

✓ = erlaubt · – = nicht erlaubt · (✓) = nur unter der genannten Bedingung. Alle Aktionen betreffen ausschließlich die **eigene** Einheit und deren Aufträge; fremde Einheiten sind nicht erreichbar.

| Aktion | Einheiten-Tablet | Gruppenkommandant (SMS-Link) | QR-Zugang | Admin-Simulation |
|---|:---:|:---:|:---:|:---:|
| Eigene Aufträge, Karte und Straßensperren am Ziel ansehen | ✓ | ✓ | ✓ | ✓ |
| Gesamtansicht der Lage ansehen | ✓ (nur lesend) | – | – | – |
| Status melden („Auftrag erhalten“, Anfahrt, Vor Ort, In Arbeit, abgeschlossen, nicht durchführbar) | ✓ | ✓ | ✓ | (✓) nur Übungslage |
| Lagemeldung, Maßnahme, Notiz, Foto | ✓ | ✓ | ✓ | (✓) nur Übungslage |
| Personal und Ausstattung der eigenen Einheit pflegen | ✓ | (✓) nur wenn „GK darf Personal/Ausstattung pflegen“ in den GSL-Einstellungen gesetzt ist | – (nie) | (✓) nur Übungslage |
| Eigene Mobilnummer per SMS-Code bestätigen | – | ✓ | ✓ | – |
| Andere Einheiten umbuchen, Verband bilden, disponieren, Zugänge verwalten | – | – | – | – |
| Offline erfassen und später senden (Outbox) | ✓ | ✓ | ✓ | ✓ |

Hinweise:
- Ein **Widerruf** (manuell, Wechsel/Entfernen des Gruppenkommandanten, Abrücken der Einheit, Lageende, Notbremse, Ausschalten des Org-Schalters) beendet Sitzungen und offene Live-Verbindungen innerhalb weniger Sekunden. Nicht übermittelte Einträge bleiben sichtbar und können kopiert werden.
- Eine **Nummernänderung** durch die Führung oder den Gruppenkommandanten sperrt nur den persönlichen Zugang, nicht den QR-Zugang. Ein **Wechsel des Gruppenkommandanten** sperrt beide.
- Der Zugang eines Gruppenkommandanten ist an dessen **bestätigte oder von der Führung eingetragene Mobilnummer** gebunden; ungeprüfte Selbsteingaben werden nie gespeichert.
- Weder SMS-Link noch QR-Zugang noch Geräte-Logins können die Führungsoberfläche, die Admin-Bereiche oder den MCP-Server nutzen.

### MCP-Werkzeuge der GSL-Ressourcen

Voraussetzungen für alle Werkzeuge: MCP für die Organisation aktiviert, GSL-Ressourcenmodul aktiv, Lage gehört zur Organisation des Benutzers. Lesen: `incident_leader`, `admin`/`org_admin`, `recorder`, `readonly`. Ändern: `incident_leader`, `admin`/`org_admin`, `recorder`; Änderungen setzen eine **aktive** Lage voraus und werden mit dem Zusatz „(MCP)“ im Audit und Journal geführt.

| Werkzeug | Lesen | Ändern |
|---|:---:|:---:|
| `gsl_ressourcen_liste`, `gsl_ressource_details` (Telefon für „Lesen“ maskiert) | ✓ | – |
| `gsl_ressource_aktualisieren`, `gsl_ressource_fuehrer_setzen`, `gsl_ressource_anlegen` | – | ✓ |
| `gsl_einheit_disponieren`, `gsl_auftrag_aendern`, `gsl_auftrag_zurueckziehen` | – | ✓ |
| `gsl_ressource_journal` (Lesen / `neuer_eintrag`) | ✓ / – | – / ✓ |
| `gsl_ressource_personal`, `gsl_ressource_ausstattung` (`aktion=lesen` / Änderungen) | ✓ / – | – / ✓ |
| `gsl_ressource_zugang_senden` (nur SMS, `bestaetigt=true`), `gsl_ressource_zugang_widerrufen` | – | ✓ |
| `gsl_ressource_qr` (`status` / `widerrufen`) | ✓ / – | – / ✓ |

Kein MCP-Werkzeug gibt Zugangstoken, Links, Token-Hashes oder PINs aus; QR-Zugänge können über MCP weder ausgestellt noch angezeigt oder gedruckt werden.

## Hinweise

- Der erste Admin-User wird automatisch beim App-Start aus `.env` (`BOOTSTRAP_ADMIN_*`) angelegt und erhält die Rollen `admin` und `org_admin`.
- Mindestens ein aktiver Admin-User muss immer vorhanden sein.
- Die Anzahl der Benutzer ist nicht begrenzt.
- Benutzer ohne `org_id` (NULL) sind System-Administratoren und sehen alle Organisationen.
- `org_admin` kann Einladungen an neue Org-Admins versenden: [Organisationen verwalten](Administration-Organisations-verwalten).
- Einige Funktionen sind bewusst **nicht** rollenbasiert, sondern über eigene, anonyme Mechanismen geregelt: der **QR-/PIN-Gästezugang** zu einem Einsatz (signierter Einmal-Token bzw. ratenbegrenzte PIN, kein Login), die **Fahrtenbuch-Erfassung per öffentlichem Link/QR** (für Besatzungsmitglieder ohne eigenen Account) sowie der **Gruppenkommandanten-SMS-Link** und der **QR-Zugang für Einheiten** der Großschadenslage (siehe Abschnitt oben).
- Geräte-Logins (fest gekoppelte Tablets/Handys, z.B. für Board-Anzeige oder SMS-Gateway) sind keine „Rolle" im obigen Sinn, sondern ein eigener Login-Mechanismus über Geräte-Token/PIN: [Geräteverleih](Anwender-Geraeteverleih), [SMS-Gateway installieren](Installation-SMS-Gateway).
