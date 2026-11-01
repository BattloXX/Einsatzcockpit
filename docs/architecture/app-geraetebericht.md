# App-Geraetebericht

Die native App meldet Diagnose ausschliesslich per `POST /api/v1/device/diagnose-report`.
Sitzungs-Cookie oder Bearer-Geraetetoken wie bei `/fcm-token`; ohne Authentifizierung 401.
Es gibt keinen Endpunkt zum Aendern oder Ausliefern von App-Einstellungen.

Schema 1 und eine UUID4 als `install_id` sind Pflicht (sonst 400). Alle anderen Felder
werden anhand der Service-Whitelist typisiert, begrenzt oder einzeln verworfen.
Unbekannte Felder werden nie gespeichert. Einstellungen: `el_alarm_tone` (String,
max. 120), `el_alarm_volume_percent` (Integer 0..100), `el_alarm_override_silent`,
`el_live_enabled`, `el_gateway_receive_enabled`, `auto_update` (jeweils bool) und
`base_url_host` (reiner Hostname, max. 253, keine URL). Fehlertexte werden vor dem
Kuerzen auf 200 Zeichen maskiert; auch andere freie Diagnosestrings werden maskiert.

Das Body-Limit gilt auch ohne Content-Length und bei Streaming: mehr als 65536 Bytes
liefern 413. Pro Benutzer/Installation gelten maximal 30 angenommene Berichte im
rollenden Stundenfenster (danach 429). Die interne JSON-Spalte `report_zeiten` und
DB-Zeilensperren halten das Limit workeruebergreifend. Der Benutzer wird zuerst
gesperrt, damit auch gleichzeitige Erstberichte keinen doppelten Datensatz erzeugen.

Nur das tatsaechlich zur Authentifizierung verwendete DeviceToken wird verknuepft
und mit App-Version, Android-Version und Modell aktualisiert. Normale Sitzungen
funktionieren ohne DeviceToken. Benutzer und Organisation stammen immer aus der
Authentifizierung, niemals aus dem Body. `geraet_details` enthaelt die uebrigen
validierten Gruppen; JSON-Gruppen stellen den zuletzt gemeldeten Zustand dar.

Fehler: maximal 50 Eintraege pro Bericht und 200 unterschiedliche
(Geraet, Komponente, Code)-Kombinationen. Nur ein neuerer Zeitstempel addiert die
Anzahl; Wiederholungen und aeltere Meldungen addieren nichts. Bei Ueberlauf entfallen
die aeltesten Eintraege nach `last_at`. Fehler aelter als 90 Tage oder mehr als
fuenf Minuten in der Zukunft werden verworfen. DB-Zeitstempel sind naive UTC;
die Antwort liefert `server_time` mit Z-Suffix.

`purge_app_geraete()` laeuft im bestehenden taeglichen `sms_log_retention_loop`
um 03:40 Europe/Vienna (Leader-Hintergrunddienst). Es loescht Fehler aelter als
90 Tage und Geraete mit `last_report_at` aelter als 90 Tage samt Fehlern, auch ohne
Organisation. Manuell kann die Funktion ebenfalls aus einem System-Kontext
aufgerufen werden. Migration 0339 folgt 0338; Fremdschluessel uebernehmen den
bestehenden ID-Typ einschliesslich MariaDB-Signedness (INT/BIGINT).

## Verbindlicher Payload-Vertrag

# Vertrag Geraetebericht (schema 1)

POST /api/v1/device/diagnose-report   (Auth: Bearer-Geraetetoken ODER Sitzung wie /fcm-token; Body <= 64 KB; JSON)
Antwort: {"ok": true, "geraet_id": int, "server_time": "...Z"}.  Kein Rueckkanal fuer Einstellungen.

Alle Felder ausser schema/install_id optional; Server validiert per Whitelist, kuerzt Strings, verwirft Unbekanntes.

