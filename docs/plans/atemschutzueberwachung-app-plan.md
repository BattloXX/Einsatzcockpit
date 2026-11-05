# Atemschutzüberwachung (ASÜW): Analyse und Verbesserungsplan mit App-Stabilisierung

Stand: 2026-11-05 · Status: **Plan, Entscheidungen getroffen (Abschnitt 8), noch nicht umgesetzt**

## 1. Ziel

Die Atemschutzüberwachung ist eine sicherheitskritische Funktion: **sie darf nicht ausfallen**.
Fällt sie trotzdem aus (Netz, Server, Gerät, Akku), muss das sofort und laut auffallen, und
die zuständigen Personen müssen benachrichtigt werden. Damit diese Zusagen technisch halten,
läuft der Überwachungsmodus künftig **nur in der Einsatzcockpit-Android-App**. Nur dort sind
Alarmton auf dem Alarm-Audiostream, Foreground-Service, exakte Wecker, Wake-Lock und
Offline-Puffer verfügbar.

Fachliche Grundlage für den Ablauf sind die Unterlagen des FAZ / LFV Vorarlberg:

- **Lehrbehelf Atemschutz** (FAZ Vorarlberg), vor allem S. 26–36: Einsatzdauer, Rückzugszeitpunkt,
  Warneinrichtung, Truppführer, Rettungstrupp, ASSP, „Nach dem Einsatz"
- **Falter „Atemschutz – ASÜW & ASSP"** (LFV Vorarlberg)
- **Formular „Atemschutzüberwachung Hochformat"** (FAZ, 11.04.2016)

Kernaussagen daraus, an denen sich das Modul ausrichten muss:

| Regel | Quelle |
|---|---|
| Die ASÜW übernimmt der GRKDT. Er kann sie an Maschinist oder Melder übertragen. | Falter, Lehrbehelf S. 36 |
| Kommt nach **1/3 der zu erwartenden Einsatzzeit** keine Lage- und Druckmeldung, fragt die überwachende Person den **niedrigsten Flaschendruck** des Trupps ab. | Falter |
| Zu erwartende Einsatzzeit **20–30 min** (ca. 80 l/min). Formular: Kontrolle **nach 10 min** und **nach 20 min**. | Falter, Formular, Lehrbehelf S. 30 |
| **Rückzug = Luftverbrauch Anmarsch × 2.** Beispiele: 300 bar, Anmarsch 30 → Rückzug bei 60 bar. 270 bar, Anmarsch 90 → Rückzug bei 180 bar (sofort). | Lehrbehelf S. 28–29 |
| Bei kurzem Anmarsch spätestens bei der **Warneinrichtung (50–60 bar)** raus. Das erste Warnsignal heißt Rückzug des **gesamten** Trupps. | Lehrbehelf S. 28/30 |
| Kein Funkkontakt: GRKDT verständigen, er entscheidet über den Rettungstrupp. Der Trupp im Innenangriff tritt den Rückzug an. | Falter |
| **Rettungstrupp ab Einsatzbeginn**. **Ab 3 gleichzeitig eingesetzten Trupps ASSP** alarmieren. | Lehrbehelf S. 34/36 |
| Der ASSP ist nur Logistik und überwacht nicht. Der Leiter ASSP informiert die EL über eingesetzte und verfügbare Trupps. | Lehrbehelf S. 36, Falter |
| Nach dem Einsatz: Rückmeldung an den GRKDT, **mindestens 30 min Pause**, Einsatzhygiene. | Lehrbehelf S. 35 |
| Innerhalb eines Trupps nur gleiche Geräte. Jeder Träger prüft sein Gerät selbst. | Falter |

## 2. Ist-Stand (Bestand)

| Datei | Zeilen | Inhalt |
|---|---|---|
| `app/models/breathing.py` | 284 | `BreathingTroop`, `BreathingDeployment` (Zyklus-Snapshot), `TroopMember`, `PressureLog`, Flaschen-Presets |
| `app/services/breathing_service.py` | 678 | Rückzugsdruck, Status, Readiness (Sicherheitstrupp/90 %), Zeitwarnungen, Eskalation, Watchdog-Loop (5 s) |
| `app/routers/ui_breathing.py` | 483 | Board, Trupp anlegen/erneut einsetzen, Start, Status, Druck, Einsatzziel, Enddruck, Meldung, Ack, Standort, Auftrag, PDF |
| `app/templates/breathing/board.html` | 1054 | Board mit Gauge, Timer, Druck-Picker, Audio, WebSocket, Wake-Lock-Hacks, Auto-Bildschirmschoner |
| `tests/test_breathing*.py` | 673 | 32 Tests, überwiegend Service-Logik |
| Android `Einsatzcockpit-Android` | – | **kein** ASÜW-Code. Nutzbar sind `EinsatzAlarmService` (USAGE_ALARM), `DeviceKeepaliveService`, `EinsatzLivePoller`, FCM-Service mit `alarm=1`, `@capacitor-community/keep-awake` |

Gute Basis: Zyklus-Archivierung, Readiness-Override mit Begründung und Audit, je Mitglied
berechnete Druckwarnung, Quittierungen mit Zeitstempel und die einmalige Eskalation bei
Max-Zeit (ÖBFV M302).

## 3. Befunde

Schweregrad: **K** = kritisch (Sicherheit oder Ausfall), **F** = fachlich/Ablauf,
**T** = technisch, **U** = Usability.

### 3.1 Kritisch

