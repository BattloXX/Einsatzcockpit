# Benutzer und Mitglieder über MCP abgleichen

Die Werkzeuge verwenden die bestehenden Organisationsgrenzen und Live-Rollenprüfungen.
Benutzer lesen, abgleichen, verknüpfen und trennen erfordert `admin`; Mitglieder lesen
erfordert `admin` oder `fahrtenbuch_admin`. Es gibt keine neuen Rollen. Technische
Gerätekonten sind von der Benutzerverwaltung ausgeschlossen.

## Lesen und vergleichen

- `benutzer_liste(limit=50, seite=1)` und `mitglieder_liste(limit=50, seite=1)` liefern
  auch inaktive Datensätze. Maximal 200 Einträge je Seite; bis `truncated=false` weiterblättern.
- `benutzer_lesen(benutzer_id)` und `mitglied_lesen(mitglied_id)` liefern die vorhandenen Details.
- Listen und Details enthalten `id`, `vorname`, `nachname`, `email`, `telefon`,
  `organisations_id`, `aktiv` sowie `mitglied_id` beziehungsweise `benutzer_id`.
  Das Benutzermodell speichert lediglich `full_name`: Vor-/Nachname werden am ersten
  Leerzeichen aufgeteilt; ohne teilbaren vollständigen Namen sind beide `null`.
  `full_name` bleibt zusätzlich verfügbar, damit zusammengesetzte Namen geprüft werden können.
- `benutzer_mitglied_abgleichen()` vergleicht die gesamte Organisation, unabhängig von
  der Listenpaginierung. Der Aufruf ändert keine Daten und liefert die Kategorien
  `sicher`, `pruefen`, `kein_treffer` und zusätzlich `bestehend`.

Beispiel eines sicheren Ergebnisses:

```json
{
  "benutzer_id": 123,
  "mitglied_id": 456,
  "automatisch_verknuepfbar": true,
  "kandidaten": [
    {"mitglied_id": 456, "merkmale": ["name", "email"], "gruende": []}
  ]
}
```

Ein sicherer Treffer erfordert mindestens zwei Merkmale aus vollständigem Namen,
E-Mail und Telefonnummer. Groß-/Kleinschreibung, Leerzeichen, Umlaute (`ü`/`ue`),
Akzente und `ß`/`ss` werden normalisiert. Beim Namen wird `full_name`, ersatzweise
`display_name`, verglichen. Ähnliche Schreibweisen liefern ausschließlich zusätzliche
Prüfkandidaten und zählen nicht als exakte Namensübereinstimmung. Telefonformatierungen
und `00`/`+` werden vereinheitlicht; Landesvorwahlen werden nicht geraten, kurze Nummern
unter sieben Ziffern nicht verglichen. Leere Werte zählen nie als Merkmal.

Konkurrierende Kandidaten auf beiden Seiten, bereits belegte Mitglieder, geschützte
Systemadministratoren und inaktive Datensätze verhindern die automatische Zuordnung.
Die Prüfung ist bewusst konservativ: Auch ein weiterer Kandidat mit nur einem
übereinstimmenden Merkmal kann eine manuelle Prüfung erforderlich machen.

## Schreiben

```json
{"benutzer_id": 123, "mitglied_id": 456, "dry_run": true}
```

`benutzer_mitglied_verknuepfen` prüft den sicheren Treffer beim Schreiben erneut.
Für einen durch einen Menschen geprüften unsicheren Fall kann ausdrücklich
`manuell_bestaetigt=true` angegeben werden. Ein KI-Client darf diese Bestätigung
nicht selbst aus einem unsicheren Abgleich ableiten.

`dry_run=true` prüft dieselben Voraussetzungen, schreibt aber weder Link noch
Änderungsaudit. `wuerde_aendern` zeigt die simulierte Änderung; `geaendert` bleibt
`false`. Ohne `dry_run` werden Link und Audit gemeinsam gespeichert. Der Audit-Eintrag
enthält Zeitpunkt, ausführenden Benutzer, OAuth-Client-ID, alten/neuen Mitgliederbezug
und die Angabe zur manuellen Bestätigung.

Bestehende Verknüpfungen können nicht ersetzt werden, auch nicht mit manueller
Bestätigung. Eine erneute identische Zuordnung liefert `status=uebersprungen` und
erzeugt keine weitere Änderung und keinen weiteren Änderungsaudit. Der eindeutige
Datenbankindex auf `user.member_id` und ein bedingtes Update schützen zusätzlich
gegen konkurrierende Schreibzugriffe.

`benutzer_mitglied_trennen(benutzer_id, dry_run=false)` löst einen Bezug explizit und
idempotent. **Änderung zum bisherigen Werkzeug:** `mitglied_id=null` oder ein fehlender
Mitgliederparameter bei `benutzer_mitglied_verknuepfen` ist nicht mehr zum Trennen zulässig.
Clients müssen das eigene Trennwerkzeug verwenden.

`benutzer_mitglieder_bulk_verknuepfen` nimmt 1–200 Zuordnungen entgegen:

```json
{
  "zuordnungen": [
    {"benutzer_id": 123, "mitglied_id": 456},
    {"benutzer_id": 124, "mitglied_id": 457}
  ],
  "dry_run": false
}
```

Bulk erlaubt ausschließlich sichere Treffer und unveränderte bestehende Zuordnungen.
Unsichere Fälle werden übersprungen. Ein initialer Abgleich verhindert, dass die
Verarbeitungsreihenfolge Mehrdeutigkeiten scheinbar auflöst; jede Änderung wird erneut
validiert und einzeln abgeschlossen. Fehler einer Zuordnung rollen nur deren
Transaktion zurück. Die Antwort enthält `ergebnisse` je Zuordnung und eine
`zusammenfassung` mit `erfolgreich`, `uebersprungen`, `fehlgeschlagen` und `simuliert`.

## KI-Workflow

1. Alle Benutzer und Mitglieder lesen und bestehende Zuordnungen berücksichtigen.
2. `benutzer_mitglied_abgleichen` aufrufen.
3. Einträge aus `sicher` mit `dry_run=true` simulieren und anschließend über Bulk schreiben.
4. `pruefen` und `kein_treffer` unverändert zur menschlichen Bearbeitung ausgeben.
5. Erfolgreiche, übersprungene und fehlgeschlagene Zuordnungen berichten.

Die Verknüpfungswerkzeuge ändern ausschließlich den Mitgliederbezug. Benutzerrollen,
Kontostatus und Stammdaten bleiben unverändert; es werden keine Benutzer oder Mitglieder
erstellt oder gelöscht. Der Mitgliederbezug wirkt sich wie in der bestehenden
Benutzerverwaltung auf die mitgliedsbezogene Einheitenansicht aus.
