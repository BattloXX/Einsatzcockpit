# Kontaktverwaltung

← [Zurück zur Startseite](Home)

Die Kontaktverwaltung ist ein **zentrales, organisationsweites Adressbuch**: Personen und
Stellen (Hausverwaltung, Brandschutzbeauftragter, Betreiber, Schlüsselträger, …) werden
hier **einmal** gepflegt und dann beliebigen [Objekten](Anwender-Objekte) zugeordnet —
statt wie früher pro Objekt einen eigenen, doppelt gepflegten Kontakt anzulegen.

> Das Modul erscheint nur, wenn es für deine Organisation aktiviert ist
> (siehe [Administration → Kontaktverwaltung](Administration-Kontaktverwaltung)).
> Einstieg: **Dokumentation → Kontakte** in der Hauptnavigation, oder direkt über
> „Kontakt zuordnen" im Objekt-Reiter **Kontakte**.

## Kontaktliste

`/kontakte` zeigt alle Kontakte deiner Organisation mit Suche über Name, Organisation,
E-Mail und Telefonnummer sowie einem Filter nach Kategorie. Archivierte Kontakte werden
standardmäßig ausgeblendet.

Jeder Kontakt hat einen **Typ**: `Person` oder `Stelle` (z. B. eine Firma/Hausverwaltung
ohne konkreten Ansprechpartner).

## Kontakt anlegen oder bearbeiten

Ein Kontakt trägt: Anzeigename, Vor-/Nachname, Funktion, Organisation, E-Mail, beliebig
viele **Telefonnummern** (mit Bezeichnung wie „Mobil"/„Büro", einer bevorzugten Nummer
und individueller Reihenfolge), freie **Erreichbarkeit**-Notiz, Notizen, ein **Profilbild**
sowie **Kategorien** (frei pflegbare Schlagworte wie „Hausverwaltung", „Behörde").