**K1: Der Auto-Bildschirmschoner schaltet die Überwachung ab.**
Nach 3 min ohne Touch navigiert das Board auf `/einsatz/{id}/screensaver`
(`board.html:1032-1052`). Dort wird `troop_warning` nicht ausgewertet, es gibt kein Audio und
keine Trupp-Anzeige (`incident/screensaver.html:219-224`). Genau in der typischen Phase
„Trupp ist drinnen, Überwacher wartet" laufen damit 1/3-, 2/3-, Max- und Rückzugswarnungen
lautlos ins Leere. Der `beforeunload`-Schutz wird dabei absichtlich umgangen (`_breathingReload = true`).

**K2: Die Rückzugsdruck-Formel am Einsatzziel ist falsch.**
`report_objective_reached` rechnet `withdraw = max(reserve, 2 × p_ziel − p_start)`
(`breathing_service.py:398`, Test `test_breathing.py:265` bestätigt diese Formel). Der
Lehrbehelf verlangt **Rückzug bei 2 × Anmarschverbrauch = 2 × (p_start − p_ziel)**:

| Beispiel Lehrbehelf | richtig | Code heute | Folge |
|---|---|---|---|
| 300 bar, Anmarsch 30 (Ziel 270) | 60 bar | 240 bar | Rückzug viel zu früh, Einsatzwert verschenkt |
| 270 bar, Anmarsch 90 (Ziel 180) | **180 bar = sofort** | 90 bar | **Rückzug viel zu spät, Luft reicht für den Rückweg nicht** |

Je länger der Anmarsch, desto gefährlicher wird die falsche Formel. Das muss sofort behoben werden.

**K3: Warnungen erreichen nur eine offene, sichtbare Seite.**
Der Watchdog sendet `troop_warning` ausschließlich per WebSocket (`breathing_service.py:669-674`).
Ist die App im Hintergrund, das Display aus, der Bildschirmschoner aktiv (K1) oder die
WS-Verbindung weg, gibt es **keine** Warnung. Einziger Push ist die Max-Zeit-Eskalation an
`incident.incident_leader_user_id`, und das erst nach der Karenzzeit. Ist kein Einsatzleiter
gesetzt, entfällt sie ganz (`:581`). Rückzugsdruck und überfällige Lagemeldungen eskalieren
nie.

**K4: Der Client berechnet keine eigenen Warnungen.**
Timer und Balken laufen zwar lokal, Warnungen und Ton kommen aber nur vom Server. Bei Netz-
oder Serverausfall (Neustart, Leader-Wechsel, WLAN-Wechsel) bleibt das Gerät stumm, während
die Uhr scheinbar normal weiterläuft. Das wiegt sich in falscher Sicherheit.

**K5: Verbindungsverlust ist nicht sichtbar.**
`ws.onclose` verbindet nach 3 s stumm neu (`board.html:985-990`). Es gibt keinen Status, kein
Banner und keinen Ton. Pro Verbindung entsteht außerdem ein neues `setInterval` für den Ping,
das nie geräumt wird. Das ist ein Leak, und nach vielen Reconnects pingen tote Sockets mit.

**K6: Eingaben werden optimistisch angezeigt, Fehler werden verschluckt.**
Der Druck-Button setzt die Anzeige **vor** der Serverantwort (`board.html:307`, `hx-swap="none"`,
keine Fehlerbehandlung). Bei 400, 5xx oder offline zeigt das Gerät einen Druck an, der nie
gespeichert wurde. Die Lagemeldung gilt lokal als erfolgt, am Server nicht. `ackWarning` stoppt
den Ton lokal und ignoriert Fehler (`:648-654`). Die Quittierung geht also verloren, und die
Warnung bleibt serverseitig offen, ohne dass es jemand merkt.

**K7: Watchdog-Fehler sind unsichtbar, Zustand liegt nur im Speicher.**
`except Exception: pass` (`breathing_service.py:677`) schluckt jeden Fehler ohne Log. Es gibt
keinen Heartbeat und keine Anbindung an `dienst_monitor`/Diagnose. Ein dauerhaft scheiternder
Watchdog fällt niemandem auf. Die bereits gesendeten Warnungen (`sent`) liegen nur im Speicher
des Leader-Workers.

**K8: Atemschutznotfall und „Funkkontakt verloren" fehlen.**
Es gibt keinen Notfall- bzw. Mayday-Status, keine Erfassung „kein Funkkontakt", keinen Ablauf
„Sicherheitstrupp/Rettungstrupp entsenden" und keine Sofortalarmierung von EL und GRKDT.
Gerade für diesen Fall braucht es das System am dringendsten.

### 3.2 Fachlich / Ablauf

**F1: Die Kontrollzeitpunkte hängen an der Flaschengröße statt an der zu erwartenden Einsatzzeit.**
Die Presets setzen `planned_duration_min` auf 33/37/50 min, 1/3 und 2/3 werden daraus
abgeleitet. Bei 9 l kommt die erste Kontrolle damit erst nach 16:40 min, das Formular
verlangt sie **nach 10 min**. Die Max-Zeit von 50 min liegt weit über den 20–30 min aus der
Ausbildung. 200-bar-Flaschen und 2 × 4 l (1600 l, Lehrbehelf S. 28) fehlen, alle Presets
setzen 300 bar voraus.
→ Org-Einstellung **„zu erwartende Einsatzzeit"** (Vorgabe 30 min) und Kontrollen bei 1/3 und
2/3 davon (10/20 min). Die Flasche liefert nur Luftvorrat und Plausibilität (theoretische
Maximaldauer als Zusatzinfo).

