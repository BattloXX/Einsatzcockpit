# Planspiele einrichten (Administration)

← [Zurück zur Startseite](Home) · Anwender-Doku: [Planspiele & Simulation](Anwender-Planspiele) · Technik: [Planspiel-Architektur](Entwickler-Planspiele)

## Aktivierung

Das Modul wird in zwei Stufen geschaltet und ist standardmäßig aus:

1. **System:** Der Systemadministrator aktiviert unter `/admin/settings` den Schalter **Planspiele**.
2. **Organisation:** Der Org-Admin schaltet das Modul in den Organisations-Einstellungen ein. Beim ersten Einschalten wird der **Szenario-Katalog** automatisch importiert (56 Vorlagen + F30 Proberuf).

Erst wenn beide Schalter aktiv sind, erscheint **Planspiele** im Menü *Module*. Wird der Systemschalter abgeschaltet, stoppt der Hintergrund-Worker sofort. Laufende Planspiele behalten ihren Status und lassen sich nach dem Wiedereinschalten fortsetzen.

## Rollen

| Rolle | Zweck |
|-------|-------|
| `uebungsleiter` | Planspiele anlegen, starten, steuern, auswerten |
| `uebungsbeobachter` | lesen und Beobachtungen erfassen (nur als Mitglied eines Planspiels) |
| `org_admin` / `admin` | zusätzlich Katalog, Gebiet, Adresspool und Grenzen |

Die Rollen werden wie gewohnt unter [Benutzer und Rollen](Administration-Benutzer-und-Rollen) vergeben. Für den **Allein-Betrieb** (Board bedienen, Einheiten simulieren) braucht der Übungsleiter **zusätzlich** `incident_leader` oder `admin`. Das Planspiel-Modul vergibt bewusst keine weitergehenden Einsatzrechte.

## Einstellungen & Adresspool

`/admin/settings/planspiele` (auch über **Einstellungen & Adresspool** in der Planspiel-Übersicht):

### Gebiet
Das Gebiet ist ein Polygon, das auf der Karte gezeichnet und als GeoJSON gespeichert wird. Ohne Polygon gilt ein **Radius** um den Standort der Organisation (`fallback_lat/lng`, Standard 5 km). Zufallsorte kommen nur aus diesem Gebiet.

### Grenzen
| Einstellung | Standard | Wirkung |
|-------------|----------|---------|
| Max. gleichzeitig laufende Planspiele | 2 | Weitere Starts werden abgelehnt |
| Max. neue Einsatzstellen je Minute | 6 | bremst den Unwetter-Generator, auch bei hohem Tempo |

### Adresspool
Planspiele verwenden **nur reale Adressen**, es werden keine Hausnummern erfunden. Ist der Pool leer, kann man nicht starten.

| Aktion | Quelle | Qualität |
|--------|--------|----------|
| **Pool aus Objekten/Einsätzen aktualisieren** | freigegebene Objekte inkl. Zusatzadressen (A), geokodierte Adressen früherer Realeinsätze (B) | A/B |
| **OSM-Adressen importieren** | Hausnummern, Straßen, Gewässer und Gleise aus OpenStreetMap (Overpass) im Gebiet | B/C |
| **Manueller Punkt** | eigener Kartenpunkt mit Ortstypen (z. B. Gewässer, BMA) | C |

Qualitätsstufen: **A** = verifiziertes Objekt, **B** = Adresse mit Hausnummer, **C** = Straßen- oder Kartenpunkt. Szenarien können eine Mindestqualität verlangen, und bei der Zufallsauswahl werden bessere Adressen bevorzugt. Einzelne Einträge lassen sich deaktivieren.

Der OSM-Import ist **opt-in**, wird manuell ausgelöst und belastet den öffentlichen Overpass-Dienst. Daher nur bei Bedarf und nicht wiederholt hintereinander ausführen. Er läuft im Hintergrund und dauert je nach Gebiet und Auslastung von Overpass etwa eine bis drei Minuten (5 km Radius ≈ 18 000 Einträge). Die Seite zeigt so lange „OSM-Import läuft …“ und darf verlassen werden. Ist Overpass ausgelastet, wird zweimal automatisch neu versucht; scheitert auch das, erscheint ein Hinweis, es später erneut zu probieren.

### Ortstyp-Zuordnung
Hier wird festgelegt, welche Objekt-Kategorie welchem Ortstyp entspricht (z. B. *Wohnhaus* → `wohngebaeude`, *Tiefgarage* → `tiefgarage`). Szenarien wählen ihre Orte über Ortstypen. Ohne passenden Typ werden manche Vorlagen nie gezogen: B18 und T03 brauchen eine Tiefgarage, T20 einen Gleisbereich, B20 und B21 ein Objekt mit Brandmeldeanlage.

