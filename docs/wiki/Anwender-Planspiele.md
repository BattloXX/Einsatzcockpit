# Planspiele & Simulation

← [Zurück zur Startseite](Home) · Administration: [Planspiele einrichten](Administration-Planspiele) · Technik: [Planspiel-Architektur](Entwickler-Planspiele)

Mit dem Planspiel-Modul erzeugt und steuert eine Übungsleitung realistische Übungseinsätze und Übungs-Großschadenslagen. Die Teilnehmer arbeiten dabei in den gewohnten Ansichten: Einsatzboard, GSL-Board, Einheitenansicht und Journale. Die Regie spielt nach Drehbuch oder per Zufall Folgemeldungen ein, z. B. „Person am Balkon sichtbar“ oder „Zufahrt blockiert“. Beim Unwetter-Szenario laufen laufend neue Einsatzstellen ein.

> **Es wird nie echt alarmiert.** Planspiel-Einsätze und -Lagen lösen keine SMS, kein Push, keine Teams- oder Mail-Meldung, keinen Automatikdruck, keine Nachbaralarmierung und kein Geocoding aus. Das gilt auch dann, wenn deine Organisation SMS oder Teams für normale Übungen freigegeben hat. Alles ist als **ÜBUNG** gekennzeichnet und zählt in der Statistik nicht als Einsatz (siehe [Übungsmodus](Anwender-Uebungsmodus)).

## Wer darf was?

| Rolle | Rechte |
|-------|--------|
| **Übungsleiter** (`uebungsleiter`) | Planspiele anlegen, starten, steuern und auswerten |
| **Übungsbeobachter** (`uebungsbeobachter`) | Planspiele lesen und Beobachtungen erfassen, sofern er als Mitglied eingetragen ist |
| Admin / Org-Admin | alles, inkl. Szenario-Katalog und Einstellungen |

Je Planspiel lassen sich zusätzlich **Mitglieder** eintragen (Leiter, Regie, Beobachter). Planspiele, in denen man nicht Mitglied ist, sieht man nicht.

Für das **Board und die Einheiten-Simulation** gelten weiterhin die normalen Einsatzrechte. Wer allein üben will, braucht neben `uebungsleiter` auch die Rolle **Einsatzleiter** (`incident_leader`) oder `admin`. Fehlt diese, weist das Regie-Cockpit darauf hin.

## Übersicht

Unter **Module → Planspiele** (`/planspiele`) stehen die Planspiele in den Gruppen *Entwürfe*, *Laufend* und *Beendet*. Bei beendeten Planspielen führt **Auswertung** direkt zum Bericht.

## Neues Planspiel – der Assistent

**+ Neues Planspiel** öffnet einen Assistenten in sieben Schritten:

1. **Titel und Lernziele:** Die Lernziele erscheinen später in der Regie und in der Auswertung.
2. **Art:**
   - *Einzeleinsatz*: ein Übungseinsatz mit Folgemeldungen
   - *Großschadenslage (Unwetter)*: eine Übungs-GSL, in die laufend Einsatzstellen einlaufen; dafür ein GSL-Szenario G01–G05 wählen
3. **Erzeugung:** manuell, teilautomatisch oder zufällig
4. **Szenario:** Wähle aus dem Katalog (56 Vorlagen nach LFV-Stichworten F1–F21 und T1–T21, z. B. *B09 Zimmerbrand in Mehrfamilienhaus*) oder lass es zufällig ziehen. Ins Feld **Stichwortvorschlag** kannst du einen Alarmtext tippen, z. B. „Rauch aus Fenster“; dann schlägt das System passende Szenarien vor.
5. **Ort** (nur Einzeleinsatz):
   - *Zufällig* oder *Pool-Eintrag* aus dem Adresspool des Übungsgebiets
   - *Objekt*: ein freigegebenes Objekt der eigenen Wehr. Szenarien mit Brandmeldeanlage verlangen ein Objekt mit hinterlegter BMA.
   - *Manuelle Adresse* oder *Kartenpunkt*

   Bei einer GSL entstehen die Einsatzstellen verteilt im Übungsgebiet.
6. **Varianten:**
   - Stichwort und Alarmtext überschreiben
   - **Tempo** (0,5× bis 10×)
   - **Seed**: Gleicher Seed und gleiches Szenario ergeben dieselbe Abfolge, praktisch für Wiederholungen.
   - Folgemeldungen ein/aus
   - Bei einer GSL zusätzlich der Unwetter-Generator: Modus (Hochwasser, Sturm, Kombination), mittlerer Abstand zwischen neuen Stellen, maximale Stellenzahl und maximale Laufzeit.
7. **Simulation:** Sicherheitsvorschau, was angelegt wird und was **nicht** ausgelöst wird.

Der Assistent speichert einen **Entwurf**. Die Detailseite zeigt eine **Vorschau** mit Alarmtext, Ort und geplanten Folgemeldungen (Trockenlauf ohne Datensätze). Der Entwurf lässt sich noch **bearbeiten** oder **verwerfen**. Erst **Übung starten** legt den Übungseinsatz bzw. die Übungs-GSL an.

## Regie-Cockpit

Nach dem Start öffnet sich das Regie-Cockpit (`/planspiele/{id}`). Es aktualisiert sich live.

