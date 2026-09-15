# Kontaktverwaltung (Administration)

Einrichtung und Betrieb des zentralen Kontaktmoduls: Aktivierung, Rollen, Kategorien,
Einsatzinfo-Freigabe, BMA-Import-Verhalten und die Offline-Sync-Schnittstelle.

Anwender-Doku: [Kontaktverwaltung](Anwender-Kontaktverwaltung)

## Modul aktivieren (zweistufig, wie Objektverwaltung/UAS)

Das Modul ist **zweistufig schaltbar** — beide Schalter müssen an sein:

1. **Systemweit** (nur `system_admin`): `/admin/settings` → Abschnitt **„Systemweite
   Module"** → **Kontaktverwaltung** → „Systemweit aktivieren". Setzt den
   SystemSettings-Key `kontakte_module_enabled`.
2. **Je Organisation** (Org-Admin): `/admin/settings` → Abschnitt **„Kontaktverwaltung"** →
   „Kontaktverwaltung für diese Organisation aktivieren". Solange das System-Flag aus ist,
   ist die Org-Checkbox ausgegraut.

Bei inaktivem Modul liefern alle `/kontakte`-Routen **404**, der Navigationseintrag
verschwindet, und Objekte zeigen im Reiter **Kontakte** keine Zuordnungsmöglichkeit.

## Rollen

| Rolle | Lesen | Anlegen/Bearbeiten/Zusammenführen/Import/Export |
|---|---|---|
| `kontakt_verwalter` | ✅ | ✅ |
| `objekt_verwalter` | ✅ | ✅ |
| `admin` / `org_admin` / `system_admin` | ✅ (über allgemeine Admin-Rechte) | — |
| `readonly`, `recorder`, `breathing_supervisor`, `incident_leader`, `fahrtenbuch_admin` | ✅ | ❌ |

Schreibrechte (Anlegen, Bearbeiten, Archivieren, Zusammenführen, Import, Export) sind auf
`kontakt_verwalter` und `objekt_verwalter` beschränkt — letztere Rolle deshalb, weil
Objekt-Kontaktzuordnungen ohne zentrale Kontaktpflege wenig Sinn ergeben. Alle anderen
oben gelisteten Rollen dürfen die Kontaktliste einsehen, aber nichts ändern.

## Kategorien pflegen