**F2: Der vorläufige Rückzugsdruck folgt nicht der Vorarlberger Ausbildung.**
Heute gilt `start × 0,5 + 10` (`calc_withdraw_pressure`). Der Lehrbehelf kennt vor dem
Einsatzziel nur die **Warneinrichtung (50–60 bar)** als letzte Grenze. → Vorläufige Grenze
konfigurierbar machen (Vorgabe: org-weiter Wert, z. B. 60 bar oder ½+Reserve) und nach
„Einsatzziel erreicht" die Formel aus K2 anwenden. Hängt dazu noch der Anmarsch nach, bleibt
der höhere Wert maßgeblich.

**F3: Lagemeldung ohne Druck setzt die 1/3-Warnung zurück.**
`update_meldung` setzt `last_meldung_at` auch ohne Druckwert. Laut Falter muss die Meldung
aber Lage **und** niedrigsten Flaschendruck enthalten. → Eine kombinierte Meldung
„Lage + niedrigster Druck" wird zum Standard. Freitext allein gilt nicht als Kontrolle.

**F4: Quittieren ersetzt die Kontrolle.**
Ein Klick auf „Quittieren" bei 1/3 oder 2/3 beendet die Warnung, ohne dass ein Druck
erfasst wird. → Die Warnung wird über „Druck abgefragt: ___ bar" erledigt. Alternativen:
„kein Funkkontakt" (löst K8-Ablauf aus) oder „später" (Snooze max. 2 min, protokolliert).

**F5: Der Sicherheitstrupp-Check zählt eingesetzte Sicherheitstrupps als Bereitschaft.**
`check_start_readiness` akzeptiert `status in ("bereit", "im_einsatz")`
(`breathing_service.py:85`). Ein Sicherheitstrupp, der selbst im Einsatz ist, steht aber
nicht mehr bereit. → Nur `bereit` zählt. Wird ein Sicherheitstrupp eingesetzt, warnt das
System, dass kein Rettungstrupp mehr bereitsteht.

**F6: Es gibt keine Erholzeit.**
`zurueck` → `erholt` geht sofort, ebenso `erneut einsetzen`. Der Lehrbehelf verlangt
mindestens 30 min Pause. → Erholzeit-Countdown nach `back_at`. Ein früher Wiedereinsatz ist
nur als Readiness-Warnung mit Begründung möglich (gleiches Muster wie beim heutigen Override).

**F7: Es fehlt die Zuständigkeit.**
Ein Trupp hat keine überwachende Person und kein überwachendes Gerät. Alle Board-Clients
bekommen alle Warnungen, und die Verantwortung ist unklar. → Pro Trupp wird eine
**überwachende Person** (GRKDT/Maschinist/Melder) samt **Überwachungsgerät** gespeichert, mit
expliziter Übergabe (Audit). Das ist auch Pflichtfeld „Überwacher" im Formular.

**F8: Formularfelder fehlen.**
Formular-Felder sind Funkrufname, Truppführer, Namen, Druck bei Einsatzbeginn + Uhrzeit,
**Einsatzziel und Angriffsweg**, 1. und 2. Kontrolle mit Uhrzeit, Bemerkungen, Einsatzort,
Datum und Überwacher. Es fehlen „Angriffsweg", „Überwacher" und die Kontrolle als eigener,
benannter Datensatz. Das PDF (`pdf/_troop_block.html`) sollte dem Vorarlberger Formular
entsprechen, also bis zu 3 Trupps pro A4-Seite.

**F9: Es fehlen ASSP-Unterstützung und eine Übersicht für die EL.**
Ab 3 gleichzeitig eingesetzten Trupps fehlt ein Hinweis „ASSP alarmieren". Es gibt keine
ASSP-Sicht (registrierte, bereite und verfügbare Trupps, Flaschenpool) und keine kompakte
Übersicht im Einsatz-Dashboard für die EL.

**F10: Personen- und Gerätechecks fehlen.**
Dieselbe Person kann in zwei aktiven Trupps stehen. Die AGT-Gültigkeit (Untersuchung,
2 Übungen/Jahr laut Lehrbehelf S. 26), die Geräte-Nr. bzw. der Prüfstatus aus dem
vorhandenen Modul `atemschutz_pruefung` und die Regel „gleiche Geräte im Trupp" werden nicht
geprüft oder erfasst.

### 3.3 Technisch

**T1: Vier Routen prüfen die Einsatzzugehörigkeit nicht.**
`/meldung`, `/ack`, `/standort` und `/auftrag` laden den Trupp ohne
`troop.incident_id == incident_id` (`ui_breathing.py:385, 409, 428, 448`). Ein Trupp aus
Einsatz A lässt sich über die URL von Einsatz B ändern, und der Broadcast landet im falschen
Einsatz. Innerhalb der Org schützt der Tenant-Filter nicht.

**T2: Es gibt keine Zustandsmaschine.**
`update_troop_status` nimmt jeden Status an, nur abgesichert mit `assert` (entfällt bei
`python -O`). Ein Doppelklick oder zwei Geräte lösen doppelte Übergänge aus und überschreiben
`back_at`/`withdraw_at`. Es gibt keinen Zeilen-Lock (`SELECT … FOR UPDATE`) und keine
erwartete Version. → Erlaubte Übergänge zentral definieren, `expected_status` bzw. `version`
mitsenden, bei Konflikt 409 zurückgeben und die Karte neu laden.

**T3: Eingaben werden nicht validiert.**
Druckwerte werden ohne Bereichsprüfung gespeichert (0–330 bar, ≤ Anfangsdruck + Toleranz).
`float()` auf Formularwerte erzeugt bei Unsinn einen 500er. Druckmeldungen sind auch im
Status `bereit` möglich und werden beim Start überschrieben.