{
 "schema": 1,
 "install_id": "uuid4 (zufaellig, pro Installation, in App-Speicher)",
 "sent_at": "ISO-8601 UTC",
 "app": {"version_name","version_code":int,"build_variant":"auto_update|standard","target_sdk":int,"min_sdk":int,
         "first_install_at","last_update_at"},
 "geraet": {"hersteller","marke","modell","geraetename","klasse":"handy|tablet","foldable":bool,"abi",
            "ram_mb_total":int,"ram_mb_free":int,"speicher_mb_free":int,"akku_prozent":int,"laedt":bool,
            "akkusparer":bool,"sim_anzahl":int,"nfc":bool,"kamera":bool,"telefonie":bool},
 "android": {"version","sdk_int":int,"sicherheitspatch":"YYYY-MM-DD","build_id","oberflaeche"},
 "display": {"breite_px":int,"hoehe_px":int,"dpi":int,"dichte_bucket","breite_dp":int,"hz":number,
             "ausrichtung":"hoch|quer","schrift_skala":number,"anzeige_skala":number},
 "webview": {"paket","version"},
 "locale": {"sprache","zeitzone","uhr_abweichung_s": int|null},
 "einstellungen": { "<whitelist-key>": bool|int|string },   // nur nicht-geheime App-Einstellungen
 "berechtigungen": {"benachrichtigungen":bool,"akku_optimierung_aus":bool,"vollbild_intent":bool,
                    "dnd_zugriff":bool,"standort":"nein|vordergrund|immer","sms":bool,"netz":"wlan|mobil|keins"},
 "dienste": {"poller_laeuft":bool,"keepalive":bool,"gateway_verbunden":bool,"fcm_registriert":bool,
             "fcm_faehigkeiten":[str],"device_token_vorhanden":bool,"gateway_token_vorhanden":bool,
             "letzter_poll_ok_at","letzte_push_bestaetigung_at","letzter_kontaktsync_at"},
 "fehler": [ {"ts","komponente","code","text":"<=200 Zeichen maskiert","anzahl":int} ]   // max 50
}

Datenschutz: NIE IMEI, Seriennummer, Telefonnummer, SIM-ID, Werbe-ID, MAC, Standort, Tokens, SMS-Texte,
vollstaendige URLs (nur Host). Server maskiert Fehlertexte zusaetzlich (Telefonnummern, Tokens, E-Mails, URLs-Query).

## Zentrale Auswertung (B2/B3)

`app_geraet_auswertung` liefert rein lesend Liste, Verteilungen, CalVer-Ampel und
Gesundheitsregeln. Der Aufrufer setzt den Tenant-Kontext; nur die persoenliche
Systemadmin-Diagnose hebt die Organisationsgrenze explizit auf. Soll-Version und
APK teilen GitHub-Abruf, Erfolgscache (1 Stunde) und Fehlercache (5 Minuten).
Ein Ausfall ergibt die Ampel unbekannt und keinen Versionsbefund.

Systemstatus > Systemdiagnose > App-Geraete zeigt die Flotte und Details ohne
Schreibaktionen. Der Aufruf eines Details erzeugt `diag.ui.geraet` im Audit.
Der leichte Check `app.geraete` aggregiert Regelverletzungen und Fehler-Cluster,
begrenzt IDs und DB-Zeilen mit dem Diagnosebudget. Push-Korrelation erfolgt nur
ueber `modul=push` in der Cluster-Evidenz, ohne Abhaengigkeit zum Push-Service.

Die Fehleranzahl wird von der App kumuliert gemeldet. Die 7-Tage-Regel bedeutet
somit: kumulierte Anzahl >= 5 und zuletzt innerhalb der letzten 7 Tage gemeldet;
eine exakte Anzahl einzelner Fehler innerhalb des Zeitfensters ist mit Schema 1
nicht rekonstruierbar. Dienstaktiv bedeutet: verknuepftes, nicht widerrufenes
DeviceToken mit `duty_active=true`. Die 12-Monatsgrenze nutzt Kalendermonate.

