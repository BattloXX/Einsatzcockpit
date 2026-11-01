# Verwaltungsmenü: Hauptpunkte und Tabs

← [Zurück zur Startseite](Home)

> URL: `/admin`  
> Zugänglich für: `org_admin`, `admin`, `system_admin` (Systemverwaltung nur `system_admin`)

Im Verwaltungsmenü gibt es pro Fachgebiet **einen** Menüpunkt. Die zugehörigen Seiten erreichen Sie über die **Tabs** am oberen Rand des Inhalts, wie beim Infoscreen. Jeder Tab hat eine eigene Adresse, Lesezeichen und der Zurück-Button funktionieren. Auf dem Handy öffnet der Button „Verwaltungsmenü“ die Liste; das Suchfeld findet auch Tab-Namen.

## Stammdaten

| Menüpunkt | Tabs |
|---|---|
| Personal | Mitglieder · Qualifikationen · Gruppen |
| Ressourcen | Fahrzeuge, Geräte, Einheiten |
| Alarmstichwörter | Alarmtypen · Ausrückordnung · Auftragsvorlagen · Meldungsvorlagen · Lage-Hinweise · Default-Meldungen |

## Verwaltung

| Menüpunkt | Tabs |
|---|---|
| Benutzer & Rechte | Benutzer · Audit-Log |
| Probenplanung | Probearten · Checklisten-Vorlagen · Öffentlicher Plan |
| Infoscreens | Alarm-Infoscreen · Wetter-Infoscreen · Statistik · Straßensperren |
| Alarmierung | SMS senden · SMS-Versandweg · SMS-Empfang · SMS-Einsatzinfo · Einsatzinfo-Mail · Teams-Alarmierung · Push-Nachrichten |
| Gateway / Druck | Gateway & Drucker · Druckregeln · Alarmempfang (je Gateway) |
| Einsatzdaten | Einsatz-Import |
| Löschwasserversorgung | Wasserstellen |
| Förderstrecke | Pumpen · Schläuche · Kalibrierung |
| Geräteverleih | Artikel · Stücklisten · Übersicht · Gerätetypen |
| Dateien & Medien | Medienverwaltung (Org-Admins) |
| Zugänge & Tokens | Geräte-Login · API-Keys · Lagekarte-Tokens |

## Einstellungen

| Menüpunkt | Tabs |
|---|---|
| Organisation | Stammdaten · Erscheinungsbild · Module · Allgemeine Einstellungen (siehe [Einstellungen](Administration-Einstellungen)) |
| GSL-Einstellungen | – |
| Wetter | – |
| Schnittstellen | LIS · DIBOS · Microsoft SSO · Mail-Versand · Posteingang · WordPress |
| Straßensperren | Teams-Benachrichtigungen · Verweis auf Infoscreen & Zugänge |
| Planspiele | Einstellungen & Adresspool · Szenarien · Stichwort-Regeln |
| KI & MCP | KI-Anfragen · KI-Prompts (nur Systemadmin) |
| Datensicherung | Sicherung · Wiederherstellung |
| Systemstatus | Übersicht · Systemdiagnose und Server-Log (nur Systemadmin) |

## Systemverwaltung (nur Systemadmin)

| Menüpunkt | Tabs |
|---|---|
| Systemkonfiguration | System-Einstellungen · Mail-Weiterleitungen · Öffentliche Seiten |
| Organisationen | Organisationsliste · Übersicht · Quotas · Medien |
| Updates & Backups | System-Update · Stammdaten-Backup · Datenbank-Dumps |

## Hinweise

- Alle bisherigen Seiten und Adressen funktionieren weiter; es wurde nur das Menü zusammengefasst.
- Tabs oder Menüpunkte, für die Ihnen die Berechtigung oder das Modul fehlt, werden nicht angezeigt.
- Die Organisations-Einstellungen sind ein einziges Formular: Speichern übernimmt alle vier Tabs und bringt Sie zurück in den aktuellen Tab.
- Entwicklerinfos: `docs/architecture/admin-navigation.md` im Repository.

Die Systemdiagnose enthaelt den lesenden Bereich **App-Geraete** mit HTMX-Detailansicht
`/admin/systemdiagnose/geraet/{geraet_id}` (nur persoenliche Systemadmin-Konten).
Er gehoert zum vorhandenen Hauptpunkt Systemstatus, Tab Systemdiagnose.
