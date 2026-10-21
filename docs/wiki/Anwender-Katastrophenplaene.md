# Einsatz- & Katastrophenpläne

Unter **Module → Einsatz- & Katastrophenpläne** (Adresse `/plaene`; alte Links unter
`/katastrophenplaene` leiten weiter) pflegt die Organisation ihre Pläne direkt im
Einsatzcockpit. Jeder Plan hat eine **Planart**:

| Planart | Kürzel | Typischer Inhalt | Prüfintervall (Standard) |
|---|---|---|---|
| **Alarm- und Einsatzplan** | AEP | Objekt- oder lagebezogener Plan: Alarmierung, Erstmaßnahmen, Verständigungen, Anfahrt | 12 Monate |
| **Maßnahmenplan** | MP | Maßnahmen je Lage und Auslöser, z. B. Hochwasser-Maßnahmenplan | 24 Monate |
| **Katastrophenplan** | KP | Umfassender Plan für Großschadenslagen (Blackout, Sturm …) | 24 Monate |

Ein Plan besteht aus Kapiteln, Phasen und Elementen. Vorhandene PDF-Pläne lassen sich als
Quellen hochladen und Seite für Seite übernehmen.

- **Leser** (Einsatzrollen wie `readonly`, `recorder`, `incident_leader`) sehen nur die
  **freigegebene Fassung**.
- **Bearbeiter** (`katplan_verwalter`) ändern den **Arbeitsstand**.
- **Freigeber** (`katplan_freigeber`) geben ihn frei.

Rollen und Schalter: [Administration](Administration-Katastrophenplaene).

## Liste und Suche

Die Liste zeigt alle sichtbaren Pläne mit Planart, Gefahrenart, Status, Version und
nächster Überprüfung. Filter nach Planart, Gefahrenart, Status, „Überprüfung fällig“ und
Titel grenzen sie ein; archivierte Pläne zeigt der
Schalter **Archiv**. Bearbeiter sehen zusätzlich die Zahl offener Qualitätshinweise je Plan.

**Inhalte suchen** (Liste und Lesesicht) durchsucht Elemente und den Volltext verarbeiteter
Quellseiten:

- Mehrere Wörter müssen alle vorkommen.
- Filter: Plan, Gefahrenart, Typ, Priorität, Phase (bei gewähltem Plan), Ort und „nur geprüfte Orte“.
- **Umkreis**: Adresse suchen oder **Mein Standort** verwenden, dann einen Radius
  (500 m bis 10 km) wählen. Treffer werden nach Entfernung sortiert.
- Bearbeiter können mit **Arbeitsstand durchsuchen** auch noch nicht freigegebene Inhalte finden.
- Jeder Treffer führt direkt zum Element oder zur Quellseite.

## Plan lesen

Die Lesesicht zeigt die freigegebene Version: Kopf mit Planart, Gefahrenarten,
Geltungsbereich, verantwortlicher Stelle, Version und Prüfvermerk („Zuletzt geprüft …
von … · Nächste Überprüfung … · Überprüfung durch …“), darunter Inhaltsverzeichnis,
Kapitel und Elemente.

- **Phasenfilter** (Chips oben) blenden nur die Elemente einer Phase ein, z. B.
  „Stufe 2 – Übergehen“ oder „Alarmstufe 2“.
- **Akteurfilter**: Sind Maßnahmen Akteuren zugeordnet (Feuerwehr, Netzbetreiber,
  Behörde …), blendet die zweite Chipzeile nur die Elemente eines Akteurs ein.
  **Gliederung: Kapitel | Akteur** stellt den Plan wahlweise nach Akteur gruppiert dar –
  so sieht jede Stelle auf einen Blick, was sie zu tun hat.
- **Verständigungsliste** (Alarm- und Einsatz- sowie Maßnahmenpläne): alle verknüpften
  Kontakte mit der Rolle „Zu verständigen“ oder „Verantwortlich“, nach Akteur gruppiert,
  mit den aktuellen Telefonnummern aus den zentralen Kontakten.