**T4: Das Audit ist lückenhaft.**
`log_pressure` schreibt kein `write_incident_change`, Standort und Auftrag auch nicht, und
`/auftrag` sendet keinen Broadcast. Für ein Einsatzprotokoll mit Beweiswert muss jede
Mutation mit Benutzer, Gerät und Zeit protokolliert werden.

**T5: Der Vollreload erzeugt Leaks und unterbricht die Bedienung.**
Fast jedes WS-Event löst `doBreathingReload()` aus und ersetzt `#troopsGrid` komplett:

- Offene Picker und Dialoge schließen sich, die Eingabe mitten im Funkgespräch geht verloren.
- Jede `troopCard`-Instanz startet ein `setInterval` und hängt `window`-Listener an, die nie
  entfernt werden (`board.html:820, 823, 753`). Nach einer Stunde Einsatz mit vielen Events
  laufen Dutzende Timer und Listener, das kostet CPU und Akku und verdoppelt Handler.

→ Pro Karte gezielt tauschen (`/atemschutz/{troop_id}/karte`) und Timer zentral
aufräumen. Das deckt sich mit der Pflicht „gezielter Swap" aus `CLAUDE.md`.

**T6: Zeitbasis und Uhrabweichung.**
Die Timer rechnen mit der Geräteuhr gegen die Server-Zeitstempel. Bei Abweichung zeigt der
Timer falsche Werte. → Bei Laden und Heartbeat einen Server-Zeitoffset ermitteln und alle
Countdowns über `serverNow()` rechnen.

**T7: Audio und Wachhalten im Web sind unzuverlässig.**
HTML-Audio braucht einen Entsperr-Klick, spielt auf dem Medienstream (bei Lautstärke 0 oder
im Lautlos-Modus nicht hörbar) und stirbt im Hintergrund. Das Wachhalten kombiniert ein
stilles Video, WakeLock und `ELNative.keepAwake`. `native-bridge.js` ruft im
Energiesparmodus aber selbst `keepAwake(false)` auf (`:145, :621`) und kann damit das
Wachhalten des Boards aufheben.

**T8: Doppelter Code.**
Mitgliedsparsing und Preset-Logik stehen doppelt in `create_breathing_troop` und
`redeploy_troop_view`. Der Statusname-Mapping-Dict ist im Template hart codiert, obwohl
`TROOP_STATUS_LABELS` existiert.

**T9: Die Tests decken zu wenig ab.**
Es gibt keine Routen-Tests für Rollen, Cross-Incident, Zustandsübergänge, Ack-Fehler und
die Watchdog-Benachrichtigung. Es gibt keine JS-Tests für Timer, Offline und
Fehlerbehandlung und keine Android-Logiktests.

### 3.4 Usability

- **U1:** Bedienelemente sind zu klein für Handschuhe und Stress. Buttons haben 0,78 rem, der
  Druck-Picker hat 30 Mini-Buttons in einem scrollenden Popup von 280 px.
  → Einsatzmodus mit großen Kacheln (≥ 56 px Touchziele), Druckeingabe als großes Raster
  bzw. Ziffernblock mit „niedrigster Druck des Trupps" in einem Schritt.
