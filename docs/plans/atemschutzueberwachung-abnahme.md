# Atemschutzüberwachung: Abnahme und Gerätetests (Phase 8)

Stand: 2026-11-06 · Gehört zu `atemschutzueberwachung-app-plan.md`.

Die Phasen 0 bis 7 sind umgesetzt und durch automatische Tests, den CI-Lauf und (für die Oberfläche)
den Browser-Prüfstand abgesichert. **Nicht abgesichert ist alles, was nur ein echtes Android-Gerät zeigt**:
Hintergrunddienst, Alarmton, exakte Wecker, Akku-Verhalten, Einstellungsdialoge. Diese Checkliste ist die
Freigabebedingung, bevor die digitale Überwachung im Echteinsatz verwendet wird. Bis dahin gilt:
**Die digitale Überwachung ergänzt Tafel und Formular, sie ersetzt sie nicht.**

## 1. Rollout in dieser Reihenfolge

1. Backend deployen (Migrationen 0347 bis 0351). Org-Schalter **„Überwachung nur in der App erlauben“**
   bleibt **aus** (Standard).
2. App-Release aus Einsatzcockpit-Android (PR #96, #97, #98) bauen und auf die Überwachungsgeräte bringen.
   Das Release verteilt eine APK an die Geräte und wird deshalb bewusst nicht automatisch ausgelöst.
3. Gerätetests (Abschnitt 2) im Übungseinsatz durchführen und abhaken.
4. Erst danach in der Org-Einstellung **„Überwachung nur in der App erlauben“** einschalten und
   optional `ASUEW_MIN_APP_VERSION` auf den getesteten App-Stand setzen.
5. Eskalationsempfänger je Organisation prüfen (Einstellungen, Abschnitt Atemschutzüberwachung):
   ohne Auswahl werden Einsatzleiter und Atemschutz-Überwacher der Organisation benachrichtigt.

## 2. Gerätetest-Checkliste (Übungseinsatz, mindestens 2 Geräte)

Jeder Punkt: Ergebnis (ok / Abweichung), Gerät, Android-Version, Datum, Prüfer.

### Hintergrund und Zuverlässigkeit
- [ ] Display aus, App im Hintergrund: Kontrolle 1/3 wird zur richtigen Zeit gemeldet (Ton hörbar, Vollbild)
- [ ] App per Taskmanager beendet: Dienst und Dauerbenachrichtigung kommen wieder (Neustart-fest)
- [ ] Gerät neu gestartet bei laufendem Trupp: Überwachung nimmt automatisch wieder auf
- [ ] App-Update bei laufendem Trupp: Überwachung nimmt automatisch wieder auf
- [ ] Energiesparmodus des Systems an: Fristen und Alarme kommen trotzdem
- [ ] Akku-Optimierung für die App nicht ausgenommen: Geräte-Check zeigt Gelb und führt zur Einstellung
- [ ] Akku 15 % ohne Laden: Warnung „Akku niedrig“; unter 10 %: Alarm

### Alarm und Ton
- [ ] Lautlos / Bitte-nicht-stören aktiv: Alarm klingt trotzdem (Alarm-Lautstärkekanal)
- [ ] Medienlautstärke 0, Alarmlautstärke hoch: Alarm hörbar; Alarmlautstärke 0: Geräte-Check zeigt Rot
- [ ] „Alarmton testen“ und „Ton gehört?“ im Geräte-Check funktionieren, Ampel wird grün
- [ ] Wiederholung alle 60 s bis zur Quittierung, bei Notfall alle 30 s
- [ ] „Alarm quittieren“ in der Benachrichtigung stoppt die Wiederholung
- [ ] Rückzugsdruck-Alarm erscheint bei Druckmeldung auf oder unter der Rückzugsgrenze

### Netz, Server, Offline
- [ ] Flugmodus 5 Minuten bei laufender Frist: Frist wird lokal alarmiert; nach Rückkehr des Netzes keine Dopplung
- [ ] Offline erfasste Kontrolle: Alarm verstummt, Eingabe erscheint als „wartend“ und wird später übertragen
- [ ] WLAN-Mobilfunk-Wechsel und Server-Neustart: Verbindungsbanner, Wiederverbindung, Board lädt nach
- [ ] Verbindungsverlust > 20 s: Vibration; > 60 s: Alarm „Verbindung zum Server verloren“
- [ ] Offline abgelehnte Eingabe (z. B. Statuskonflikt): wird mit Klartext angezeigt und lässt sich verwerfen

### Eskalation und Benachrichtigung (zusätzlich zu den Geräten der Überwacher)
- [ ] Unquittierte Kontrolle: nach 60 s Push an den Überwacher, nach 180 s an die Leitung, nach 300 s SMS
- [ ] Rückzugsdruck ohne Bestätigung: nach 60 s Push und SMS an die Leitung
- [ ] Atemschutznotfall: sofort Push-Alarm und SMS an Überwacher und Leitung
- [ ] Kein Funkkontakt: sofort Push-Alarm und SMS an die Leitung, Checkliste im Board sichtbar
- [ ] Überwachungsgerät ohne Heartbeat (App beenden, Schalter „nur in der App“ an): nach 60 s Warnung „Kein Überwachungsgerät“
- [ ] Übung: keine SMS, Push mit Übungs-Kennzeichen
- [ ] SMS ohne hinterlegte Telefonnummer wird im Zustellungsprotokoll als unterdrückt gezeigt

### Bedienung und Anzeige
- [ ] Handschuhe: Tippflächen Druck, Rückzug, Kontrolle, Notfall zuverlässig bedienbar
- [ ] Sonnenlicht: Hochkontrast-Modus lesbar; Druckbalken und Rückzugsmarke erkennbar
- [ ] Handy hoch, Tablet hoch und quer: kein horizontales Scrollen, Alarmleiste klebt oben
- [ ] Trupp anlegen mit Personen-Flyout, Schnellwahl für den Druck, Fehler direkt am Feld
- [ ] Nur-Lese-Ansicht im Browser: kein Alarmton, Hinweis auf die App, Notfall melden bleibt möglich
- [ ] Zwei Überwachungsgeräte, Übernahme der Überwachung: Zuständigkeit wechselt, Audit-Eintrag vorhanden

### Fachlich (Vergleich mit Lehrbehelf und Formular)
- [ ] Rückzugsberechnung gegen die Beispiele: 300 bar / 30 bar Anmarsch → 60 bar; 270 bar / 90 bar Anmarsch → 180 bar
- [ ] Kontrollzeiten bei 30 min Einsatzzeit: 10 min und 20 min; bei geänderter Org-Einstellung entsprechend
- [ ] Erholzeit 30 min: Warnung beim Wiedereinsatz, Begründung wird protokolliert
- [ ] Ab 3 eingesetzten Trupps: Hinweis „ASSP alarmieren“; nach „ASSP eingerichtet“ endet er
- [ ] Ausgedrucktes Überwachungsformular (befüllt und leer) ist lesbar, Layout passt auf A4
- [ ] Archiv und Einsatzbericht zeigen Kontrollen, Notfall, Funkverlust und ASSP korrekt

## 3. Bekannte Grenzen (vor der Freigabe zur Kenntnis nehmen)

- Das Backend wurde nur unter SQLite lokal und in der CI unter MariaDB geprüft, die Android-App nur durch
  Kompilierung und Unit-Tests der CI. Alle Android-Funktionen sind bisher **nicht am Gerät erprobt**.
- iOS: Es gibt keinen nativen Wächter. Auf iOS bleibt es bei der Nur-Lese-Ansicht, solange der Schalter
  „nur in der App“ an ist. Die Vorbereitung (Phase 9 im Plan: Critical Alerts, Live Activity) ist offen.
- `SCHEDULE_EXACT_ALARM` ist ab Android 14 für Nicht-Wecker-Apps nicht automatisch erteilt; der
  Geräte-Check führt zur Einstellung, ohne sie fallen Wecker auf eine ungenauere Planung zurück.
- Eine Person, die in zwei aktiven Trupps steht, wird beim Einsetzen gewarnt, aber nicht gesperrt.
- Der Einsatz beginnt offline nicht: „Einsetzen“, Trupp anlegen, erneut einsetzen und Notfall aufheben
  verlangen Verbindung (Zeitbasis und Bereitschaftsprüfung gelten sonst nicht).
- Einsätze mit mehreren beteiligten Organisationen: Die App-Sitzung ist an die primäre Organisation des
  Einsatzes gebunden.
- Der volle lokale Testlauf zeigt an der Zeitzonengrenze (00:00 bis 01:00 MEZ) vier bestehende
  Fehlschläge in `tests/test_update_version.py` (UTC- gegen Ortsdatum); in der CI treten sie nicht auf.

## 4. Offene Entscheidungen für die Freigabe

1. Zeitpunkt des App-Releases und Verteilung auf die Überwachungsgeräte.
2. Zeitpunkt, ab dem „Überwachung nur in der App erlauben“ eingeschaltet wird.
3. Pilotorganisation und Pilot-Einsatzarten (zunächst Übungen).
4. Ob und wann die iOS-Vorbereitung (Phase 9) umgesetzt wird.