## Szenario-Katalog

`/admin/settings/planspiele/szenarien` listet alle Vorlagen mit Filter nach Kategorie, Stichwort und Status.

- **Detail** mit den Tabs Grunddaten, Alarmierung, Standortregeln, Folgemeldungen, Lernziele, Varianten & Zufall, Vorschau und Versionen.
- **Versionen:** Jede Änderung erzeugt eine neue Version. Laufende Planspiele behalten den Stand, mit dem sie gestartet wurden (Snapshot).
- **Systemupdates:** Unveränderte Systemvorlagen werden bei neuen Programmversionen automatisch aktualisiert. **Geänderte Vorlagen werden nie überschrieben**; dort lässt sich der Systemstand mit **Auf Standard zurücksetzen** übernehmen.
- **Duplizieren**, **Deaktivieren** (nicht mehr im Zufall bzw. in der Auswahl), **Entfernen** (bereits verwendete Vorlagen werden nur archiviert).
- **Export/Import** als JSON. Beim Import werden nur erlaubte Felder übernommen. Textvorlagen laufen in einer abgesicherten Sandbox, es wird kein Code ausgeführt.
- **Stichworte** (`/admin/settings/planspiele/stichworte`): Referenzansicht der LFV-Stichworte (V03, 02/2021) mit Kurz- und Langtext. Die Szenarien verweisen auf die Alarmtypen der Organisation. Fehlt ein Code dort, wird beim Start auf F1/T1 ausgewichen und gewarnt.

## Sicherheit

- **Keine Außenwirkung:** Für Objekte mit Planspiel-Kennung gilt eine eigene, strengere Sperre im zentralen Übungs-Guard. Die Org-Freigaben für normale Übungen (Übungs-SMS, Teams-Übung, Übungsdruck …) werden **ignoriert**. SMS, Push, Teams, Mail, Druck, Objektkontakte, Nachbaralarmierung, LIS/DIBOS, WordPress und Geocoding sind gesperrt. Die Freigabe einzelner Kanäle für Planspiele ist vorbereitet, aber noch nicht aktiv.
- **Straßensperren** aus Szenarien sind nur übergreifende Meldungen in der Übungs-GSL und nie echte Straßensperren.
- **Mandantentrennung:** Alle Planspiel-Daten gehören zu einer Organisation. Planspiele sind nur für Leitung und Mitglieder sichtbar.
- **Ende:** Beim Beenden werden QR-/PIN-Zugänge der Übungslage widerrufen und der öffentliche Lage-Link gelöscht.

## Betrieb

| Variable | Standard | Zweck |
|----------|----------|-------|
| `PLANSPIEL_LOOP_ENABLED` | `true` | Hintergrund-Worker für Folgemeldungen und Generator |
| `PLANSPIEL_LOOP_INTERVAL_S` | `2` | Takt des Workers in Sekunden |

Der Worker läuft im Leader-Prozess. Doppelte Ausführungen verhindern Datenbank-Sperren (Lease je Planspiel, Claim je Ereignis), deshalb ist auch ein Neustart während einer Übung unkritisch. Ereignisse, die während eines längeren Ausfalls fällig geworden wären, werden nicht nachgeholt. Sie stehen in der Zeitleiste der Regie als *übersprungen (verpasst)*. Ausgelassene Einsatzstellen des Generators vermerkt das Einsatzjournal der Übungs-GSL („… Meldungen wegen Unterbrechung ausgelassen“).

Migrationen: `0284` (Basis), `0285` (Katalog), `0286` (Adresspool).

## Fehlersuche

| Symptom | Ursache / Lösung |
|---------|------------------|
| Start: „Kein Ort … im Adresspool“ | Pool aufbauen oder OSM-Import; Gebiet prüfen |
| Szenario wird nie gezogen | Ortstyp fehlt im Pool (z. B. Tiefgarage, Gleis, BMA-Objekt) oder Vorlage deaktiviert |
| Übungsleiter sieht kein Board | Rolle `incident_leader`/`admin` fehlt (siehe Rollen) |
| Folgemeldungen kommen nicht | Planspiel pausiert, Folgemeldungen ausgeschaltet oder Worker deaktiviert; Logger `einsatzleiter.planspiel_loop` und `app.services.planspiel_engine` prüfen |
