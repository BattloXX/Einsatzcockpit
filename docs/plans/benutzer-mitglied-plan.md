# Benutzer ↔ Mitglied verknüpfen, Einheitenansicht per Login, MCP Benutzer/Mitglieder

Stand: 2026-10-17

## Ziel

1. Ein Login-Benutzer (`user`) kann mit genau einem Mitglied (`member`) derselben Organisation
   verknüpft werden.
2. Ist das verknüpfte Mitglied in einer aktiven Großschadenslage aktueller Gruppenkommandant
   (Führer) einer Einheit, öffnet der Benutzer mit seinem normalen Login die Einheitenansicht
   `/einheit` dieser Einheit – ohne SMS-Link, QR-Code oder Tablet.
3. Ein umfangreicher MCP-Werkzeugsatz für Mitglieder und Benutzer, dessen Rechte exakt der
   Weboberfläche entsprechen.

## Bestand (Analyse)

- `User` (`app/models/user.py`) hat keinen Bezug zu `Member` (`app/models/master.py:184`).
- Gruppenkommandanten einer Einheit stehen in `LageEinheitLeader` (`app/models/major_incident.py:621`)
  mit `member_id` (nullable, wenn frei eingetippt). Aktueller Führer:
  `LageEinheit.leader_assignment_id == leader.id` und `leader.end_at IS NULL`.
- Kontext der Einheitenansicht: `EinheitKontext` (`app/services/einheit_service.py:183`), aufgelöst
  in `app/routers/ui_einheit.py` (`_optionaler_kontext`, `einheit_kontext`) für Tablet
  (`kontext_fuer_geraet`), GK-Zugang/QR (`kontext_fuer_zugang`) und Admin-Simulation
  (`kontext_fuer_einheit`). Normale Benutzer ohne `sim` werden heute auf `/` umgeleitet.
- Rechte Weboberfläche: Mitgliederliste lesen `admin`, `fahrtenbuch_admin`; Mitglieder ändern
  `admin`; Benutzerverwaltung `admin` mit `authorize_user_mutation`, `manageable_role_codes`,
  `permitted_submitted_roles` (`app/core/permissions.py`).
- MCP: Registry `app/mcp/registry.py` (`required_roles`, `module_check`), Live-Rechte je Aufruf
  (`app/mcp/context.py`), Vorlage für schreibende Tools mit Audit: `app/mcp/tools/kontakt.py`.

## Entscheidungen

- E1 Verknüpfung als Spalte `user.member_id` (FK `member.id`, `ON DELETE SET NULL`, eindeutig).
  Mitglied muss zur Org des Benutzers gehören; Geräte-Benutzer werden nie verknüpft.
- E2 Neue Kontextquelle `"benutzer"`. Schreibrechte wie GK-Zugang (`ZUGANG_AKTIONEN`, gleiche
  Einschränkung für `ressource_*` per Org-Einstellung). Autor im Journal:
  `"<Anzeigename> (GK <Einheit>)"`.
- E3 Mehrere Einheiten gleichzeitig (mehrere Lagen): Auswahl per `?einheit=<id>` bzw. Header
  `X-EC-Einheit`; ohne Angabe die jüngste Lage. Die Seite zeigt eine Umschaltliste.
- E4 Einstieg: Hinweis-Karte auf der Startseite „Du führst <Einheit> in <Lage> → Einheitenansicht“,
  keine automatische Umleitung (Benutzer haben auch Board-Rechte).
- E5 MCP-Rechte = Web-Rechte: Mitglieder lesen `admin`/`fahrtenbuch_admin`, Mitglieder schreiben
  `admin`, Benutzer lesen/schreiben `admin`. Keine neue Rolle.
- E6 MCP-Benutzertools setzen nie Passwörter, löschen nie endgültig (nur deaktivieren), vergeben
  nie `system_admin`, fassen keine Geräte-Benutzer und keine System-Admins an. Rollenvergabe nur
  über `permitted_submitted_roles`. Jede Schreibaktion schreibt ein Audit `mcp.<entity>.<aktion>`.

## Pakete

| Paket | Inhalt |
|---|---|
| P1 | Migration 0290 `user.member_id` + Modell, Admin-UI Benutzer anlegen/bearbeiten mit Mitglied-Auswahl, Spalte „Mitglied“ in der Liste, Validierung, Tests |
| P2 | `einheiten_fuer_benutzer` + `kontext_fuer_benutzer`, Router-Auflösung, JS-Header, Umschaltliste, Startseiten-Karte, Tests |
| P3 | MCP Mitglieder lesen: `mitglieder_suchen`, `mitglied_lesen`, `mitglieder_kataloge` (Qualifikationen, Tags) |
| P4 | MCP Mitglieder schreiben: `mitglied_anlegen`, `mitglied_aktualisieren`, `mitglied_aktivieren`/`_deaktivieren`, `mitglied_qualifikationen_setzen`, `mitglied_tags_setzen` |
| P5 | MCP Benutzer lesen: `benutzer_suchen`, `benutzer_lesen`, `rollen_katalog` |
| P6 | MCP Benutzer schreiben: `benutzer_anlegen` (Willkommens-/Reset-Mail statt Passwort), `benutzer_aktualisieren`, `benutzer_rollen_setzen`, `benutzer_aktivieren`/`_deaktivieren`, `benutzer_mitglied_verknuepfen`, `benutzer_passwort_reset_mail` |
| P7 | MCP für jeden Benutzer: `whoami` um verknüpftes Mitglied erweitern, `meine_einheiten` (Einheiten, die ich gerade führe, mit offenen Aufträgen) |

## Status

- P1+P2: PR #593.
- P3–P7: PR „MCP Benutzer und Mitglieder“ (gestapelt auf #593). Gemeinsame Logik in
  `app/services/mitglied_service.py` und `app/services/benutzer_service.py`.

## Offene Tests nach Deploy

- Benutzer mit Mitglied verknüpfen, Mitglied in Übungslage als GK setzen, Login → Startseite zeigt
  Karte, Einheitenansicht bedienbar (Status, Lagemeldung, Foto).
- MCP-Tools mit admin, fahrtenbuch_admin und recorder prüfen (Rechte greifen).