**SMS-Fähigkeit je Nummer:** Beim Tippen einer Nummer wird automatisch geprüft, ob es sich
um eine österreichische Mobilnummer handelt (Vorwahlen 0650–0699), und die Checkbox
„SMS-fähig" entsprechend vorbelegt — das steuert, ob die Nummer später als SMS-Kanal zur
Auswahl steht (siehe [Einsatzinfo-Freigabe](#einsatzinfo-freigabe-je-kontakt) unten). Die
Checkbox bleibt jederzeit manuell übersteuerbar; eine manuelle Änderung wird nicht mehr
von der Auto-Erkennung überschrieben.

**Anhänge:** An jeden Kontakt lassen sich Dateien (z. B. Vollmachten, Schlüsselübergabe-
Protokolle) anhängen.

### Dubletten-Erkennung

Beim Anlegen prüft das System automatisch gegen bestehende Kontakte — Treffer bei
identischer E-Mail, identischer (normalisierter) Telefonnummer, oder gleichem
Anzeigename **und** gleicher Organisation. Bei einem Treffer wird vor dem Speichern eine
Liste möglicher Duplikate angezeigt; du kannst trotzdem einen neuen, separaten Kontakt
anlegen oder stattdessen den bestehenden verwenden.

## Kontakt einem Objekt zuordnen

Im Objekt-Reiter **Kontakte** wird ein zentraler Kontakt gesucht und mit einer **Rolle**
verknüpft (Brandschutzbeauftragter, Betreiber, Hausverwaltung, Schlüsselträger,
Sonstig), optional mit einer objektspezifischen Erreichbarkeits-Notiz und einer
Sortierposition. Derselbe zentrale Kontakt kann gleichzeitig mehreren Objekten
zugeordnet sein — Änderungen an seinen Stammdaten (z. B. eine neue Telefonnummer)
wirken sofort überall, ohne dass die Zuordnung selbst angefasst wird.

### Einsatzinfo-Freigabe je Kontakt

Pro Objekt-Zuordnung lässt sich für **jede einzelne Telefonnummer** eine SMS-Freigabe und
für die E-Mail-Adresse eine Mail-Freigabe aktivieren. Nur freigegebene Kanäle erhalten bei
Alarm eine automatische **Einsatzinfo** — die Freigabe ist bewusst je Objekt-Zuordnung und
je konkretem Wert gespeichert (nicht am zentralen Kontakt), damit:

- ein Kontakt, der mehreren Objekten zugeordnet ist, pro Objekt unterschiedlich
  freigegeben sein kann,
- eine spätere **Nummernänderung am zentralen Kontakt niemals automatisch eine
  bestehende Freigabe mitnimmt** — die alte Freigabe bleibt auf dem alten Wert stehen
  und muss bei Bedarf bewusst neu gesetzt werden. Das ist Absicht: eine Freigabe soll nie
  unbemerkt auf eine andere, ungeprüfte Nummer wandern.

Im Objekt-Abschnitt **🔔 Benachrichtigung** siehst du die aktuell wirksamen Empfänger
(Name, Kanal, Zielwert) sowie eine Vorschau von Betreff/Text mit Beispielwerten. Löst ein
Alarm am Objekt aus (und passt ggf. das Stichwort-Filter, siehe
[Administration](Administration-Kontaktverwaltung)), bekommt jeder freigegebene Kanal
automatisch eine SMS bzw. Mail mit den Einsatzdaten (Adresse, Meldung, Stichwort, Link
zur öffentlichen Einsatzinformation, …).

> ⚠️ Der Platzhalter `{meldung}` gibt den Alarmtext unverändert an externe Empfänger
> weiter — er kann personenbezogene Melderdaten enthalten.

## Kontakte zusammenführen

Wurden versehentlich zwei Kontakte für dieselbe Person/Stelle angelegt, lassen sie sich
verlustfrei zusammenführen: für jedes Feld (Name, Funktion, Organisation, E-Mail, …)
wählst du, ob der Wert aus Quelle oder Ziel übernommen wird. Telefonnummern, Kategorien,
Anhänge und externe Referenzen (z. B. aus dem BMA-Import) wandern beide auf den Zielkontakt,
Dubletten werden automatisch entfernt. Alle Objekt-Zuordnungen der Quelle wandern auf den
Zielkontakt — bestehen für dasselbe Objekt bereits Zuordnungen auf beiden Seiten, wird das
als Konflikt gemeldet, statt die Zuordnung stillschweigend zu duplizieren.

## Archivieren

Ein Kontakt lässt sich archivieren statt löschen — er verschwindet aus Liste und Suche,
bleibt aber in bestehenden Objekt-Zuordnungen und der Historie erhalten. Archivierte
Kontakte tauchen nicht mehr als Dubletten-Kandidat oder Zuordnungs-Ziel auf.

## Import und Export

Unter **Kontakte → Import** lässt sich eine CSV- oder XLSX-Datei hochladen (Vorlage über
„Vorlage herunterladen" verfügbar, wahlweise mit Beispielzeile). Vor der Übernahme zeigt
eine **Vorschau** jede Zeile mit Status (neu / aktualisiert / Fehler) sowie optional
Objektzuordnungen anhand einer Objektnummer-Spalte. Erst nach Bestätigung werden die
Kontakte tatsächlich angelegt bzw. aktualisiert; danach lässt sich das Ergebnis als CSV
herunterladen. Ein Roundtrip Export → Bearbeiten in Excel → Re-Import ist damit möglich,
ohne bestehende Einsatzinfo-Freigaben zu verlieren (die bleiben unverändert bestehen, auch
wenn sich z. B. die Telefonnummer nicht geändert hat).

Kontakt-Stammdaten und Telefonnummern liegen dabei gemeinsam in einer flachen CSV bzw. im
Blatt **Kontakte** der XLSX-Datei. Pro Kontakt stehen bis zu drei Telefonnummern direkt in
derselben Zeile: `telefon_1`, `telefon_1_bezeichnung`, `telefon_1_bevorzugt` und
`telefon_1_sms` (entsprechend bis `telefon_3`). Die `id` ist optional; mit leerer `id`
wird ein neuer Kontakt angelegt. Weitere Telefonnummern können nach dem Import über die
normale Bearbeiten-Maske ergänzt werden. Objektzuordnungen bleiben nur in XLSX als eigenes
Blatt erhalten.

**Export** (`Kontakte → Export`) liefert alle aktiven Kontakte deiner Organisation als CSV
oder XLSX.

## SMS an einen Kontakt senden

Aus der Kontakt-Detailansicht lässt sich (mit entsprechender Berechtigung) direkt eine SMS
an eine seiner Nummern senden — praktisch für kurzfristige Rückfragen, ohne den Umweg über
die allgemeine SMS-Versandseite.

## BMA-Datenblatt-Import

Kontakte, die aus einem hochgeladenen BMA-Datenblatt (Brandmeldeanlagen-PDF) stammen,
werden automatisch angelegt/aktualisiert und über eine externe Referenz stabil
wiedererkannt — ein erneuter Upload desselben Datenblatts legt keine Dubletten an. Solche
Kontakte sind in der Objekt-Kontaktliste am Symbol/Hinweis „aus BMA-Datenblatt" erkennbar;
manuell nachgetragene Ergänzungen (z. B. eine zusätzliche private Nummer) bleiben beim
nächsten Re-Sync erhalten, sofern sie nicht in Konflikt mit den importierten Daten stehen.
Details zur Zuordnungslogik: [Administration → Objektverwaltung, Abschnitt
BMA-Datenblatt-Import](Administration-Objektverwaltung).

---

**Verwandt:** [Objekte](Anwender-Objekte) · [Administration: Kontaktverwaltung](Administration-Kontaktverwaltung) · [SMS-Einsatzinfo & Empfang](Administration-SMS-Einsatzinfo)
