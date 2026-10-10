# GSL Gesamtplan: Ressourcenkarte, Einheitenmodus, GK- und QR-Zugang

> **Status:** freigegeben am 2026-10-10 (Plan), Umsetzung ab Phase A. Konsolidiert
> `gsl-einheitenmodus-plan.md` und `gsl-ressourcenkarte-gruppenkommandant-plan.md` und löst sie dort ab, wo sie sich widersprechen.
> **Prozessregel:** pro Phase ein Integrationsbranch mit gestapelten Feature-PRs; vollständiger Check und Merge nach `main` nur am Phasenende.

## 1. Executive Summary und verbindliche Entscheidungen

| # | Entscheidung | Begründung |
|---|---|---|
| G1 | **Ableitbarer Zugangs-Token:** `token = prefix + b64url(HMAC(GSL_ZUGANG_KEY, typ \| zugang_id \| generation))`, in der DB nur `token_hash`. | Nachdruck und Auftrags-SMS brauchen denselben Link ohne Rotation. Klartext wird nie gespeichert, nur bei Bedarf neu berechnet. Schlüssel getrennt von `SECRET_KEY`, rotierbar (Rotation = alle Zugänge neu ausstellen). Bestehende `gkz_`-Zufallstokens bleiben gültig. |
| G2 | **QR-Zugang ist ein zweiter Credential-Typ** in `lage_einheit_zugang` (`typ` = `personal`/`qr`, UNIQUE `(einheit_id, typ)`), eigene Sitzungen/Cookie `ec_qr`. | Keine zweite Tabellenfamilie, gleiche Prüfkette, getrennter Widerruf. |
| G3 | **QR-PIN optional und separat.** Standard ohne PIN; mit PIN wird sie nie aufs Blatt gedruckt. | Papier ist Besitz; Widerruf ersetzt PIN. |
| G4 | **Kommunikation läuft über Funk.** Digitale Kräfteanforderung, Chat, Antwort-/Quittierungsketten, Video, eigene Routing-Engine und Zusatzstatistiken entfallen. P2-1 und GK-5.1 entfallen damit. | Auftrag Abschnitt 12; kein zweiter Kommunikationsweg. |
| G5 | **Druck nur über die bestehende ECP-Druckregelverwaltung** (neuer Trigger `gsl_einheit_angelegt`, Standard inaktiv). | Keine eigene Druckpipeline. |
| G6 | **Auto-SMS bei Disposition** über die vorhandene Outbox (`lage_einheit_zugang_versand`), Versand nach Commit, kein Versand ohne verifizierte Nummer. | Idempotenz und Übungsschutz sind bereits gebaut. |
| G7 | **Merge nur am Phasenende**; vorher gilt die PR-CI weiter. | Auftrag Abschnitt 16. |

## 2. Git-/Code-Ist-Analyse (Stand 2026-10-10)