Kategorien (z. B. „Hausverwaltung", „Behörde", „Schlüsselträger") sind frei benennbare,
org-weite Schlagworte ohne feste Vorbelegung — sie entstehen beim ersten Anlegen eines
Kontakts mit einer neuen Kategoriebezeichnung und lassen sich über die Kontaktliste als
Filter verwenden. Es gibt keine separate Verwaltungsseite dafür; eine nicht mehr verwendete
Kategorie verschwindet automatisch aus dem Filter, sobald kein Kontakt sie mehr trägt.

## Einsatzinfo-Freigabe je Kontakt

Diese Funktion ist von der org-weiten [Einsatzinfo-SMS](Administration-SMS-Einsatzinfo)
(Verteiler an eigene Mitglieder) zu unterscheiden: Hier geht es um **automatische
Benachrichtigung externer, objektbezogener Kontakte** (z. B. Hausverwaltung,
Brandschutzbeauftragter) bei Alarm an genau diesem Objekt.

Pro Objekt lässt sich unter **Objekt → 🔔 Benachrichtigung** einstellen:

| Einstellung | Bedeutung |
|---|---|
| Auch bei Übungen senden | Standard: aus |
| Nur bei Stichworten (kommagetrennt) | Leer = alle Stichworte lösen aus |
| Mail-Betreff / Nachrichtentext | Leer = Org-Standard aus `/admin/settings` (bzw. der Auslieferungs-Standard), objektspezifisch überschreibbar |

**Verfügbare Platzhalter:** `{objekt}`, `{objektnummer}`, `{vulgoname}`, `{stichwort}`,
`{adresse}`, `{ort}`, `{meldung}`, `{einsatzgrund}`, `{datum}`, `{zeit}`, `{feuerwehr}`,
`{kontakt}` (Anzeigename des jeweiligen Empfängers), `{leitstellennummer}`.

> ⚠️ `{meldung}` gibt den Alarmtext unverändert an externe Empfänger weiter — bei Bedarf
> aus der Vorlage entfernen, wenn der Meldungstext personenbezogene Daten enthalten kann.

Empfänger sind ausschließlich Kontakte mit **aktiver Freigabe** an der jeweiligen
Objekt-Zuordnung (siehe [Anwender-Doku](Anwender-Kontaktverwaltung#einsatzinfo-freigabe-je-kontakt)).
Der Versand läuft als Hintergrund-Task nach Einsatzanlage — ausgelöst sowohl über
Alarm-Matching (automatisch erkanntes Objekt) als auch über manuelle Objektzuordnung am
Einsatz; protokolliert im allgemeinen SMS-/Mail-Log.

**Architekturhinweis:** Die Freigabe ist bewusst an der Objekt-Zuordnung
(`objekt_kontakt_freigabe`) mit **denormalisiertem Zielwert** gespeichert, nicht als Flag
am zentralen Kontakt oder an der Telefonnummer selbst. Ändert sich die Nummer eines
Kontakts, wandert eine bestehende Freigabe **nicht automatisch mit** — sie bleibt auf dem
alten Wert stehen (sichtbar als inaktive/veraltete Freigabe) und muss bewusst neu gesetzt
werden. Das verhindert, dass eine ungeprüfte neue Nummer stillschweigend Einsatzdaten
erhält.

## BMA-Datenblatt-Import: Verhalten bei Kontakten

Der BMA-Import (siehe [Administration → Objektverwaltung](Administration-Objektverwaltung))
schreibt Kontakte über denselben zentralen Kontaktbestand, nicht in eine separate Tabelle:

- Kontakte aus einem Datenblatt werden über eine **stabile externe Referenz**
  (Quelle + Kontext + externe ID) wiedererkannt — ein erneuter Upload desselben oder eines
  aktualisierten Datenblatts aktualisiert den bestehenden Kontakt statt eine Dublette
  anzulegen.
- **Händisch angelegte Kontakte und händisch ergänzte Felder fasst der Import nie an** —
  nur Zeilen/Zuordnungen mit gesetzter externer Referenz gehören dem Import.
- Wird eine SMS-/Mail-Freigabe durch einen Re-Sync automatisch entzogen (z. B. weil eine
  Nummer im neuen Datenblatt fehlt), erscheint das im Audit-Log des Objekts unter dem
  Bereich „Kontakte".
- Leere oder als Text übernommene „None"/"null"-Werte aus dem PDF-Parsing werden vor dem
  Speichern normalisiert und landen nicht als sichtbarer Text im Kontaktfeld.
- Neue/aktualisierte Kontakte sowie neue/entfernte Objektzuordnungen aus dem BMA-Import
  erscheinen wie jede andere Änderung im [Offline-Sync-Feed](#offline-sync-fur-externe-clients).

## Offline-Sync für externe Clients

`GET /api/v1/kontakte/sync` liefert externen/mobilen Clients (z. B. einer nativen App) den
Kontaktbestand einer Organisation als **versioniertes Snapshot+Delta-Modell** —
authentifiziert per `X-API-Key` (siehe [API-Keys verwalten](Administration-API-Keys-verwalten)).
Der Endpunkt verlangt keinen speziellen Scope, nur einen aktiven, org-gebundenen Key.

- **Erster Abruf** (kein `cursor`): liefert einen paginierten **Snapshot** aller Kontakte
  (`contacts`, inkl. Telefonnummern) sowie — nur auf der ersten Seite (`page_after=0`) —
  alle **Objekt-Zuordnungen** (`mappings`). Bei mehr Ergebnissen als `limit` liefert
  `next_page` die ID, mit der die nächste Seite (`page_after`) abzufragen ist. Die Antwort
  enthält einen `cursor`-Wert, ab dem anschließend Delta-Abrufe möglich sind.
- **Folgeabrufe** (mit `?cursor=<wert>`): liefern nur noch die **Änderungen** seit diesem
  Cursor (`changes`, je Eintrag `entity`, `id`, `operation` — `upsert` oder `tombstone` —
  und `payload`), ohne den kompletten Bestand erneut zu übertragen. `has_more=true`
  signalisiert, dass weitere Seiten mit demselben Aufruf und dem zurückgegebenen `cursor`
  nachgeladen werden müssen.

Änderungen werden serverseitig als Append-only-Feed (`kontakt_sync_aenderung`)
mitgeschrieben — sowohl bei manuellen Änderungen über die UI als auch beim BMA-Import.
`schema_version` im Antwortobjekt erlaubt Clients, ein zukünftiges Format-Update zu
erkennen.

## Bekannte Einschränkungen

- Der Duplikat-Dialog beim Anlegen bietet aktuell nur „trotzdem anlegen" und „bestehenden
  verwenden" — kein direkter Feld-für-Feld-Vergleich an dieser Stelle (dafür steht das
  reguläre Zusammenführen-Werkzeug zur Verfügung).
- Beim manuellen Zusammenführen zweier Kontakte werden Freigabe-Konflikte an
  gemeinsam zugeordneten Objekten nicht vor dem eigentlichen Merge separat aufgelistet.

---

**Verwandt:** [Anwender: Kontaktverwaltung](Anwender-Kontaktverwaltung) · [Objektverwaltung](Administration-Objektverwaltung) · [SMS-Einsatzinfo & Empfang](Administration-SMS-Einsatzinfo) · [API-Keys verwalten](Administration-API-Keys-verwalten)
