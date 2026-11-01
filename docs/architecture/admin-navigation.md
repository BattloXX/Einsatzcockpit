# Admin-Navigation: ein Hauptpunkt je Fachgebiet, Unterseiten als Tabs

Stand: 2026-10-29 (PR #793, #794, #795). Gilt fuer das gesamte Verwaltungsmenue unter `/admin`
(plus `/infoscreen-alarm/verwaltung`, `/gateway`, `/strassensperren/einstellungen`).

## Regel

- Pro Fachgebiet genau **ein** Sidebar-Hauptpunkt.
- Zugehoerige Seiten sind **URL-basierte Tabs** (serverseitig, keine reinen JS-Tabs), Vorbild Infoscreen.
- Jeder Tab hat eine eigene, direkt aufrufbare URL; der Hauptpunkt bleibt auf allen seinen Tabs aktiv,
  genau ein Tab ist aktiv.
- Aktionen (`Neu`, `Import`, `Export`, `Test senden`) sind Buttons innerhalb eines Tabs, keine Tabs.
- Der Katalog **zeigt nur an**. Autorisierung bleibt in den Routern (`require_role`, Modulflags);
  ein versteckter Tab ersetzt keine Rechtepruefung.
- Querverweise statt Doppelpflege: Eine Funktion hat eine kanonische Seite (z. B. Server-Log unter Systemstatus).

## Code

| Was | Wo |
|---|---|
| Katalog (Gruppen, Hauptpunkte, Tabs, Rollen, Modulflags) | `app/services/admin_navigation.py` |
| Jinja-Global `admin_navigation(request, user)` | `app/core/templating.py` |
| Sidebar, Mobile-Drawer, Menuesuche | `app/templates/admin/_layout.html` |
| Tab-Leiste eines Hauptpunkts | `app/templates/admin/_section_tabs.html` (nutzt `subtabnav` aus `_tabnav.html`) |
| Tests | `tests/test_admin_navigation.py` |

Die Modul-Navigation des Hauptmenues (`app/services/modul_navigation.py`) ist davon getrennt.

Eine Seite bindet ihre Tab-Leiste so ein:

```jinja
{% with section_id='alarmierung' %}{% include 'admin/_section_tabs.html' %}{% endwith %}
```

### Katalog-Schema

`AdminGruppe(key, titel, eintraege)` -> `AdminEintrag(id, label, gruppe, icon, href, aktiv_praefixe, tooltip,
sichtbarkeit, modulflag, tabs, ...)` -> `AdminTab(id, label, href, aktiv_praefixe, exakt, sichtbarkeit, modulflag)`.

- `sichtbarkeit`: `voll` (admin/org_admin/system_admin), `alle` (auch eingeschraenkte Admins),
  `sysadmin`, `org_admin`.
- `modulflag`: Name aus `request.state` (z. B. `probenplanung_enabled`); Eintrag/Tab nur bei gesetztem Flag.
- Aktiv-Bestimmung ueber Pfadpraefixe (`exakt=True` fuer Praefix-Konflikte wie `/admin/mail` vs.
  `/admin/mail/eingang`). Eine URL darf zu genau einem Hauptpunkt gehoeren.
- Ein Hauptpunkt ist nur sichtbar, wenn er selbst oder mindestens ein Tab fuer die Rolle sichtbar ist;
  Gruppen ohne sichtbare Eintraege werden nicht gerendert.

## Aktueller Menuebaum

**Stammdaten:** Personal (Mitglieder, Qualifikationen, Gruppen) · Ressourcen · Alarmstichwoerter (Alarmtypen,
Ausrueckordnung, Auftragsvorlagen, Meldungsvorlagen, Lage-Hinweise, Default-Meldungen)

**Verwaltung:** Benutzer & Rechte (Benutzer, Audit-Log) · Probenplanung (Probearten, Checklisten-Vorlagen,
Oeffentlicher Plan) · Infoscreens (Alarm, Wetter, Statistik, Strassensperren) · Alarmierung (SMS senden,
Versandweg, Empfang, SMS-Einsatzinfo, Einsatzinfo-Mail, Teams, Push) · Gateway / Druck · Einsatzdaten ·
Loeschwasserversorgung · Foerderstrecke (Pumpen, Schlaeuche, Kalibrierung) · Geraeteverleih (Artikel,
Stuecklisten, Uebersicht, Geraetetypen) · Dateien & Medien (nur Org-Admins) · Zugaenge & Tokens (Geraete-Login,
API-Keys, Lagekarte-Tokens)

**Einstellungen:** Organisation (Stammdaten, Erscheinungsbild, Module, Allgemeine Einstellungen) ·
GSL-Einstellungen · Wetter · Schnittstellen (LIS, DIBOS, Microsoft SSO, Mail-Versand, Posteingang, WordPress) ·
Strassensperren (Teams-Benachrichtigungen, Querverweis Infoscreen & Zugaenge) · Planspiele (Einstellungen &
Adresspool, Szenarien, Stichwort-Regeln) · KI & MCP (KI-Anfragen, KI-Prompts nur Sysadmin) · Datensicherung
(Sicherung, Wiederherstellung) · Systemstatus (Uebersicht; Systemdiagnose und Server-Log nur Sysadmin)

**Systemverwaltung (nur Sysadmin):** Systemkonfiguration (System-Einstellungen, Mail-Weiterleitungen,
Oeffentliche Seiten) · Organisationen (Organisationsliste, Uebersicht, Quotas, Medien) · Updates & Backups
(System-Update, Stammdaten-Backup, Datenbank-Dumps)

Kanonische Zustaendigkeiten: Server-Log unter Systemstatus, Audit-Log unter Benutzer & Rechte, KI-Anfragen
unter KI & MCP, Strassensperren-Infoscreen unter Infoscreens. Die Medienverwaltung ist eine URL
(`/admin/medien`); Sysadmins sehen sie als Tab in Organisationen, Org-Admins als Hauptpunkt Dateien & Medien.

## Organisations-Einstellungen

`/admin/settings` (Stammdaten), `/erscheinungsbild`, `/module`, `/allgemein` rendern dasselbe Template mit einem
gemeinsamen Formular (`POST /admin/settings/org`). Alle vier Panels sind immer im DOM, nur das aktive ist
sichtbar (`hidden`), damit nie Felder fehlen und Checkboxen beim Speichern nicht zurueckgesetzt werden.
Ein verstecktes Feld `tab` (Whitelist `SETTINGS_TAB_PATHS`) bringt den Nutzer nach dem Speichern in denselben Tab.
Leaflet-Picker werden nur in sichtbaren Panels initialisiert. Die Sysadmin-Organisationsauswahl (`?org_id=`)
bleibt ueber Tab-Links erhalten.

## Neue Verwaltungsseite hinzufuegen

1. Gehoert die Seite fachlich zu einem bestehenden Hauptpunkt? Dann **Tab** in `admin_navigation.py` ergaenzen.
2. Nur fuer ein eigenstaendiges Fachgebiet einen neuen Hauptpunkt anlegen und das in der PR-Beschreibung begruenden.
3. Tab-Leiste in allen Templates der Seite einbinden (`_section_tabs.html`).
4. Rollen (`sichtbarkeit`) und Modulflag im Katalog setzen **und** in der Route per `require_role`/Flag pruefen.
5. `tests/test_admin_navigation.py` ausfuehren: der Waechter `test_jede_admin_seite_gehoert_genau_einem_hauptpunkt`
   schlaegt bei neuen `/admin`-GET-Routen ohne Katalogeintrag fehl. Reine Teilansichten (HTMX), Downloads und
   Detailseiten stehen mit Begruendung in `OHNE_KATALOGEINTRAG`; die Liste darf nur schrumpfen.
6. Dieses Dokument, die Wiki-Seite `Administration-Navigation` und `CHANGELOG.md` aktualisieren.

## Pruefliste fuer PRs

- [ ] Vorhandene Hauptpunkte auf passende Zuordnung geprueft, wenn moeglich Tab statt neuem Hauptpunkt
- [ ] Katalogeintrag mit eindeutiger URL, aktiver Markierung, Rollen und Modulflag
- [ ] Rechte und Modulflags serverseitig in der Route geprueft
- [ ] Mobile (360/768/1024 px): Tab-Leiste scrollt horizontal, Drawer funktioniert
- [ ] Tests fuer Navigation, Deep Link und Berechtigung ergaenzt
- [ ] Dieses Dokument, Wiki, `CHANGELOG.md` aktualisiert

## Bewusst offen

- GSL-Einstellungen und Wetter sind je eine Seite ohne Tabs; sie werden gesplittet, wenn getrennte Seiten entstehen.
- Fahrtenbuch- und Atemschutz-Pruefungs-Einstellungen haben eigene Modul-Tab-Leisten (`fahrtenbuch/_einstellungen_tabs.html`,
  `atemschutz_pruefung/_einstellungen_tabs.html`) und stehen in `OHNE_KATALOGEINTRAG`. Das gilt auch fuer
  Einsatzwissen, Objekte, Kontakte (fachliche Verwaltung im Modul).
- Tabs aus dem Konzept ohne Bestand (Rollen & Berechtigungen, Importprotokoll, Datenpruefung, Dateipruefung,
  Dienste, Datenquellen) sind nicht gebaut.

Die Systemdiagnose enthaelt den lesenden Bereich **App-Geraete** mit HTMX-Detailansicht
`/admin/systemdiagnose/geraet/{geraet_id}` (nur persoenliche Systemadmin-Konten).
Er gehoert zum vorhandenen Hauptpunkt Systemstatus, Tab Systemdiagnose.
