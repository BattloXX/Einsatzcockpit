# Einstellungen

← [Zurück zur Startseite](Home)

> URL: `/admin/settings` (Tabs: `/admin/settings/erscheinungsbild`, `/module`, `/allgemein`)  
> Zugänglich für: `org_admin`, `admin`, `system_admin`

## Organisations-Einstellungen

Jede Organisation kann ihre eigenen Einstellungen verwalten.

### Logo hochladen

**Einstellungen** → **Logo** → Datei auswählen (PNG, JPG, SVG, WebP — max. 2 MB)

Das Logo wird angezeigt:
- Im Header der App (neben dem Organisationsnamen)
- Auf dem Deckblatt des PDF-Einsatzberichts
- Im Kopf der Kontakt-Druckverzeichnisse (beide Layouts, Vorschau, PDF und Browserdruck)
- Auf der About-Seite

Hochgeladene Logos werden in `app/static/img/uploads/` gespeichert und sind beim System-Update **geschützt** (werden nicht überschrieben).

Im [Kontakt-Druckdialog](Anwender-Kontaktverwaltung#organisationslogo) lässt sich die Logoanzeige
ein- oder ausschalten und im persönlichen Druckprofil speichern. Ohne lesbare Logodatei
bleibt das Verzeichnis ohne Bild nutzbar.

### Organisationsname

Der angezeigte Name der Organisation in der gesamten App und in Berichten.

### Primärfarbe

Die Akzentfarbe der Organisation:
- Linker Streifen auf Fahrzeugkarten im Board
- Wird auch für eigene Fahrzeuge im Multi-Org-Einsatz genutzt

### Kontaktdaten

Für Impressum und PDF-Berichte: E-Mail, Telefon, Adresse. Der **Ort** dient außerdem als
Vorauswahl der Adresssuche in [Katastrophenplänen](Administration-Katastrophenplaene), wenn
ein Element keinen Ort hat.

### Module

Optionale Module werden doppelt geschaltet: zuerst systemweit (System-Admin), dann je
Organisation in deren Einstellungen. Ein Beispiel ist **Katastrophenpläne**: Modul und
optional der **KI-Import für Planquellen**. Details stehen auf der jeweiligen Modulseite,
z. B. [Katastrophenpläne](Administration-Katastrophenplaene).

### Footer-Text

Erscheint im Footer jedes PDF-Einsatzberichts (z.B. „FF Wolfurt — Rathausstraße 1 — 6922 Wolfurt").

### Karte / Fallback-Standort

Der Fallback-Standort bestimmt, wo der Karten-Picker im Adress-Bearbeitungs-Dialog startet, wenn noch keine Koordinaten am Einsatz gesetzt sind und Geocoding keinen Treffer liefert.

**Felder**: Breitengrad (lat) + Längengrad (lng) in Dezimalgrad.  
**Karten-Picker**: Marker auf der Karte verschieben oder direkt auf die Karte klicken.  
**Standard**: Wolfurt (47.4664 / 9.7416) — falls nicht konfiguriert.

Details zur Lagekarte.info-Integration: [Lagekarte.info](Anwender-Lagekarte)

### Keine Alarmierung bei Begriffen

Unter **Keine Alarmierung bei Begriffen** können pro Zeile Begriffe hinterlegt werden. Kommt einer davon in Stichwort, Meldung oder Grund eines neuen Einsatzes vor, wird der Einsatz weiterhin angelegt und angezeigt, aber SMS, Teams, Mail und Push werden nicht versendet. Groß- und Kleinschreibung werden dabei ignoriert. Einsätze mit dem Stichwort F30 werden immer unterdrückt.

## Atemschutzüberwachung

Unter **Einstellungen → Allgemein → Atemschutzüberwachung** werden die Werte der
Organisation gepflegt. Das Modul muss zusätzlich systemweit und für die
Organisation freigeschaltet sein. Für den Trupp gelten die Werte der primären
Einsatzorganisation.

| Einstellung | Wirkung / Vorgabe |
|---|---|
| Überwachung nur in der App erlauben | Standard aus; Eingaben verlangen eine App-Sitzung mit frischem Heartbeat. Browser bleiben als Nur-Lese-Ansicht verfügbar; Notfall und Funkverlust können auch ohne App gemeldet werden. |
| Erwartete Einsatzzeit | 30 Minuten (5–120); Kontrollen bei 1/3 und 2/3, also standardmäßig nach 10 und 20 Minuten. Die Flaschengröße bestimmt die Zeit nicht mehr. |
| Rückzug-Untergrenze | 60 bar (30–100 einstellbar); Vorgabe entspricht der Warneinrichtung. Nach Erreichen des Einsatzziels gilt der größere Wert aus Untergrenze und doppeltem Anmarschverbrauch. |
| Gelbschwelle | 75 % des Anfangsdrucks (50–95); gelbe Druckwarnung. |
| Erholzeit | 30 Minuten (0–120); vorzeitig erneutes Einsetzen verlangt eine begründete Übersteuerung. |
| Eskalationsempfänger | Aktive Benutzer der eigenen Organisation; zusätzlich erhält der Einsatzleiter die Eskalation. Ohne Zuordnung fallen die Empfänger auf aktive AS-Überwacher der Organisation zurück. |
| SMS-Eskalation | Standardmäßig aktiv; zusätzlich SMS-Versand und Telefonnummern einrichten. In Übungen werden SMS unterdrückt. |
| Eskalationsfrist | Organisationswert `escalation_grace_min`, Vorgabe 3 Minuten; Wartezeit für die Max-Zeit-Eskalation. Andere Ereignisse haben feste Stufen. |

Die Abläufe folgen FAZ/LFV Vorarlberg: niedrigsten Druck des Trupps abfragen,
fällige Kontrollen dokumentieren statt nur quittieren, Rückzug beim doppelten
Anmarschverbrauch mit Warneinrichtung als Untergrenze. Überwacher, Funkkanal und
Angriffsweg werden je Trupp erfasst. Doppelbelegung und zu kurze Erholung werden
beim Einsetzen geprüft.

| Ereignis | Push / SMS und Zeitpunkt ab Auslösung |
|---|---|
| Kontrolle 1/3 oder 2/3 offen | Stufe 0 nach 60 s Push an Überwacher; Stufe 1 nach 180 s Push an Leitung; Stufe 2 nach 300 s SMS an Leitung. |
| Rückzugsdruck | Stufe 0 sofort Push an Überwacher; Stufe 1 nach 60 s Push und SMS an Leitung. |
| Max-Einsatzzeit | Stufe 0 sofort Push an Überwacher; Stufe 1 nach Eskalationsfrist Push und SMS an Leitung. |
| Funkverlust | Stufe 0 sofort Push an Überwacher, Push und SMS an Leitung. |
| Notfall | Stufe 0 sofort Push und SMS an Überwacher und Leitung. |
| ASSP / kein bereiter Sicherheitstrupp | Stufe 0 sofort Push an Leitung. |
| Kein Überwachungsgerät aktiv | Bei App-Pflicht und eingesetztem Trupp ohne frischen Heartbeat: sofort Push-Alarm an Leitung, nach 180 s SMS an Leitung; keine Zustellung an die Gruppe Überwacher. |

Überwacher ist der zugeordnete Benutzer, ersatzweise der Einsatzleiter. Leitung
bezeichnet die oben beschriebene Empfängergruppe. Warnungen und Zustellungen sind
persistent; wartende Zustellungen werden bei Erledigung unterdrückt. Das Board
zeigt offene Warnungen samt Zustellungsprotokoll und Gründen für Unterdrückungen.

Ab drei eingesetzten Trupps fordert der Einsatzhinweis den **ASSP** über die
Einsatzleitung an (nur Logistik, Überwachung bleibt beim Gruppenkommandanten).
Ein Sicherheitstrupp zählt nur im Status **bereit**. Funkverlust und Notfall
bleiben im Board sichtbar, bis sie aufgehoben werden.

> **Bis zur unterzeichneten Geräteabnahme (Phase 8) ergänzt die digitale ASÜW
> die papierbasierte Überwachung und ersetzt sie NICHT.** Tafel/Formular weiterführen;
> siehe Abnahme-Checkliste (docs/plans/atemschutzueberwachung-abnahme.md).

**Rollout:** Den Schalter **Überwachung nur in der App erlauben** unter
**Einstellungen → Allgemein** erst aktivieren, wenn alle Überwachungsgeräte die
App-Version **mit nativem Wächter** installiert haben und die Geräteabnahme
abgeschlossen ist. Die Fähigkeit `asuew` allein genügt nicht. Android-PRs #96–#98
sind gemergt, das dafür nötige Android-Release ist noch nicht veröffentlicht.
Standard ist aus.
Die App-Sitzung ist an Benutzer, Einsatz und Gerät gebunden. Heartbeats kommen
alle 15 Sekunden und gelten 60 Sekunden; nach spätestens 24 Stunden oder bei
beendetem Einsatz läuft die Sitzung ab. Beendete Sitzungen werden nach 30 Tagen
in begrenzten Chargen gelöscht.

Die Selbsttest-Ampel bewertet Benachrichtigungen, Verbindung, Uhr und Akku.
Alarmton, exakte Wecker, Vollbild und Akku-Optimierung sind native
Prüfpunkte; fehlende Angaben sind keine bestätigte Prüfung. Einzelheiten zu Grün,
Gelb und Rot sowie zur Nur-Lese-Ansicht stehen in der Anwenderseite.

Systemadmins können über die Umgebungsvariable `ASUEW_MIN_APP_VERSION` eine
Mindest-App-Version für den Sitzungsstart festlegen, zum Beispiel `2026.11.06`.
Leer (Standard) bedeutet keine Versionsprüfung. Bei gesetztem Wert werden ältere,
fehlende oder ungültige App-Versionen mit dem Hinweis zum Aktualisieren abgewiesen.
Die App ermittelt ihre Version über das offizielle Capacitor-App-Plugin.
Die kommentierte Variablenreferenz steht in `.env.example`.

Gerätecheck, nativer Alarmton, Ablauf und Funkverlust-Checkliste:
[Atemschutzüberwachung](Anwender-Atemschutzueberwachung).

## Auto-Schließen (Einsatz automatisch beenden)

Einsätze können automatisch geschlossen werden, wenn sie für eine konfigurierbare Zeit inaktiv waren.

| Einstellung | Beschreibung |
|-------------|-------------|
| **Auto-Schließen aktiviert** | Einschalten für diese Org (NULL = globaler Standard) |
| **Nach Stunden** | Einsatz nach X Stunden ohne Aktivität schließen |
| **Toleranzzeit (Minuten)** | Zusätzliche Karenzzeit vor dem Schließen |

Wenn die Org-Einstellung auf NULL steht, gelten die globalen Werte aus den System-Einstellungen.

> Einsätze mit aktiver Atemschutzüberwachung werden nie automatisch geschlossen.

## System-Einstellungen (nur system_admin)

Auf der Einstellungsseite sieht der System-Admin zusätzlich:

- **Aktuelle App-Version**
- **Schnelllinks**: Organisationen verwalten, System-Update, About

Globale Auto-Schließ-Defaults (gelten für alle Orgs, die kein eigenes Limit gesetzt haben) werden in den System-Einstellungen konfiguriert.

## Lokale Wetterstation

Orgs mit einer Davis Vantage Pro 2 Plus (oder kompatibler Station) können diese über **Admin → Einstellungen → Wetterstation** anbinden.

**Station anlegen:**
1. „Station hinzufügen" — Name eingeben, optionale GPS-Koordinaten
2. Der einmalige **Push-Token** (`wxst_...`) und die fertige **Meteobridge-URL** werden angezeigt
3. URL in **Meteobridge PRO RED → Services → Custom Push** eintragen, Intervall 5 min, Methode GET

> Token wird nur einmalig angezeigt — sofort in Meteobridge eintragen.

**Token rotieren:** „Token neu generieren" widerruft den alten Token sofort.

**Station entfernen:** Löscht Station und Token; historische Zeitreihen werden beim nächsten Retention-Lauf bereinigt.

Vollständige Anleitung: [Lokale Wetterstation (Admin)](Administration-Wetterstation)

## System-Update (nur system_admin)

Neue Versionen können über `/admin/system/update` per ZIP-Upload eingespielt werden.

## About-Seite

`/admin/about` — Versions-Info, Autoren, Changelog.

Zugänglich für alle angemeldeten Benutzer.


---

## GSL-Einstellungen

Die vollständige Feldliste und das Widerrufsverhalten stehen unter [GSL-Einstellungen](Administration-GSL-Einstellungen).

## GSL: Gruppenkommandanten-Zugang

Unter **Einstellungen → Großschadenslage** steuert die Karte „Gruppenkommandanten-Zugang“:

| Einstellung | Wirkung |
|---|---|
| Zugang aktiv | Hauptschalter, standardmäßig **aus**. Ausschalten widerruft alle bestehenden Zugänge der Organisation. |
| Nachrichtenvorlage | Text für SMS und Kopieren, mit Platzhaltern, Live-Zähler der SMS-Segmente und Vorschau. Der Link wird in Protokollen geschwärzt. |
| Automatisch senden | SMS beim Zuweisen eines Gruppenkommandanten. Bei Übungen wird sie nur gesendet, wenn zusätzlich **„SMS an Gruppenkommandanten auch bei Übungen senden“** aktiv ist; dieser Schalter ist unabhängig von Einsatzinfo-SMS. |
| Gültigkeit, Limits | Gültigkeit 1–168 Stunden, Sitzung 1–72 Stunden und maximal 1–5 Sitzungen. |
| SMS-PIN | Optionaler SMS-Bestätigungscode beim Öffnen; benötigt einen SMS-Anbieter. |
| Ressourcen pflegen | Erlaubt dem GK, Personal und Ausstattung der eigenen Einheit zu ändern. |
| Notbremse | Widerruft sofort alle Gruppenkommandanten-Zugänge der Organisation. |

| Auftrags-SMS automatisch | Sendet bei neuem/geändertem/zurückgezogenem Auftrag eine SMS an den Gruppenkommandanten (eigene Vorlage mit Platzhaltern `{ereignis}`, `{gsl}`, `{einheit}`, `{einsatzstelle}`, `{auftrag}`, `{link}`; `{link}` ist Pflicht). Voraussetzung: Zugang aktiv und Mobilnummer vorhanden. |
| QR-Zugang aktiv | Erlaubt QR-Ausdrucke für Einheiten (Standard: aus). Ausschalten widerruft alle QR-Zugänge. |
| QR-Gültigkeit (Stunden) | 1–168, Standard 72. |
| QR-PIN | Optional; die PIN wird nur der Einsatzleitung angezeigt, nie gedruckt. |

Zusätzlich konfiguriert die GSL-Seite die Module, den Lagemeldungs-Regelkreis, Sonderalarm, Geräteverleih-SMS und die automatische Übernahme als Einsatzstelle.

**Serverkonfiguration:** `GSL_ZUGANG_KEY` (eigener, geheimer Schlüssel für die Ableitung der Zugangslinks, empfohlen: `python -c "import secrets; print(secrets.token_urlsafe(48))"`). Ein Wechsel des Schlüssels macht alle ausgegebenen Links ungültig. Ohne Wert wird der Schlüssel aus `SECRET_KEY` abgeleitet (eine Warnung im Log weist darauf hin); dann entwertet eine `SECRET_KEY`-Rotation alle Links.

## Zwei-Faktor-Authentifizierung fuer Systemadministratoren

Unter `/admin/system-einstellungen` kann TOTP-2FA eingerichtet und systemweit
aktiviert werden. Standard ist AUS; vor der Aktivierung muessen alle aktiven
Systemadministratoren eingerichtet sein. Siehe
[Zwei-Faktor-Authentifizierung](Administration-Zwei-Faktor-Authentifizierung)
fuer Einrichtung, Recovery-Codes und den lokalen CLI-Notfallreset.

## Lesender System-Admin-MCP

Unter dem 2FA-Abschnitt stehen der globale Admin-MCP-Schalter (Default AUS), die
persoenlichen Freigaben und die Verbindungs-URL. Details zu Aktivierung, OAuth,
Werkzeugen und Grenzen: [System-Admin-MCP](Administration-System-Admin-MCP).


## Systemdiagnose (nur system_admin)

Der eigene Abschnitt **Systemdiagnose** unter `/admin/system-einstellungen`
steuert die optionale Tagesdiagnose. Standard ist **AUS**; es gibt keine
voreingestellten Mailempfaenger. Uhrzeit (Standard 03:00) gilt systemweit in
Europe/Vienna, unabhaengig von Org-Zeitzonen. Umfang ist `schnell` oder `voll`.
Kritische Befunde werden bei konfigurierten Empfaengern ueber die vorhandene
Systemmail versandt: Mindest-Schweregrad `warn`, `fail` (Standard) oder `kritisch`,
Wiederholung unveraenderter Befunde nach standardmaessig 24 Stunden (1–720).

Ein DB-Tages-Slot verhindert doppelte geplante Laeufe ueber mehrere Hosts.
Einsaetze verschieben den Start bis zu sechs Stunden; danach laufen nur leichte
Checks. Meldungen sind nach Finding-Key dedupliziert und auf eine Sammelmail je
Lauf begrenzt. Versandfehler blockieren die Diagnose nicht und stehen im Audit.
Die Schluessel `diag_schedule_*` sind im allgemeinen Key-Value-Editor gesperrt;
Aenderungen erfolgen nur ueber diesen eigenen Abschnitt mit CSRF und Audit.

Das [Systemdiagnose-Cockpit](Administration-Systemdiagnose) ist im bestehenden
Admin-Menue unter Einstellungen verlinkt. Der
[System-Admin-Ueberblick](Administration-System-Admin-Ueberblick) erklaert die
Einschaltreihenfolge, Rechte und Wiederherstellung.