| Bereich | Stand | Beleg |
|---|---|---|
| Einheitenmodus Phase 1 | gemergt (#479–#489) | `app/routers/ui_einheit.py`, `app/services/einheit_service.py`, `app/static/js/einheit_outbox.js` |
| Ressourcenkarte Phase 1 | gemergt (#495) | `app/routers/ui_ressourcenkarte.py`, `ressource_karte_service.py`, Migration 0265 |
| GK-Zugang (SMS/Kopieren/Sitzungen/PIN) | gemergt (#496) | `gk_zugang_service.py`, `ui_gk_zugang.py`, Migration 0266, `ws.py /ws/einheit-zugang` |
| GSL-Konfiguration, Auto-SMS bei GK-Zuweisung | gemergt (#497) | `admin/gsl_einstellungen.html`, `plane_auto_sms`/`sende_auto_sms` |
| Personal, Ausstattung, Verband, GK-Pflege | PR #498 (CI läuft) | Migration 0267, `ressource_pflege_service.py`, `ui_einheit.py /api/ressource/*` |
| MCP-Tools Ressourcen, Doku | Branch `feat/gsl-gk-phase5` (lokal fertig) | `app/mcp/tools/gsl.py` |
| „Einheit hinzufügen“ | nur Basisfelder, kein Audit, kein GK/Personal/Ausstattung | `ressourcen.html:175-246`, `ui_major_incident.py:4927`, `resource_service.add_resource` |
| Disposition → SMS/Push | **nicht vorhanden** (nur WebSocket) | `dispatch_to_site`, `aendere_auftrag`, `withdraw_from_site` in `resource_service.py` |
| „Auftrag bestätigt“ | Status vorhanden, kein eigener Button | `EinheitSiteDispatch.bestaetigt_at`, `einheit_service.py:115-119` |
| Straßensperren im Einheitenmodus | vorhanden (Ziel + Navigationshinweis) | `ui_einheit.py:628,659`, `einheit.html:300-310` |
| Offline-Outbox | vorhanden, „Jetzt senden“, Konflikt/Fehler | `einheit_outbox.js`, `einheit_modus.js` |
| Druckregeln | vorhanden für Einsatz/GSL/Verleih; kein GSL-Einheit-Trigger | `app/models/gateway.py`, `print_dispatcher.py`, `print_artifact_service.py` |
| Telefonverifizierung | nur Muster (`/gk/pin`, Bürgerportal In-Memory) | `ui_gk_zugang.py:92-157` |
| QR-Zugang für Einheiten | nicht vorhanden; QR-Muster Lage/Einsatz | `qr_service.py`, `LageToken`, `_render_qr_einsatz` |
| Kommunikationsmodell P2-1 | nicht umgesetzt, entfällt | `CommLogEntry` ohne `einheit_id`/`art` |

## 3. Traceability-Matrix

Status: V = vollständig, T = teilweise, N = nicht, E = ersetzt, G = bewusst gestrichen, Z = zurückgestellt.

| Quelle | Abschnitt/ID | Anforderung | Code-Nachweis | Status | Entscheidung | Phase/PR | Akzeptanztest |
|---|---|---|---|---|---|---|---|
| RK | 3/4 | Ressourcenkarte als Drawer/Mobil-Sheet, Tabs, Live-Update ohne Verlust | `ui_ressourcenkarte.py` | V | beibehalten | #495 | Tab-/Karten-Tests |
| RK | 1.1 | Gruppenkommandant + Telefon + Stellvertretung + Historie | `setze_gruppenkommandant` | V | beibehalten | #495 | `test_ressourcenkarte_*` |
| RK | 10 | Ressourcenjournal, manuelle Einträge, Storno, Einsatzhistorie | `ressource_karte_service` | V | beibehalten | #495 | Journal-Tests |
| RK | 6 | Token Hash-only, Rotation, Widerruf, Sessions, PIN | `gk_zugang_service` | V | ergänzen um HMAC-Ableitung (G1) | #496, B1 | `test_gk_zugang_*` |
| RK | 7 | SMS, Nachricht/Link kopieren, Vorlage, Segmente | `kopie_ausstellen`, `ressourcen_zugang.js` | V | beibehalten | #496 | Versand-/Zugang-Tests |
| RK | 8 | Auto-SMS bei GK-Zuweisung, GSL-Einstellungen, Notbremse | `plane_auto_sms`, `gsl_einstellungen.html` | V | beibehalten | #497 | `test_gk_auto_sms` |
| RK | 9 | `/einheit` mit GK-Principal, WS, Offline je Principal | `ui_einheit.py`, `ws.py` | V | beibehalten | #496 | `test_einheit_zugang` |
| RK | 11 | Personal, Ausstattung, Verstärken, Ablösen, Umbuchen | `ressource_pflege_service` | V | beibehalten | #498 | Pflege-Tests |
| RK | 11 | Zusammenfassen/Aufteilen/Verlegen mit Historie | `verband_*`, `einheit_aufteilen` | T | härten | E1 | Pflichtszenario 14 |
| RK | 12 | MCP-Tools Ressourcen | `app/mcp/tools/gsl.py` | T | ergänzen (Anlage, Disposition, QR-Status) | Phase 5 / E2 | `test_mcp_gsl_ressourcen` |
| RK | 12.3 | GK-Token öffnet kein MCP | `test_mcp_gsl_ressourcen` | V | beibehalten, auf QR erweitern | Phase 5 / E2 | Token-Tests |
| RK | 15 / GK-5.1 | Kommunikationsblock (P2-1) | – | G | gestrichen (G4) | – | – |
| RK | 15 / GK-5.3 | Abfrageoptimierung `zugang_status_by_einheit` | – | Z | zurückstellen bis Messung | E3 | Ladezeitmessung |
| EM | P1-1…P1-5 | Datenmodell, Services, Kontext, Tablet-UI, Führungsansicht | #479–#489 | V | schützen (Regression) | D3 | Regressionsläufe |
| EM | 5.2a | Admin-Simulation | `ui_einheit._simulationskontext` | V | beibehalten | #485 | Simulationstests |
| EM | E2/5.3 | Stellvertretende Funkerfassung | #488 | V | beibehalten | D3 | Szenario 12 |
| EM | E3/E3a | Stelle nur auf „in Arbeit“ anheben, Abschluss durch Führung | `setze_site_phase` | V | beibehalten | D3 | Szenario 11 |
| EM | 6.5 | Outbox, Idempotenz, Konflikte | `einheit_outbox.js` | T | ergänzen: „Jetzt synchronisieren“, Zähler-Badge, kopierbare Konfliktliste | D1 | JS-Tests |
| EM | P2-1/P2-2/P2-3 | Rückfrage, Anforderung, Quittierung, Gefahrmeldung digital | – | G | gestrichen (G4), `ZUGANG_AKTIONEN`-Einträge ungenutzt | – | – |
| EM | P2-4 | Push bei Neuzuteilung/Änderung/Rückzug | `push_service.notify_vehicle` | N | umsetzen (Tablet) | C3 | Szenario 8 |
| EM | P2-4 | Reminder für Einheiten | – | Z | zurückstellen | – | – |
| EM | P2-2 | „Auftrag bestätigen“ als Primäraktion | Status `bestaetigt` | T | Button ergänzen | D1 | Szenario 8/11 |
| EM | P2-5 | Android-Widget/FCM `einheit_auftrag` | Android-Repo | T | nur funktional nötige Updates | D2 | Gerätetest |
| EM | P3-1 | Offline vollständig, Android-Worker | – | Z | nur bei nachgewiesenem Bedarf | D2 | Flugmodus-Test |
| EM | P3-2 | Navigation, Sperren | `_sperren` | T | Phase-1-Umfang behalten; kein Routing | D2 | Sperrentest |
| EM | P3-3 | MCP-Tools Einheit | – | T | auf Disposition/Auftrag reduzieren | E2 | MCP-Tests |
| EM | P3-4 | Video, Diktat, Keep-Awake | – | G/Z | Video gestrichen; Keep-Awake nach Praxistest | D2 | – |
| EM | P3-5 | Zeitstrahl/Statistiken | – | G | gestrichen | – | – |
| NEU | Auftrag 6 | „Einheit hinzufügen“ mit Kernfeldern, ein Vorgang | `add_resource` | T | `lege_einheit_an` + Drawer | A1–A3 | Szenarien 1, 2, 5 |
| NEU | Auftrag 8 | QR-Zugang + A4-Ausdruck, Widerruf, Nachdruck ohne Rotation | – | N | G1/G2/G3 | B1–B3 | Szenario 4 |
| NEU | Auftrag 9 | Druckregel `gsl_einheit_angelegt`, genau ein Job | `print_dispatcher` | N | Trigger + Dokument + Hook | B4 | Szenario 3 |
| NEU | Auftrag 10 | GK bestätigt eigene Nummer per SMS-Code | Muster `/gk/pin` | N | DB-gestützt, Leader-Version | C1 | Szenarien 6, 7 |
| NEU | Auftrag 11 | Auto-SMS bei Disposition/Änderung/Rückzug | – | N | Outbox + Schalter | C2 | Szenarien 8, 9 |
| NEU | Auftrag 12 | Tablet-Umfang reduziert | – | – | siehe G4 | D | – |
| NEU | Auftrag 15 | Rollenmatrix, Mandanten, Migration, Kompatibilität | – | T | Abschnitt 10 | alle | Szenarien 10, 15–17 |

RK = Ressourcenkarte-Plan, EM = Einheitenmodus-Plan, NEU = Gesamtauftrag.

## 4. Gemeinsame Zielarchitektur und Datenfluss

```
MajorIncident → LageEinheit → LageEinheitLeader ─┐
                    │                              ├─ LageEinheitZugang(typ=personal|qr) → Sitzung (ec_gk | ec_qr)
                    └→ EinheitSiteDispatch → SiteLogEntry/SiteMedia/LageJournalEntry
Disposition (dispatch_to_site / aendere_auftrag / withdraw_from_site)
   → commit → [Outbox auftrag_sms] → SMS nach Commit   → Tablet-Push (notify_vehicle)
Einheit angelegt → commit → Druckregel gsl_einheit_angelegt → PrintJob(artifact_ref=einheit:generation) → Renderer
```
- `/einheit` bedient Gerät, GK-persönlich, QR und Simulation mit denselben Services; nur `einheit_darf` unterscheidet Rechte.
- Der QR-Token wird beim Rendern aus dem HMAC neu berechnet; der Druckjob trägt nur `artifact_ref`.

## 5. Fachkonzept Ressourcenkarte inkl. schneller Neuanlage

- Karte bleibt unverändert (Tabs Übersicht, Einsätze, Journal, Personal, Ausstattung, Zugang).
- **`resource_service.lege_einheit_an`** bündelt `add_resource`, `aktualisiere_einheit_stamm`, `setze_gruppenkommandant`, `personal_setzen`/`person_hinzufuegen`, `ausstattung_hinzufuegen` in einer Transaktion; Audit `gsl.einheit.angelegt`; Duplikatschutz (Fahrzeug/Label je Lage); Org-Prüfung `vehicle_id`; danach `plane_auto_sms` und Druckregel nach Commit.
- Dialog: Kompaktbereich (Name, Funkruf, Org/BOS, Art/Fahrzeug, Status, Abschnitt/Bereitstellungsraum, GK, Telefon, Stärke), aufklappbar Stellvertreter, Mitglieder, Ausstattung, Bemerkung. Telefon aus Mitglied vorbelegen, Eingaben respektieren.

## 6. Personal, Ausstattung, Verstärken, Teilen, Journal

Umgesetzt in Phase 4 (#498). Phase E härtet: keine Doppelzuordnung (`aktiv_key`), keine Überbuchung, Zugangsrotation/-sperre bei Nachfolger-Einheiten, Historie über `aufgeteilt_von_id`/`verband_id`, aktive Dispositionen nur bei gleicher Stelle.

## 7. GK-Zugang, QR-A4-Zugang, Nummernverifizierung

- **QR:** `stelle_qr_zugang_aus` (idempotent, ohne Rotation bei Wiederverwendung), Einlösung über `/gk` (Fragment, POST), Cookie `ec_qr`, Sitzungs-`typ`. Rechte: lesen, Status, Lagemeldung, Foto; Personal/Ausstattung nur Typ `personal`. Widerruf bei GK-Wechsel, abgerückt, Lageende, Notbremse, Org-Schalter, manuell.
- **A4:** Hochformat, s/w, Org-Logo, GSL, Einheit, großer QR, Gültigkeit, Kurzanleitung (QR scannen → Einheitenansicht → Aufträge → Status/Lagemeldung/Foto), fett: „Kräfteanforderungen und dringende Meldungen ausschließlich über Funk!“. Optionale PIN erscheint nie auf dem Blatt.
- **Nummer:** Einheit ohne Nummer arbeitsfähig; nicht blockierender Hinweis; E.164; Code-SMS (DB-gestützt, Ablauf, Rate-Limit, Fehlversuchssperre); Speichern am aktuellen Leader erst nach Verifikation (`phone_verifiziert_at`, Leader-Version gegen Race); Änderung sperrt Alt-Zugänge und stellt neu aus.

## 8. Druckregel und SMS-Automatik

- **Trigger** `gsl_einheit_angelegt` („GSL – Neue Einheit / QR-Einheitenzugang“, 20 Zeichen, keine Migration), Dokument `DOC_GSL_EINHEIT_QR`; Einträge in `TRIGGER_LABELS`, `TRIGGER_DOCUMENT_TYPES`, `RULE_DOCUMENT_LABELS`, `DOCUMENT_TYPE_LABELS`; Zweig in `_jobs_for_rule` mit `artifact_ref="<einheit_id>:<qr_generation>"`; Hook `autoprint_gsl_einheit_background` nach Commit; Admin-UI (`druckregeln.html`) um `elif` erweitern. Statusanzeige: nicht angefordert / beauftragt (`queued`) / übergeben (`sent`) / gedruckt (`done`) / fehlgeschlagen. Druckfehler rollt die Einheit nicht zurück; Retry ohne Rotation.
- **SMS:** Org-Schalter `gk_auto_sms_auftrag` (getrennt von der Zuweisungs-SMS), Vorlage mit Platzhaltern und Segmentprüfung. Ereignisse: Neuauftrag, wesentliche Änderung (Auftragstext, Stelle, Priorität), Rückzug. Keine SMS bei Foto, Lagemeldung, Journalnotiz. `auto_schluessel = <dispatch_id>:<version>:<ereignis>:<phone_version>`. Zusammenlegen mit fälliger Erstzugangs-SMS. Übungs- und Echtverhalten über `darf_extern("sms")`/`darf_extern("autoprint")`. SMS ist keine Lesebestätigung; Funk bleibt verbindlich.

## 9. Reduzierter Tablet-/Android-/Offline-Umfang

Beibehalten: alle Phase-1-Funktionen. Ergänzt: „Auftrag erhalten“-Button (setzt `bestaetigt`), „Jetzt synchronisieren“ und Zähler-Badge, kopierbare Konfliktliste, Outbox je Principal getrennt, Tablet-Push. Straßensperren: Phase-1-Hinweis bleibt, keine neue Routing-Engine. Android-Widget/Worker nur bei Praxisbedarf. Gestrichen: digitale Anforderung, Chat, Video, Zusatzstatistiken. Technisch nicht mehr erforderlich (nicht entfernen ohne Regressionsprüfung): `ZUGANG_AKTIONEN` `anforderung/antwort/quittierung`.

## 10. Sicherheit, Rollen, Token, Migration

| Aktion | Führung | Funker | Gerät | GK-SMS | QR | Sim | MCP |
|---|---|---|---|---|---|---|---|
| Karte lesen/disponieren | ja | lesen/Status | nein | nein | nein | nur lesen/Übung | ja (Rollen) |
| Eigene Einheit lesen, Status, Lagemeldung, Foto | – | – | ja | ja | ja | Übung | – |
| Personal/Ausstattung eigene Einheit | ja | ja | nein | Org-Schalter | nein | – | ja |
| Zugang/Druck/Widerruf | ja (`_darf_zugang_verwalten`) | nein | nein | nein | nein | nein | ja, ohne Token/Link/PIN |

Tenant-/Lagebindung auf allen API-/WS-/Medienpfaden; `no-store`, Referrer-Policy, Rate-Limits, CSRF; keine Geheimnisse in SmsLog/Audit/PrintJob/Logs (`schwaerze_link`); Widerruf beendet WS ≤25 s; Offline-Entwürfe bei Widerruf sichtbar halten (`blockiert_zugang`). Migrationen: MariaDB-Zyklus (FKs vor Index droppen), additive Spalten, UNIQUE-Umbau in zwei Schritten, alte Android-Versionen und API-Formate unverändert.

## 11. MCP- und API-Konzept

Vorhanden (Phase 5): neun Ressourcen-Tools. Ergänzung (E2): `gsl_ressource_anlegen`, `gsl_einheit_disponieren`, `gsl_auftrag_aendern`, `gsl_auftrag_zurueckziehen`, QR-Status/-Widerruf. Immer dieselben Services, Rollen, Tenant-Filter, Audit `via=mcp`; nie Token, Link oder PIN; GK-/QR-Credential öffnet kein MCP. Keine Tools für Anforderung/Chat.

## 12. Phasen, PRs, Abhängigkeiten

Phase 0: #498 und `feat/gsl-gk-phase5` abschließen (CI, Merge).

| Phase | PR | Inhalt | Abhängigkeit |
|---|---|---|---|
| A | A1 | `lege_einheit_an` + Audit + Duplikatschutz | 0 |
| A | A2 | Dialog „Einheit hinzufügen“, Auto-SMS-Plan | A1 |
| A | A3 | Tests, Darstellung auf Karte/Übersicht/Journal | A2 |
| B | B1 | Migration `typ`/QR-Felder, HMAC-Token, Schlüsselkonfiguration | A |
| B | B2 | QR-Service, Einlösung `ec_qr`, `einheit_kontext`, WS, Widerrufs-Hooks | B1 |
| B | B3 | Tab Zugang: QR-Vorschau, manueller A4-Druck, Status, Widerruf, PIN-Anzeige | B2 |
| B | B4 | Druckregel `gsl_einheit_angelegt`, Template, Hook, Statusanzeige | B3 |
| C | C1 | Nummernverifizierung (Migration, Endpunkte, Journal) | B2 |
| C | C2 | Dispositions-SMS (Schalter, Vorlage, Hooks, Idempotenz, Retry) | B1, C1 |
| C | C3 | Tablet-Push | C2 |
| D | D1 | Auftrag erhalten, Sync-UI, Outbox je Principal | B2 |
| D | D2 | Sperren-Hinweis prüfen, Android nur bei Bedarf | D1 |
| D | D3 | Regression Funkerfassung/Simulation/Geräte | D1 |
| E | E1 | Verband/Umbuchung gehärtet | C |
| E | E2 | MCP-Erweiterung | A–C |
| E | E3 | Wiki, CHANGELOG, Konfigurationsdoku, E2E | alle |

```
0 → A → B → C → E
        B2 → D1 → D2/D3
```

## 13. Testfälle und Phasenabnahme

Pflichtszenarien 1–17 des Gesamtauftrags sind den Phasen zugeordnet: 1, 2, 5 → A; 3, 4 → B; 6, 7 → C1; 8, 9 → C2/C3; 10 → B2; 11, 12, 13, 17 → D; 14 → E1; 15 → E2; 16 → jede Phase.
**Phasenende:** (1) Code-Review des integrierten Stands, (2) volle pytest-Suite, ruff, mypy, JS-Tests, (3) MariaDB-Zyklus (upgrade, downgrade, upgrade) und Rückwärtskompatibilität, (4) Tenant-/Rollen-/Token-/QR-/SMS-/Druck-Regressionen, (5) Playwright-Szenario Anlage → QR-Druckjob → QR-Login → Nummer bestätigen → Disposition → SMS-Outbox → Offline-Meldung → Widerruf, (6) alle Akzeptanzkriterien abhaken und dokumentieren, (7) Merge der gestapelten PRs in Abhängigkeitsreihenfolge, (8) Dokumentation (PR-Nummern, Testergebnisse, Restrisiken).

## 14. Risiken, Streichungen, offene Entscheidungen

- **Risiken:** HMAC-Schlüssel-Kompromittierung (getrennter, rotierbarer Schlüssel; Rotation widerruft alles); SMS-/Chat-Historie enthält Link; ausgedruckte Blätter (Widerruf, Gültigkeit, kein PIN-Aufdruck); Drucker-Queue als Datenspeicher (nur `artifact_ref`, Rendern erst beim Abruf); UNIQUE-Umbau auf MariaDB; Duplikat-SMS bei gleichzeitiger Anlage und Disposition.
- **Gestrichen:** digitale Anforderung, Chat, Antwortketten, Video, Routing-Engine, Zusatzstatistiken/Zeitstrahl, P2-1, GK-5.1.
- **Zurückgestellt:** Reminder für Einheiten, Android-Hintergrundworker, `zugang_status_by_einheit`-Optimierung.
- **Offene Entscheidungen:** keine blockierenden. Zu klären im Betrieb: Gültigkeitsdauer gedruckter QR-Zugänge (Vorschlag: bis Lageende, max. 72 h), Schlüsselablage für `GSL_ZUGANG_KEY`.

## 15. Phasenübersicht

| Phase | Ziel | PRs | Abhängigkeiten | Abschlusskriterien |
|---|---|---|---|---|
| 0 | Bestand abschließen | #498, Phase 5 | – | CI grün, gemergt |
| A | Schnellanlage | A1–A3 | 0 | Szenarien 1, 2, 5 grün |
| B | QR-Zugang + Druck | B1–B4 | A | Szenarien 3, 4, 10 grün, MariaDB-Zyklus |
| C | Nummer + Dispositions-SMS + Push | C1–C3 | B | Szenarien 6–9 grün |
| D | Einheitenmodus-Politur | D1–D3 | B2 | Szenarien 11–13, 17 grün |
| E | Verlegen/Teilen, MCP, Doku | E1–E3 | A–C | Szenarien 14–16 grün, Doku vollständig |

## 16. Umsetzungsstand

### Phase A + B – Schnellanlage, QR-Zugang, Druckregel (Branch `feat/gsl-gesamt-b`)

- A1–A3: `resource_service.lege_einheit_an` (atomar, Audit `gsl.einheit.angelegt`, Duplikatschutz), Route `lage_einheit_create` delegiert; Dialog mit Funkruf, Status, Abschnitt, Bereitstellungsraum, GK + Mobilnummer, Besatzungsstärke und aufklappbaren Zusatzangaben.
- B1: Migration 0268 (`typ` an Zugang und Sitzung, UNIQUE `(einheit_id, typ)`, `qr_*`, Org-Schalter `gk_qr_*`), ableitbarer HMAC-Token (`GSL_ZUGANG_KEY`, Präfix `gkz_`/`gkq_`); Altbestand-Zufallstokens bleiben gültig.
- B2: `stelle_qr_zugang_aus` (idempotent, ohne Rotation bei Wiederverwendung), Cookie `ec_qr`, `/einheit` und WebSocket für QR; Ressourcenpflege für QR gesperrt; Widerruf bei GK-Wechsel, Abrücken, Lageende, Notbremse, manuell, Schalter.
- B3: QR-Bereich im Zugang-Tab (Vorschau auf Klick, PIN nur für die Führung), A4-Dokument (`DOC_GSL_EINHEIT_QR`, PIN nie auf dem Blatt, Renderer prüft Org/Lage/Generation), manueller Druck.
- B4: Druckregel `gsl_einheit_angelegt` (Standard: keine Regel), Autodruck nach Commit, Statusanzeige, Cookie-Reihenfolge `ec_gk`/`ec_qr`.
- Browser-Smoketest (Chromium): `/gk#gkq_…` → Fragment entfernt → Einlösung (mit und ohne PIN) → `/einheit`. Dabei gefunden und behoben: Einlöseseite akzeptierte nur `#gkz_`, versteckte Elemente wurden durch `.btn` überschrieben.
- Offen: Gerätetest auf echtem Smartphone, echter Drucker/Gateway, Layout des A4-Dokuments im Druck.

### Phase C – Nummernbestätigung, Auftrags-SMS, Tablet-Push (Branch `feat/gsl-gesamt-c`)

- C1: Der Gruppenkommandant bestätigt seine Mobilnummer im Einheitenmodus (QR- oder persönliche Sitzung) per SMS-Code (Migration 0269, `gk_nummer_service`): E.164, 10 Minuten gültig, 5 Fehlversuche → 15 Minuten Sperre, 3 Codes je 10 Minuten, Race-Schutz gegen Führungswechsel/Nummernänderung, Widerruf des persönlichen Zugangs und Auto-SMS an die bestätigte Nummer; kein Klartext in Audit, Journal und SmsLog.
- C2: Auftrags-SMS bei Neu-Disposition, wesentlicher Änderung (Auftragstext) und Rückzug (Migration 0270, Org-Schalter `gk_auto_sms_auftrag`, eigene Vorlage). Idempotenzschlüssel je Auftragsversion; ein bestehender gültiger Zugang wird nicht rotiert (ableitbarer Link), Altbestand-Zufallstokens werden einmalig neu ausgestellt. **Entscheidung:** Als vertrauenswürdig gelten von der Führung eingetragene und vom GK bestätigte Nummern; eine Selbsteingabe wird nie ungeprüft gespeichert. Manueller Retry in der Ressourcenkarte (Zugang-Tab).
- C3: Push an das Fahrzeug-Tablet (nur Geräte mit `gsl_profil=einheit`, Übungsschutz `push`) nach dem Commit; ein Push ist keine Lesebestätigung.
- Nicht umgesetzt: Auftrags-SMS beim Rückzug durch Verbandsauflösung; Zusammenlegen der Erstzugangs-SMS mit der Auftrags-SMS (beide enthalten denselben Link, getrennte Nachrichten).
- Beim Review gefunden und behoben: QR-Sitzungen hatten keinen Leader (Nummernbestätigung wäre für QR immer gescheitert), SmsLog-`source` länger als die Spalte (MariaDB), Downgrade 0269 auf MariaDB, Layout des Hinweisbalkens auf dem Smartphone.

### Phase D + E – Einheitenmodus-Politur, MCP, Doku

- D1: „Auftrag erhalten“ (Status `bestaetigt`), „Jetzt synchronisieren“, Konflikte/Fehler kopierbar, getrennte Outbox `ec-einheit-qr-<id>`. D2/D3: Straßensperren-Hinweis, stellvertretende Funkerfassung und Simulation unverändert, durch die bestehenden Regressionstests (`test_einheit_*`, `test_gsl_*`) abgesichert.
- E1: Verband/Aufteilung/Umbuchung sind durch die Tests aus Phase 4 abgedeckt (Mengen, Historie, Dispositionen, Doppelzählung); Zugangssicherheit: Nachfolger-Einheiten erhalten keinen Zugang, Verbands-Kinder bleiben nicht disponierbar.
- E2: MCP `gsl_ressource_anlegen`, `gsl_einheit_disponieren`, `gsl_auftrag_aendern`, `gsl_auftrag_zurueckziehen`, `gsl_ressource_qr` (nur Status/Widerruf); gemeinsamer Helper `gsl_auftrag_events` für SMS und Push nach dem Commit.
- E3: Wiki (Anwender, Administration, MCP), CHANGELOG. Die Traceability-Matrix in Abschnitt 3 ist **verdichtet**; die zeilengenaue Fassung beider Altpläne steht aus.
- Offen: Tests auf echten Smartphones/Tablets, echter Drucker und SMS-Gateway, Layout des A4-Blatts im Druck, Rückzugs-SMS bei Verbandsauflösung, Zusammenlegen von Erst- und Auftrags-SMS.