- Elemente zeigen Kennung (z. B. `KS-03`), Typ, Priorität, Phasen, Akteure, Beschreibung,
  typspezifische Angaben (Schritte, Bedingungen, Materialbedarf, Gefahren …), Adresse,
  Verknüpfungen, Beziehungen, Anhänge und die **Quelle** (Dokument, Version, Seite).
- **Quellennachweis je Maßnahme**: Der Chip „Quelle S. 13“ führt zur Seitenansicht. Unter
  **Vorschau** stehen die Miniaturen der referenzierten Seiten; **Original-PDF auf Seite
  öffnen** springt im Original direkt auf die erste referenzierte Seite.
- **Verknüpfte Kontakte** werden live angezeigt. Telefonnummern erscheinen nur, wenn du das
  Kontaktverzeichnis sehen darfst. Sonst erscheinen nur Dienstnummern einsatzrelevanter
  Kontakte. „Seit Freigabe geändert“ weist auf neue Kontaktdaten hin.
- Badges: **aus Dokument übernommen** (Herkunft Planquelle), **KI-Vorschlag**
  (über KI oder MCP erstellt), **vertraulich** (nur für berechtigte Rollen sichtbar).

## Karte

Der Reiter **Karte** zeigt verortete Elemente als Punkte, Linien oder Flächen. Filter für
Phase, Typ und Priorität grenzen die Anzeige ein. Ungeprüfte Orte sind gekennzeichnet; prüfe
ihre Lage vor der Verwendung. Bearbeiter können zwischen freigegebener Fassung und
Arbeitsstand umschalten.

## Versionen und Vergleich

Jede Freigabe erzeugt eine unveränderliche Version. Unter **Versionen** stehen alle
Freigaben mit Datum, Freigeber und Änderungsnotiz. Zwei Versionen (oder Version und
Arbeitsstand) lassen sich vergleichen: neue, geänderte und entfernte Elemente werden je
Feld gegenübergestellt. Ältere Versionen bleiben samt ihren Anhängen und Quellseiten lesbar.

## PDF / Offlinefassung

**PDF / Offlinefassung** lädt die freigegebene Version herunter. Enthalten sind:

- Deckblatt mit Planart, Fassung, Geltungsbereich, verantwortlicher Stelle, Prüfvermerk und Ansprechstellen
- Inhaltsverzeichnis mit Seitenzahlen bis auf Elementebene und eine Phasenübersicht
- Kapitel in Planreihenfolge, je Element eine Karte mit Prioritätsfarbe, Beschreibung und
  typspezifischen Angaben: Schritte als Checkliste zum Abhaken, Wenn/Dann und Materialbedarf
  als Tabelle, Gefahren als Liste
- Adresse, Ort mit Koordinaten und Prüfstatus, Gültigkeit, Quelle und Seite mit
  Fußnotennummer; am Ende ein **Quellenverzeichnis** (Dokument, Version, Herausgeber/Stand,
  Seiten)
- Verknüpfungen mit Telefonnummern (nach deinen Rechten)
- eine Anhangsliste mit bis zu zwei Bildvorschauen je Element
- bei AEP und Maßnahmenplänen die Verständigungsliste

**PDF nach Akteur** (Auswahl neben dem PDF-Button, sobald Akteure vorkommen) gliedert die
Elemente nach Akteur oder erzeugt einen **Auszug für einen Akteur**, z. B. nur den Teil für
den Netzbetreiber zum Weitergeben.

In **Versionen** lässt sich jede frühere Freigabe als PDF speichern. Bearbeiter können ein
PDF des Arbeitsstands mit dem Wasserzeichen **ENTWURF – nicht freigegeben** erzeugen.
Bewahre die aktuelle Fassung für den Einsatz offline auf und lade sie nach jeder neuen
Freigabe erneut.

## Plan bearbeiten (Bearbeiter)