## Admin-MCP (B4)

Unter `/mcp/admin` lesen `admin_geraete_pruefen` und `admin_geraet_details` die
zentrale Auswertung, ohne eigene Gesundheitsregeln oder Schreibaktionen.
Die Liste akzeptiert `org_id`, `nur_probleme`, `sdk_max`, `limit` (1..100,
Standard 50); Aggregate und Status beziehen sich auf die gesamte gefilterte
Auswahl. Verteilungen und Cluster sind auf `limit`, Geraetelisten und
Cluster-/Berechtigungs-IDs zusaetzlich auf `MAX_IDS=50` begrenzt. Gesamtzahlen
und Begrenzungsflags bleiben erhalten. Details zeigen alle gemeldeten
Einstellungen nur lesend und die neuesten 50 maskierten Fehler samt Befunden.
Eine unbekannte positive Integer-ID liefert `geraet: null`; Bool-IDs sind ungueltig.

Die Tools verwenden den bestehenden Admin-Audit-/Korrelationspfad und behalten
den expliziten Tenant-Kontext der Session. Nur IDs werden referenziert, keine
Benutzer-/Organisationsnamen, Installations-UUIDs oder frei vergebenen Geraetenamen.
Freitexte werden nochmals mit `admin_mcp.masking` maskiert. Ein GitHub-Ausfall
liefert Soll-Version `null` mit Hinweis, ohne Versionsbefund. Die Tools sind
in `ADMIN_TOOLS` und `READ_TOOLS`, aber explizit von Systemuebersicht und
Gesamtzustand-Panels ausgeschlossen. Vertrag und Grenzen siehe
[Admin-MCP](../wiki/Administration-System-Admin-MCP.md#app-geraete).

## Standortmeldung der Geraete (24-h-Ziel)

`POST /api/v1/device/location` (Session oder Bearer-Geraetetoken) nimmt zwei Arten von Meldungen an:

- **Live-Tracking im Einsatz** (WebView, Background-Geolocation-Watcher) und
- **Periodische Meldung ohne Einsatz** (Android-App, WorkManager `location-report-periodic`, alle 6 h mit 2 h Flex,
  Netzwerk-Constraint; zusaetzlich einmalig bei Einsatzbeginn und per Knopf in der Einstellungsseite).

Felder: `lat`, `lng`, `accuracy`, `measured_at` (UTC-Messzeit), `source` (`live|periodic|incident|manual`),
`status` (`ok|permission_denied|background_permission_missing|location_disabled|no_fix|stale_fix|mock_location|error`),
`error` (maskiert). Ohne `lat/lng` wird nur der Status festgehalten (verstaendliche Meldung statt Schweigen).

Regeln (`app/services/geraet_standort.py`):

- `last_location_at` ist die **Messzeit**, `last_location_received_at` der Empfang. Eine erfolgreich uebertragene alte
  Position gilt nie als frisch: aelter als 24 h wird sie verworfen (`stale_fix`), aelter als 15 min erzeugt sie weder
  Fahrzeughistorie noch WebSocket-Broadcast.
- Zukunftszeiten werden auf den Empfang begrenzt, aeltere Messungen ueberschreiben neuere nicht.
- Veraltet = keine gueltige Messung in 24 h. Sichtbar im Geraete-Login (Liste: Warnsymbol, Detail: Standortstatus,
  Messalter, Empfang, letzter Versuch, Fehler) und im Diagnose-Check `app.standort` (`standort_veraltet`,
  `standort_blockiert`); aeltere Clients ohne `measured_at` funktionieren unveraendert (Messzeit = Empfang).
- Android-Seite: gemeinsam mit dem Geraetetoken; ohne Token keine Ortung; Retry mit Backoff (max. 3 Versuche je Lauf),
  Neustart-fest durch WorkManager plus Boot-/Push-Aufrufe von `ensureScheduled`.