- **U2:** Warnungen sind kleine Badges im Kopf der Karte.
  → Eine Vollbreite-Alarmleiste oben (sticky) mit klarer Aktion („Druck abfragen"), dazu ein
  Kartenrahmen in der Warnfarbe.
- **U3:** Das Gauge ist irreführend. Die Farbzonen sind fix in Dritteln und nicht am
  Rückzugsdruck ausgerichtet, der Text zeigt den niedrigsten Druck ohne Namen.
  → Balken pro Person mit Rückzugsmarke, der niedrigste Wert hervorgehoben.
- **U4:** Sortierung. Aktive Trupps sollen nach Dringlichkeit oben stehen, erholte und
  abgeschlossene zugeklappt unten.
- **U5:** Trupp anlegen ist umständlich. Es gibt ein Dropdown aller AGT ohne Suche, und
  Fehler erscheinen als `alert()`.
  → Personen-Flyout aus PR #666 wiederverwenden (Suche, AGT-Status, „bereits in Trupp X").
  Funkrufname und Überwacher vorbelegen (eigene Einheit, angemeldeter Benutzer).
- **U6:** Es fehlt eine Zeitachse. → Pro Trupp eine kompakte Timeline (Eintritt, 1. und
  2. Kontrolle mit Druck, Einsatzziel, Rückzug, zurück) im Stil des Formulars.
- **U7:** Querformat-Tablet. Zwei bis drei Trupps nebeneinander wie auf der Formulartafel,
  am Handy einspaltig mit fixierter Alarmleiste.

## 4. Zielbild

### 4.1 Nur in der App: der Überwachungsmodus

```
 ┌──────────────── Android-App ─────────────────────────────┐
 │ WebView: ASÜW-Board (Bedienung, Darstellung)             │
 │    │ ELNative.asuew.* (Bridge)                           │
 │ ┌──▼─────────────────────────────────────────────────┐   │
 │ │ AtemschutzWatchService (Foreground, nativ)          │   │
 │ │ • lokaler Zustand: Eintritt, Schwellen, Drücke      │   │
 │ │ • exakte Wecker (AlarmManager.setAlarmClock)        │   │
 │ │ • Alarmton USAGE_ALARM + Vibration + Vollbild       │   │
 │ │ • Dauerbenachrichtigung mit Trupp-Uhren             │   │
 │ │ • Heartbeat + Zustands-Poll (OkHttp, 10–15 s)       │   │
 │ │ • Offline-Outbox (idempotent)                       │   │
 │ └──────────────────────┬──────────────────────────────┘   │
 └────────────────────────┼──────────────────────────────────┘
                          │ HTTPS (+ WS für Live-Darstellung)
 ┌────────────────────────▼──────────────────────────────────┐
 │ Server: ASÜW-API + Watchdog (Leader) + Benachrichtigungs- │
 │ Outbox → FCM (Alarmkanal) / SMS / Teams an EL, GRKDT,     │
 │ weitere Überwacher; Heartbeat-Überwachung der Geräte      │
 └───────────────────────────────────────────────────────────┘
```

Grundsatz: **Server und Gerät überwachen sich gegenseitig, beide warnen unabhängig
voneinander.** Fällt das Netz aus, warnt das Gerät lokal weiter. Fällt das Gerät aus (Akku
leer, App beendet, Gerät verloren), merkt das der Server am fehlenden Heartbeat und alarmiert
andere.

### 4.2 Wie die App-Bindung gesichert wird

Das `ec_native`-Cookie bzw. `?native=1` ist bewusst unsicher (nur UI-Steuerung, `main.py:643`)
und **reicht dafür nicht**. Vorschlag:

1. **Überwachungssitzung (`asuew_session`)**: Beim Öffnen des Boards startet die App nativ
   (OkHttp mit Geräte-/Benutzersitzung) `POST /api/asuew/sitzung`. Mitgesendet werden der
   FCM-Token mit der Capability `asuew` (neu, analog zu `native_alarm`), die App-Version und
   das Ergebnis des Selbsttests. Der Server bindet die Sitzung an Benutzer, Einsatz und
   FCM-Token und gibt eine kurzlebige Sitzungs-ID zurück.
2. Alle **schreibenden** ASÜW-Routen verlangen eine gültige Sitzung mit frischem Heartbeat
   (≤ 60 s). Die WebView sendet die ID über einen HTMX-Header (`X-ECP-ASUEW`). Die Bridge
   setzt ihn nur, wenn das native Plugin antwortet.
3. **Mindestversion** serverseitig (`ASUEW_MIN_APP_VERSION`). Ältere Apps zeigen
   „Bitte App aktualisieren" (Auto-Update existiert).
4. **Browser** (Desktop, PWA, iOS-Safari): Hinweisseite „Atemschutzüberwachung nur in der
   Einsatzcockpit-App" mit QR-Code bzw. Deep-Link. Optional gibt es eine **Nur-Lese-Ansicht**
   für EL und Stab ohne Quittierung und Eingabe (offene Entscheidung, siehe Abschnitt 8).
5. **Übergabe**: Eine zweite App kann einen Trupp ausdrücklich übernehmen
   („Überwachung übernehmen"). Erst nach Bestätigung wechselt die Zuständigkeit, mit Audit
   und Benachrichtigung an das alte Gerät.

### 4.3 Vor dem Start: Selbsttest

Bevor der erste Trupp eingesetzt wird, prüft die App und zeigt eine Ampel:

- Benachrichtigungen erlaubt, Vollbild-Intent (Android 14+), exakte Wecker
  (`SCHEDULE_EXACT_ALARM`/`USE_EXACT_ALARM`), Akku-Optimierung ausgenommen
- Alarmlautstärke > 0, Testton auf dem Alarm-Stream („Ton gehört?"), Bitte-nicht-stören
  wird durch die Alarm-Kategorie umgangen
- Akku ≥ 30 % oder Ladegerät angeschlossen (unter 20 % während der Überwachung: Warnung)
- Serververbindung und Uhrabweichung < 5 s
- Der Selbsttest wird pro Sitzung protokolliert. Bei Rot ist ein Start nur mit Begründung
  möglich (Override-Muster).

## 5. Benachrichtigungsmatrix

| Ereignis | Gerät lokal (sofort) | Server an Überwachungsgerät(e) | Eskalation | Kanal |
|---|---|---|---|---|
| 1/3- bzw. 2/3-Kontrolle fällig | Ton + Vibration, Alarmleiste | FCM-Alarm, falls nach 60 s nicht erledigt | GRKDT der Einheit nach 2 min | FCM Alarmkanal |
| Rückzugsdruck erreicht/unterschritten | Dauerton bis Bestätigung „Rückzug befohlen" | FCM-Alarm sofort | EL nach 60 s ohne Bestätigung | FCM Alarmkanal |
| Max. Einsatzzeit überschritten | Dauerton | FCM-Alarm sofort | EL + GRKDT nach Karenz (heute 3 min) | FCM Alarmkanal, SMS-Fallback |
| **Kein Funkkontakt** (manuell gemeldet) | Dauerton, Ablauf-Assistent | – | **sofort** EL + GRKDT + Führer des Sicherheitstrupps | FCM Alarm + SMS |
| **Atemschutznotfall / Mayday** | Vollbild-Alarm | an alle Überwachungsgeräte des Einsatzes | **sofort** EL, GRKDT, Sicherheitstrupp; Teams optional | FCM Alarm + SMS + Teams |
| Überwachungsgerät ohne Heartbeat > 60 s bei aktivem Trupp | (Gerät tot) | – | EL + andere Überwacher sofort, SMS nach 3 min | FCM + SMS |
| Gerät offline (Netz weg) | Banner + Vibration, „lokal weiter, Eingaben werden gepuffert" | – | wie Heartbeat-Ausfall | – |
| Akku < 20 % während Überwachung | Warnung „Ladegerät/Übergabe" | – | Hinweis an EL bei < 10 % | FCM normal |
| Server-Watchdog ohne Lauf > 30 s | – | – | Sysadmin/Diagnose (dienst_monitor) | bestehender Admin-Alarm |
| 3 Trupps gleichzeitig im Einsatz | Hinweis „ASSP alarmieren" | – | EL | FCM normal |
| Kein bereiter Sicherheitstrupp, Trupp im Einsatz | Dauerhinweis auf Board | – | EL einmalig | FCM normal |
| Wiedereinsatz vor 30 min Erholzeit | Readiness-Warnung | – | – | – |

Regeln gegen Alarmmüdigkeit: Pro Trupp, Ereignis und Zyklus wird nur einmal gesendet
(persistente Dedupe-Tabelle statt `sent`-Dict). Wird eine Warnung lokal erledigt, entfernt
das die Benachrichtigungen auf allen Geräten. In Übungen gibt es ein klar gekennzeichnetes
„ÜBUNG" ohne SMS/Teams (wie bei den bestehenden Alarm-Übungsschaltern).

## 6. Umsetzungsplan (Phasen und PR-Schnitt)

Jede Phase ist ein eigener PR mit Tests, Doku (Wiki `Anwender-Atemschutzueberwachung.md`),
`CHANGELOG.md` und allen vier CI-Checks. Backend-Repo = **B**, Android-Repo = **A**.

### Phase 0: Sofortmaßnahmen (B, klein, vor allem anderen)

1. **K2** Rückzugsdruck-Formel auf `max(reserve, 2 × (p_start − p_ziel))` umstellen. Test mit
   den drei Lehrbehelf-Beispielen (300/30 → 60, 280/25 → 50, 270/90 → 180) statt des
   heutigen Tests.
2. **K1** Den Auto-Bildschirmschoner auf dem ASÜW-Board entfernen, solange Trupps `bereit`,
   `im_einsatz` oder `rueckzug` sind. Zusätzlich auf der Bildschirmschoner-Seite
   `troop_warning` auswerten (Ton + Rücksprung) als zweite Absicherung.
3. **F5** Der Sicherheitstrupp zählt nur im Status `bereit`.
4. **T1** In allen Routen `troop.incident_id == incident_id` prüfen, mit Test pro Route.
5. **T2** (Minimalversion) Erlaubte Statusübergänge prüfen und `assert` durch `ValueError`/409
   ersetzen.
6. **T3** Druck 0–330 bar validieren, sauberer 400er statt 500er.
7. **K6** Druck, Ack und Meldung: Anzeige erst nach Erfolg, bei Fehler eine rote Meldung
   „Nicht gespeichert – erneut senden" und der Ton läuft weiter.
8. **K5** Verbindungsstatus-Anzeige (grün/gelb/rot, „seit 00:23 getrennt"), Backoff beim
   Reconnect, Ping-Intervall räumen, nach Reconnect den Zustand neu laden.
9. **K7** Watchdog loggt Fehler (`logger.exception`, gedrosselt) und schreibt einen Heartbeat
   (Zeitstempel), den `dienst_monitor` prüft.
10. **T5** (Minimalversion) Intervalle und Listener in `troopCard`/`memberPressRow` beim
    Neuaufbau abräumen (Alpine `destroy`).

### Phase 1: Fachmodell und Ablauf (B, Migration)

- Org-Einstellungen (Tab im bestehenden Verwaltungs-Hauptpunkt, siehe `admin_navigation.py`):
  zu erwartende Einsatzzeit (30 min), Kontrollen (1/3, 2/3), vorläufige Rückzugsgrenze,
  Mindestreserve/Warneinrichtung, Gelbschwelle, Erholzeit (30 min), Eskalations-Karenzen,
  Flaschen-Presets (200/300 bar, 6/6,8/9 l, 2 × 4 l).
- `BreathingTroop`: `ueberwacher_user_id`, `ueberwacher_name` (Freitext Maschinist/Melder),
  `angriffsweg`, `funkkanal`, `version` (optimistische Sperre), neue Status `funk_verloren`
  und `notfall` (bzw. ein eigenes Flag mit Zeitstempel).
- **Kontrolle als Datensatz** (`breathing_kontrolle`): Stufe (1/3, 2/3, frei), fällig um,
  erledigt um, niedrigster Druck, wer, wie (Funk/Snooze/kein Kontakt). Das ersetzt die
  reinen `warn_*_acked_at`-Felder im PDF und in der Timeline.
- Kombinierte Meldung „Lage + niedrigster Druck" (F3/F4). Freitext allein erledigt keine
  Kontrolle.
- Erholzeit (F6), Doppelbelegungs-Check (F10), optional Geräte-Nr. mit Verknüpfung zu
  `AtemschutzGeraet` und Prüfstatus-Warnung.
- Vollständiges Audit für jede Mutation inklusive Gerät/Sitzung (T4).
- Gemeinsamer Parser für Mitglieder und Presets (T8).

### Phase 2: Serverseitige Benachrichtigung und Eskalation (B)

- Tabelle `breathing_warnung` (Trupp, Zyklus, Art, ausgelöst, erledigt, eskaliert,
  Zustellungen). Sie ersetzt das In-Memory-`sent` und funktioniert damit auch über einen
  Worker- oder Leader-Wechsel hinweg (Lektion Multi-Worker).
- Zustellung über eine Outbox nach dem bewährten Muster `alarm_outbox` (Retry, Status,
  Diagnose). Empfänger aus der Matrix: Überwachungsgerät(e), GRKDT der Einheit (über
  Fahrzeug/Einheit), EL, Führer des Sicherheitstrupps.
- FCM mit eigenem Android-Kanal `atemschutz_alarm` und `alarm=1, asuew=1` für den
  nativen Vollbild-Alarm. SMS als Fallback für Notfall, Funkverlust und Heartbeat-Ausfall.
- Notfall-Ablauf (K8): Button „Atemschutznotfall" mit Bestätigung. Er setzt den Status,
  alarmiert sofort, schlägt den Sicherheitstrupp „einsetzen" vor und protokolliert. Der
  Ablauf „kein Funkkontakt" hat einen Assistenten nach Falter: GRKDT verständigt? Entscheidung
  Rettungstrupp? Rückzug des Trupps erwartet um …
- Eskalation auch ohne gesetzten Einsatzleiter: Fallback auf Rollen im Einsatz bzw. Org.

### Phase 3: App-Bindung und Überwachungssitzung (B + A)

- B: `POST /api/asuew/sitzung`, `POST /api/asuew/heartbeat`,
  `GET /api/asuew/{incident}/zustand` (kompakter JSON-Zustand mit Server-Zeit und Version),
  Dependency `require_asuew_session` an allen Schreibrouten, Mindestversion,
  Browser-Hinweisseite, Capability `asuew` am FCM-Token, Übergabe-Ablauf.
- B: Heartbeat-Überwachung im Watchdog → Matrixzeile „Gerät ohne Heartbeat".
- A: Bridge-Methoden `ELNative.asuew.start/stop/sync/selbsttest`, Header-Injektion für HTMX.
- Tests: Cross-Org-Test (`test_public_tenant_isolation.py`, falls Token-basiert), Browser
  ohne Sitzung → 403/Hinweisseite, abgelaufener Heartbeat → 409.

### Phase 4: Nativer Wächter in der Android-App (A)

- `AtemschutzWatchService` (Foreground-Service, eigener Kanal, Dauerbenachrichtigung mit
  `Chronometer` je Trupp, Tippen öffnet das Board).
- Lokale Fristen: `AlarmManager.setAlarmClock()` für 1/3, 2/3, Max und Snooze-Ende.
  Diese Wecker überleben Doze und WebView-Tod und werden nach Neustart (`BOOT_COMPLETED`) und
  App-Update aus dem persistierten Zustand neu gesetzt.
- Alarmwiedergabe über das Muster von `EinsatzAlarmService` (USAGE_ALARM, Audiofokus,
  Lautstärke-Anhebung, Vibration, Vollbild-Intent). Eigene Töne für Kontrolle, Rückzug,
  Max und Notfall.
- Zustand aus `GET …/zustand` alle 10–15 s, zusätzlich Push-to-sync via FCM `silent=1`.
  Der Server ist maßgeblich, lokale Fristen werden bei jeder Synchronisierung neu
  berechnet.
- Netz- und Serverüberwachung: nach mehr als 20 s ohne Antwort Banner + Vibration und
  Heartbeat-Retry mit Backoff.
- Wachhalten: `FLAG_KEEP_SCREEN_ON` nativ, solange das Board offen ist. Der Energiesparmodus
  der Bridge darf ASÜW nicht aufheben (T7).
- Akku-Überwachung (`ACTION_BATTERY_CHANGED`) mit Warnung bei < 20 %.
- Android-Unit-Tests für die reine Fristen- und Warnlogik (Muster `*Logic.kt` + Test).

### Phase 5: Offline-Fähigkeit (B + A)

- Jede Eingabe bekommt `client_event_id` (UUID) und `erfasst_am` (Gerätezeit + Offset). Der
  Server ist idempotent und übernimmt verspätete Ereignisse mit Kennzeichen „nachgetragen".
- Outbox in der WebView nach dem Muster `einheit_outbox.js` (bereits mit `npm run test:js`
  getestet) bzw. nativ gespiegelt, damit nichts verloren geht, wenn die WebView neu lädt.
- Konfliktregeln: Druck kann nur sinken (Ausnahme: Korrektur mit Begründung). Bei
  Statusübergängen gilt „erster gewinnt", der zweite bekommt 409 und eine Anzeige.
- Offline-Anzeige: Der letzte Zustand bleibt sichtbar, Fristen laufen lokal weiter, die
  Warnungen kommen vom nativen Dienst.

### Phase 6: Board-Redesign und Usability (B)

- Einsatzmodus-Layout: Alarmleiste oben, Trupp-Kacheln nach Dringlichkeit sortiert, große
  Touchziele, eine primäre Aktion pro Zustand („Einsetzen" → „Kontrolle/Druck" → „Rückzug"
  → „Zurück" → Erholzeit läuft).
- Druckeingabe als große Zifferntasten bzw. Raster mit Personenauswahl „niedrigster Druck".
- Druckbalken pro Person mit Rückzugsmarke statt Gauge (U3), Timeline pro Trupp (U6).
- Gezielter Swap pro Karte statt Grid-Reload (T5). Server-Zeitoffset für alle Timer (T6).
- Personen-Flyout aus #666 im Dialog „Trupp anlegen", Vorbelegungen, Fehler inline statt
  `alert()`.
- Hochkontrast/Outdoor-Modus, Querformat mit 2–3 Spalten (Formular-Tafel), Handy einspaltig.
- Mobile-Check ≤ 760 px, keine Smart Quotes, Icons nur über das Icon-System.

### Phase 7: ASSP, EL-Übersicht, Protokoll (B)

- Kompaktkachel im Einsatz-Dashboard für die EL: Trupps im Einsatz, nächste Frist,
  niedrigster Druck, offene Warnungen, Sicherheitstrupp-Status, Hinweis „ASSP ab 3 Trupps".
- ASSP-Ansicht (nur Logistik): Registrierung der eintreffenden Trupps, Bereitschaft,
  verfügbare und eingesetzte Trupps, Flaschenpool. Ohne Überwachungsfunktion, wie es die
  Ausbildung festlegt.
- PDF im Layout des Vorarlberger Formulars (3 Trupps pro Seite, Kontrollen mit Uhrzeit,
  Überwacher, Einsatzort, Datum) plus Detailseite mit allen Druckmeldungen und Audits.
- Übernahme in Archiv und Einsatzbericht.

### Phase 8: Abnahme und Gerätetests (A + B)

Gerätetest-Checkliste (Übungseinsatz, mindestens 2 Geräte):

- Display aus, App im Hintergrund, App per Taskmanager beendet, Gerät neu gestartet
- Flugmodus 5 min während laufender Frist, WLAN↔Mobil-Wechsel, Server-Neustart und
  Leader-Wechsel
- Lautlos und Bitte-nicht-stören aktiv, Medienlautstärke 0
- Akku 15 %, Energiesparmodus des Systems, Akku-Optimierung nicht ausgenommen
  (Selbsttest muss rot zeigen)
- Zwei Überwachungsgeräte, Übergabe, gleichzeitige Eingabe auf beiden
- Notfall- und Funkverlust-Ablauf mit Benachrichtigung von EL, GRKDT und Sicherheitstrupp
- Vergleich der Rückzugsberechnung mit den Lehrbehelf-Beispielen am Gerät

## 7. Priorisierung

| Reihenfolge | Was | Warum |
|---|---|---|
| 1 | Phase 0 | Behebt aktive Gefahren (K1, K2) und Datenfehler mit wenig Aufwand |
| 2 | Phase 2 + Phase 1 (Kontrolle/Notfall) | Erst dann werden Warnungen zugestellt, auch ohne offene Seite |
| 3 | Phase 3 + 4 | Ausfallsicherheit durch nativen Wächter und App-Bindung |
| 4 | Phase 5 | Offline-Robustheit |
| 5 | Phase 6 + 7 | Bedienkomfort, ASSP, Protokoll |
| 6 | Phase 8 | Abnahme vor Freigabe im Echteinsatz |
| 7 | Phase 9 | iOS-Vorbereitung, nach Android-Abnahme und Apple-Account |

Bis Phase 4 abgenommen ist, sollte das Wiki deutlich festhalten: **Die digitale ASÜW ergänzt
die Überwachungstafel bzw. das Formular und ersetzt sie nicht.**

## 8. Entscheidungen (2026-11-05)

| # | Frage | Entscheidung | Auswirkung auf den Plan |
|---|---|---|---|
| 1 | Browser | **Nur-Lese-Ansicht** mit deutlichem Hinweis „Atemschutzüberwachung nur in der Einsatzcockpit-App" | Browser zeigt Trupps, Drücke, Fristen und Timeline ohne Eingabe, Quittierung und Alarmton. Alle Schreibrouten verlangen die App-Sitzung (4.2). Der Hinweis ist ein fester Kopfbanner mit Deep-Link/QR zur App. |
| 2 | Kontrollzeiten | **Relativ** zur zu erwartenden Einsatzzeit | Org-Einstellung (Vorgabe 30 min) → Kontrollen bei 1/3 und 2/3 (10/20 min). Flasche liefert nur Luftvorrat/Plausibilität (F1). |
| 3 | Vorläufige Rückzugsgrenze | **Warneinrichtung bei 60 bar** | Vorgabe 60 bar (org-einstellbar, 50–60 bar laut Lehrbehelf). Nach „Einsatzziel erreicht" gilt `max(60, 2 × Anmarschverbrauch)` (F2/K2). |
| 4 | Eskalation | **Kritischer Hinweis auf dem Board** (nicht wegklickbar, bis erledigt) **plus SMS** | SMS-Eskalation für Rückzugsdruck ohne Bestätigung, Max-Zeit, Funkverlust, Notfall und Heartbeat-Ausfall. Empfänger gemäß Matrix (EL, GRKDT der Einheit, Führer Sicherheitstrupp), Fallback auf Rollen/Org-Einstellung. Konkrete Empfängerliste je Org ist in Phase 1 konfigurierbar. |
| 5 | iOS | **Beides vorbereiten** | Fachlogik, Sitzungs-/Heartbeat-API und Fristenberechnung bleiben plattformneutral (siehe Phase 9). Android wird zuerst abgenommen. |
| 6 | Rolle `recorder` | **Darf weiter Trupps einsetzen** | Rollen bleiben `breathing_supervisor`, `incident_leader`, `admin`, `recorder`. Der Readiness-Override (Begründung) bleibt auf die ersten drei beschränkt. |

### Phase 9: iOS-Vorbereitung (B + iOS-Repo)

Die iOS-App ist noch in Vorbereitung (Mac-Test und Apple-Account offen). Für ASÜW ohne Umbau
nachrüstbar machen:

- Server-API (`/api/asuew/*`), Capability `asuew` und Mindestversion bleiben
  plattformneutral (`X-ECP-Platform` unterscheidet).
- Fristenlogik als reine, getestete Funktionen ohne Android-Bezug (Kotlin-Logik und
  JS-Spiegel mit identischen Testfällen).
- iOS-Ersatz für den nativen Wächter: lokale Benachrichtigungen mit
  `UNNotificationInterruptionLevel.timeSensitive`, für Rückzug/Notfall
  **Critical Alerts** (Apple-Berechtigung muss beantragt werden), Background-Modes und
  Live Activity für die Trupp-Uhren. Ein iOS-Gerät kann Fristen weniger zuverlässig im
  Hintergrund halten als Android; bis zur Abnahme ist die Überwachung auf iOS nur freigegeben,
  wenn die Selbsttest-Ampel grün ist und zusätzlich ein Android-Gerät mitüberwacht.
- Bis dahin zeigt iOS die Nur-Lese-Ansicht mit App-Hinweis.