**+ Neuer Plan** legt einen Plan mit Planart, Titel, Gefahrenarten (bei Katastrophenplänen
Pflicht) und Beschreibung an. Für Alarm- und Einsatzpläne werden die Kapitel Alarmierung,
Erstmaßnahmen, Einsatzdurchführung, Verständigungen, Objekt- und Anfahrtsinformationen und
Abschluss sowie die Phasen Alarmstufe 1–3 vorbelegt; für Maßnahmenpläne die Kapitel Lage und
Auslöser, Maßnahmen, Verständigungen, Ressourcen und die Phasen Vorwarnung, Alarm,
Entwarnung. Alles lässt sich danach anpassen.
**Bearbeiten** öffnet den Editor: links der Kapitelbaum, rechts das Formular des gewählten
Elements.

- **Kapitel** anlegen, umbenennen und per Drag-and-drop sortieren; **+ Unterkapitel** für
  eine zweite Ebene.
- **Phasen verwalten**: Phasen wie „Vorwarnung“ oder „Stufe 2 – Übergehen“ mit Auslöser,
  Farbe und Reihenfolge. Das Formular **Neue Phase** steht oben.
- **Elemente** über die Schnellerfassung je Kapitel anlegen: Typ oder **Vorlage**
  (Gewässer-Kontrollstelle, Verständigung, Materialbereitstellung, Sicherheitshinweis)
  wählen, Titel eingeben. Die Kennung wird automatisch vergeben.
- **Elementtypen:**
  - Information/Hinweis
  - Maßnahme/Checkliste (Schritte)
  - Kontroll-/Gefahrenstelle (Gefahren, Kontrollhinweise, Zugänglichkeit)
  - Gefahrenbereich/Kontrollstrecke
  - Ablauf/Entscheidungshilfe (Wenn/Dann)
  - Ressourcen-/Materialbedarf (Bezeichnung, Menge, Einheit, Lagerort)
  - Dokument-/Medienverweis
  - Kontakt-/Zuständigkeitsverweis
  - Objektverweis
- **Formular**:
  - Titel, Kapitel, Beschreibung (Editor mit Zwischenüberschriften und Listen),
    Priorität, Tags, Gültig bis, Prüfen am, Vertraulich.
  - **Zuständig (Akteure)**: eine oder mehrere Stellen, die die Maßnahme ausführen
    (Feuerwehr, Netzbetreiber, Behörde, Polizei, Rettungsdienst, Gemeinde/Bauhof,
    Sonstige; der Katalog ist erweiterbar).
  - Adresse, Phasen und die typspezifischen Angaben.
  - Quelle: Dokument und Seiten. **Seite wählen** zeigt alle Seiten des Dokuments als
    Miniaturen; ein Klick setzt die Seite, **+** ergänzt weitere Seiten.
  - Speichern aktualisiert sofort.
  - Hat jemand anderes das Element inzwischen geändert, erscheint ein Hinweis, statt die
    Änderung zu überschreiben.
- **Verortung**:
  - Auf der Karte im Formular Marker, Linie oder Fläche zeichnen.
  - Oder **Adresse suchen**: Das Feld ist mit der Adresse des Elements vorbelegt. Ist kein
    Ort eingetragen, wird der Ort der Organisation ergänzt. **Suchen** oder Enter zeigt die
    Treffer.
  - Ein Treffer setzt den Marker und füllt Straße, Hausnummer, PLZ und Ort aus.
  - **Ort geprüft** erst setzen, wenn die Lage vor Ort oder anhand gesicherter Unterlagen
    bestätigt ist. Jede Änderung der Geometrie setzt den Ort wieder auf „ungeprüft“.
- **Verknüpfungen**:
  - Kontakte, Kontakt-Organisationen, Objekte, Fahrzeuge oder Wasserstellen suchen und mit
    Rolle verknüpfen (z. B. „Zu verständigen“, „Zuständig“).
  - Angezeigt werden immer die aktuellen Stammdaten; es wird nichts kopiert.
  - **Telefonnummern nie in den Text schreiben**, sondern den zentralen Kontakt verknüpfen.
    So bleibt jede Nummer an einer Stelle gepflegt und ist in allen Plänen aktuell.
- **Beziehungen** zwischen Elementen: z. B. „Folgt auf“ oder „Siehe auch“, per Kennung oder
  Titel suchen.
- **Anhänge**:
  - Fotos und Dateien hochladen (mit Beschriftung, Urheber und Aufnahmedatum), Links oder
    Objektdokumente anfügen.
  - Anhänge sind an Plan, Kapitel oder Element möglich und lassen sich als vertraulich
    markieren.