- **Uhr und Steuerung:** Übungszeit, Status, **Pause**/**Fortsetzen**, Folgemeldungen ein-/ausschalten, **Übung beenden**. Eine Pause friert die Übungsuhr ein, und es wird nichts mehr eingespielt.
- **Geheimlage:** Die Regie-Informationen zum Szenario (rot umrandet) sehen nur Regie und Leitung, nie die Teilnehmer.
- **Zeitleiste:** Geplante, ausgeführte und übersprungene Ereignisse. Je Ereignis gibt es **Jetzt**, **Überspringen** und **Wiederholen**. Manche Ereignisse warten auf einen Zustand: Sie kommen z. B. erst, wenn die Einsatzstelle in *Erkundung* ist oder ein Auftrag abgeschlossen wurde. Meldungen, die einen Erfolg behaupten („Person befreit“), feuern nur manuell oder nach einem tatsächlich abgeschlossenen Auftrag.
- **Freie Meldung einspielen:** eigener Text als Folgemeldung, sofort oder später.
- **Stichwort ändern** (Einzeleinsatz): z. B. eine Eskalation von F3 auf F4.
- **Beobachtungen:** Notizen mit Übungszeit und Lernziel für die Nachbesprechung.
- **Mitglieder:** Regie und Beobachter für genau dieses Planspiel eintragen.

Folgemeldungen landen dort, wo sie auch im Ernstfall ankämen:

| Art | Ziel |
|-----|------|
| Einzeleinsatz | als Meldung „[Übung] …“ im Einsatzboard und im Einsatzprotokoll |
| GSL-Einsatzstelle | im Funkjournal und in der Chronik der Einsatzstelle |
| GSL lageweit | im Funkjournal; Straßensperren erscheinen als **Übergreifende Meldung** „Straße gesperrt“ (nie als echte Straßensperre) |

## Großschadenslage: Unwetter-Generator

Bei einer GSL-Übung erzeugt der Generator laufend neue Einsatzstellen auf dem GSL-Board (Badge **Planspiel**).

- **Wellen** je Modus, z. B. Hochwasser: Anstieg → Spitze → Konzentration → Entspannung.
- **Schwere:** überwiegend T1, seltener T2, vereinzelt T3/T4
- **Herausforderungen:** gelegentlich mehrere Meldungen gleichzeitig und Doppelmeldungen zur selben Adresse mit anderer Schilderung
- **Generator-Panel:**
  - **Generator stoppen**: Es kommen keine neuen Stellen mehr, die vorhandenen bleiben bearbeitbar.
  - **Fortsetzen**
  - **Welle jetzt**: sofort mehrere Stellen erzeugen
- **Grenzen:** Ist die maximale Stellenzahl oder Laufzeit erreicht, stoppt der Generator von selbst.
- Neue Stellen kommen immer im Status *Eingegangen* an. Sie werden **nicht** automatisch disponiert; das ist Aufgabe der Übenden.

## Allein üben: simulierte Einheiten

Eine Person kann das ganze ECP allein durchspielen:

1. GSL-Planspiel starten (Rollen: Übungsleiter **und** Einsatzleiter).
2. Im Cockpit unter **Simulierte Einheiten** zusätzliche Einheiten anlegen, z. B. „Nachbarwehr LF“. Sie erscheinen als „SIM …“ in der Übungs-GSL, ohne SMS und ohne Druck.
3. Im **GSL-Board** Einsatzstellen übernehmen und Einheiten disponieren.
4. Im Cockpit bei einer Einheit **Als Einheit bedienen** wählen (öffnet einen neuen Tab). Die Einheitenansicht öffnet sich im Modus **SIMULATION**: Auftrag bestätigen, Anfahrt, vor Ort, abschließen, Meldungen schreiben. Die Phasen der Einsatzstelle laufen dabei wie gewohnt automatisch mit.
5. **Zurück zur Regie** führt wieder ins Cockpit.

Alle simulierten Handlungen werden mit dem Hinweis „(Simulation …)“ protokolliert.

## Beenden

**Übung beenden** stoppt das Planspiel endgültig:
- Noch geplante Ereignisse werden verworfen.
- QR-/PIN-Zugänge der Übungslage werden widerrufen, der öffentliche Lage-Link wird ungültig.
- Übungseinsatz und Übungs-GSL bleiben erhalten. Die GSL wird wie gewohnt im Board geschlossen.

## Auswertung

**Auswertung** (`/planspiele/{id}/auswertung`) erstellt einen neutralen Bericht **ohne Gut-/Schlecht-Wertung**:

- Szenario, Seed, Tempo, Dauer in Echtzeit und Übungszeit
- **GSL:**
  - Median und Mittelwert der Zeiten Eingang → Erkundung → Bewertet → Disponiert → In Arbeit → Fertig gemeldet
  - Verlauf je 10 Minuten
  - Doppelmeldungen (und wie viele davon erledigt wurden)
  - überfällige Lagemeldungen und Einheiten-Einsätze
- **Einzeleinsatz:** Meldungen und Aufgaben (erledigt/gesamt)
- Ereignis-Zeitleiste, Beobachtungen und Regie-Aktionen

Mit **PDF** lädst du den Bericht für die Nachbesprechung herunter. **Wiederholen** legt einen neuen Entwurf mit demselben Szenario-Stand und Seed an. So lässt sich dieselbe Übung mit einer anderen Mannschaft vergleichen.

## Tipps

- Für den Einstieg eignen sich *B01 Unklarer Brandgeruch* (Einzeleinsatz) oder *G01 Starkregen* mit Tempo 2× (GSL).
- Mit Tempo 5–10× lässt sich ein langes Unwetter in kurzer Zeit durchspielen. Das Board kann dann aber schnell voll werden.
- Ein leerer Adresspool verhindert den Start. Die Administration baut ihn auf (siehe [Planspiele einrichten](Administration-Planspiele)).
