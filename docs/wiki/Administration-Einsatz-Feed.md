# Einsatz-Feed (Pull-Schnittstelle)

← [Zurück zur Startseite](Home)

Der Einsatz-Feed ist eine **rein lesende** Schnittstelle, über die externe Systeme (z. B. eine Gebäudevisualisierung, ein Infoscreen oder ein Dashboard) Einsätze **abfragen**. Das Einsatzcockpit muss dafür keine Verbindung nach außen aufbauen und keinen Webhook kennen. Der Zugriff läuft über einen API-Key mit eigenen Feed-Scopes.

Das Datenschema für Entwickler steht unter [Einsatz-Feed-Schema](Entwickler-Einsatz-Feed).

## Einrichten

1. **Admin** → **API-Keys** → Label vergeben, im Block **Einsatz-Feed lesen** die benötigten Scopes wählen.
2. Optional **IP-Allowlist** eintragen (CIDR, kommagetrennt, z. B. `203.0.113.10/32`). Anfragen von anderen Adressen werden mit 403 abgelehnt und im Audit-Log als `api.feed.denied` festgehalten.
3. Key einmalig kopieren und im abfragenden System hinterlegen (Header `X-API-Key`). Der Key gehört nur auf den Server des Abfragenden, nie in Browser-Code.

Es ist kein zusätzlicher Modulschalter nötig: Der Feed ist freigeschaltet, sobald ein Key mit einem Feed-Scope existiert. Sperren Sie den Key, ist der Zugriff sofort beendet.

## Scopes und Datenumfang

| Scope | Liefert |
|-------|---------|
| `einsatz:read` | Stichwort, Status, Phase, Zeiten, Adresse und Koordinaten, bestätigtes Objekt, Übungs-Kennzeichen, Anzahl Einheiten |
| `einsatz:read:kraefte` | zusätzlich (`include=kraefte`, `include=wachen`): Fahrzeuge mit Kennung, Typ und Status sowie Wachen-Alarmierungsstatus |
| `einsatz:read:board` | zusätzlich (`include=board`): Spalten, Aufgaben und Meldungen nur mit Titel, Status und Zeiten |

**Nie** Teil des Feeds: Patienten und gerettete Personen, Melder, Funkjournal, Einsatz-Log, Medien, Detailtexte und Autoren von Aufgaben und Meldungen, Namen von Einsatzleiter, Fahrern und Kommandanten, KI-Texte sowie alle Tokens und PIN-Daten. Aufgaben- und Meldungstitel sind frei eingegebener Text; vergeben Sie `einsatz:read:board` deshalb nur an Systeme, die diese Titel sehen dürfen.

Adresse und Koordinaten gehören zu `einsatz:read`. Ein Key sieht nur Einsätze seiner Organisation, einschließlich Einsätzen, an denen die Organisation als Kollaborationspartner beteiligt ist. Übungen sind standardmäßig ausgeblendet.

## Betrieb

- **Abfrageintervall:** 5 bis 15 Sekunden genügen. Der Client sollte `If-None-Match` mitsenden; ohne Änderung antwortet das Einsatzcockpit mit `304` ohne Daten und ohne Aufbau der Antwort.
- **Limit:** Standardmäßig 120 Anfragen pro Minute je Key (`FEED_RATELIMIT`). Danach antwortet die API mit `429`.
- **Datenstand:** Eine Änderung an Einsatz, Fahrzeugen, Wachenstatus, Aufgaben, Meldungen, Spalten oder bestätigtem Objekt wird beim nächsten Abruf sichtbar. Umbenennungen in den Stammdaten (z. B. Fahrzeugname) erscheinen erst nach der nächsten Änderung am Einsatz.
- **Ausfallverhalten im Client:** Der Client muss „keine Verbindung“ bzw. „veraltet“ anzeigen, wenn Abfragen fehlschlagen. Ein alter Zustand darf nicht als aktuell erscheinen.

## Key-Hygiene

- Pro abfragendem System ein eigener Key mit den kleinstmöglichen Scopes.
- IP-Allowlist setzen, wenn der Client eine feste Adresse hat.
- Rotation: neuen Key anlegen, im Client umstellen, danach den alten sperren (beide Keys sind parallel gültig).
- Das Feld „Zuletzt genutzt“ in der Key-Liste wird höchstens einmal pro Minute aktualisiert.

## Fehlerbilder

| Status | Bedeutung |
|--------|-----------|
| 401 | Key unbekannt, gesperrt oder abgelaufen |
| 403 | Scope fehlt (Meldung nennt den Scope) oder IP nicht in der Allowlist |
| 404 | Einsatz nicht vorhanden oder nicht im Scope der Organisation |
| 422 | Ungültiger Parameter (`status`, `since`, `limit`, `include`) |
| 429 | Rate-Limit überschritten |