- **Duplizieren** kopiert ein Element in denselben oder einen anderen Plan (ohne Anhänge).
  **Verschieben** und die Pfeile ändern die Reihenfolge.

### Qualitätshinweise

Der Editor zeigt oben eine Summenzeile (z. B. „Qualität: 12 ohne Geometrie · 3 KI
ungeprüft“). Die Symbole im Baum stehen für folgende Hinweise:

| Symbol | Hinweis |
|---|---|
| 📍 | Kontroll-/Gefahrenstelle oder Gefahrenbereich ohne Geometrie |
| ❔ | Ort ungeprüft |
| 🔗 | Verknüpfungsziel fehlt (archiviert, gelöscht oder Modul aus) |
| 👤 | Kontakt seit der letzten Freigabe geändert |
| ⏰ | Prüftermin („Prüfen am“) erreicht |
| ⌛ | Gültigkeit abgelaufen |
| 🤖 | KI-Vorschlag noch nicht in der Oberfläche geprüft und gespeichert |
| 📄 | Die Quelle hat eine neuere Version |
| 📞 | Telefonnummer im Text – zentralen Kontakt verknüpfen |
| 👥 | Maßnahme ohne zuständigen Akteur (nur AEP und Maßnahmenplan) |
| 🔎 | Maßnahme ohne Quellenangabe, obwohl der Plan Quelldokumente hat |

**Telefonnummern umwandeln:** Beim Hinweis „Telefonnummer im Text“ öffnet **Kontakt
verknüpfen** einen Assistenten. Er zeigt jede gefundene Nummer (aus Beschreibung, Schritten
und anderen Feldern) und sucht passende zentrale Kontakte – auch wenn die Schreibweise
abweicht (`0664 …` und `+43 664 …`). Du kannst

- einen gefundenen Kontakt mit einer Rolle verknüpfen oder
- einen neuen Kontakt anlegen (nur mit Recht zur Kontaktpflege; die Nummer wird als
  Dienstnummer gespeichert, der Kontakt als einsatzrelevant markiert)

und die Nummer samt „Tel.:“ gleich aus dem Text entfernen lassen.

Die Summenzeile öffnet das erste betroffene Element. Hinweise sperren die Freigabe nicht;
enthält ein Plan noch Telefonnummern im Text, weist der Freigabebereich darauf hin.
Ein KI-Element gilt als geprüft, sobald es einmal bewusst im Editor gespeichert wurde.
Eine Änderung über MCP genügt dafür nicht.

### Freigabe-Ablauf

| Schritt | Wer | Wirkung |
|---|---|---|
| **Einreichen** | Bearbeiter | Status „in Prüfung“, optional mit Kommentar |
| **Zurückweisen** | Freigeber | zurück in „Entwurf“ bzw. „in Überarbeitung“, Kommentar Pflicht |
| **Freigeben** | Freigeber | neue Version mit Änderungsnotiz, sofort für Leser sichtbar |
| **Archivieren / Reaktivieren** | Freigeber | Plan aus der Liste nehmen bzw. zurückholen |

Ein Vier-Augen-Prinzip gibt es nicht; ein Freigeber kann auch eigene Änderungen freigeben,
Einreichen ist dafür nicht nötig. Status: Entwurf → (in Prüfung) → freigegeben → in Überarbeitung → …
Ändert jemand einen freigegebenen Plan, wechselt er auf **in Überarbeitung**. Leser sehen
bis zur nächsten Freigabe weiterhin die alte Version.

### Regelmäßige Überprüfung

Unter **Stammdaten** legst du fest, **wer** den Plan überprüft (ECP-Benutzer mit
Plan- oder Admin-Rolle; Standard: wer den Plan angelegt hat) und in welchem
**Prüfintervall** (leer = Standard der Planart). Die **Verantwortliche Stelle (extern)**
bleibt davon getrennt und beschreibt, wer fachlich für den Plan zuständig ist.

- Jede **Freigabe** zählt als Überprüfung und setzt die nächste Überprüfung auf
  heute + Intervall.
- Hat sich nichts geändert, bestätigt die verantwortliche Person (oder ein Freigeber) im
  Kopf **Überprüfung bestätigen**, optional mit Kommentar. Dabei entsteht keine neue
  Version; der Eintrag „Überprüft (ohne Änderung)“ erscheint unter **Versionen**.
- Hat der Plan offene Änderungen („in Überarbeitung“), wird die Überprüfung über die
  nächste Freigabe abgeschlossen.
- **Erinnerungen per Mail:** 14 Tage vorher und bei Fälligkeit an die verantwortliche
  Person; ist die Überprüfung 30 Tage überfällig, zusätzlich an die Freigeber.
- Nur Termin, Intervall oder prüfende Person zu ändern, setzt einen freigegebenen Plan
  nicht in Überarbeitung.
Das **Protokoll** zeigt alle Änderungen mit Benutzer, Zeit und Herkunft (Oberfläche oder MCP).

## Planquellen (PDF-Pläne)

Unter **Quellen** werden vorhandene PDF-Pläne hochgeladen: Titel, Herausgeber und Stand
angeben. Die Verarbeitung zerlegt das PDF in Seiten mit Vorschaubild und Volltext; der
Status aktualisiert sich selbst.

- Die **Seitenansicht** zeigt alle Seiten als Raster. Eine Einzelseite bietet Bild,
  Volltext, Blättern, Sprung zu Seite, Einzelseiten-PDF und den Download des Originals.
- **Element aus dieser Seite anlegen** übernimmt Seitentext und Titel (Aufzählungen werden
  zu Listen) und öffnet das neue Element im Editor. Herkunft: „aus Dokument übernommen“.
- **Neue Version hochladen** legt eine weitere Version derselben Quelle an. Vorhandene
  Elemente und Freigaben bleiben unverändert.
- **Mit v… vergleichen** zeigt je Seitennummer unveränderte, geänderte, neue und entfallene
  Seiten mit kurzem Textvergleich.
  - **Betroffene Elemente** listet Elemente, deren Quellseiten sich geändert haben.
  - **Quelle auf v… umstellen** ändert nur die Dokumentreferenz und protokolliert die
    Aktion. Inhalt und Seitenbezug danach im Editor prüfen.
- **Entfernen** löscht eine Quelle nur, wenn kein Element und keine Freigabe sie verwendet;
  sonst wird sie archiviert.

### KI-Vorschläge aus Quellen

Ist der KI-Import für die Organisation aktiviert, erzeugt **KI-Vorschläge erzeugen** in
der Quellenliste Elementvorschläge je Seite. Unter **Import** (bzw. „n offene Vorschläge
prüfen“) steht für jeden Vorschlag eine Karte mit bearbeitbarem Typ, Titel, Priorität,
Kapitel und Phase (vorhanden oder neu), Adresse und Beschreibung (eine Zeile je Absatz,
Listenpunkte mit •). Die Karte zeigt außerdem:

- die typspezifischen Angaben, die mit übernommen werden;
- im Dokument genannte Kontakte mit dem Hinweis „Telefonnummer aus dem Dokument, nicht
  geprüft und nicht übernommen“;
- bei eindeutig gefundenen Kontakten bzw. Objekten ein Häkchen **verknüpfen**, das
  standardmäßig aus ist.

**Übernehmen** legt das Element an (Herkunft „KI-Vorschlag“, Quellseite gesetzt),
**Verwerfen** entfernt den Vorschlag. **Ausgewählte übernehmen** übernimmt mehrere
Vorschläge mit ihren Vorschlagswerten. Gleichnamige Kapitel und Phasen werden dabei
zusammengeführt, Kontakte und Objekte nicht verknüpft. Ohne Bestätigung entsteht kein
Element. Koordinaten werden nie übernommen; Mengen und Telefonnummern immer fachlich prüfen.
Vorschläge können auch von einem KI-Assistenten über MCP kommen
([MCP-Assistent](Anwender-MCP-Assistent)).

Siehe auch: [Administration](Administration-Katastrophenplaene), [Entwicklung](Entwickler-Katastrophenplaene).
