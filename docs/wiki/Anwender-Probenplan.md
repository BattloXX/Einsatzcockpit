# Probenplan

← [Zurück zur Startseite](Home)

> Einstieg: `/probenplanung`  
> Verwaltung: `/admin/probenplanung`  
> Das Modul muss systemweit und für die jeweilige Organisation aktiviert sein.

Der **Probenplan** bündelt die Jahresplanung von Übungen und Veranstaltungen: vom Termin und der Vorbereitung über den Teilnehmerappell bis zur Nachbereitung und Archivierung. Jede Organisation verwaltet ausschließlich ihre eigenen Probearten, Vorlagen, Termine und Veröffentlichungen.

---

## Berechtigungen

| Aufgabe | Berechtigung |
|---|---|
| Probenplan und Details ansehen | Angemeldete Benutzer der Organisation |
| Termine, Checklisten, Teilnehmer und Nachbereitung bearbeiten | `probenverwalter`, `incident_leader`, `recorder`, `org_admin`, `system_admin` |
| Probearten, Checklisten-Vorlagen und öffentliche Links verwalten | `org_admin`, `system_admin` |
| Modul systemweit aktivieren | `system_admin` |

## Modul aktivieren und einrichten

1. Ein Systemadministrator aktiviert **Probenplanung** unter den System-Einstellungen.
2. Der Organisationsadministrator aktiviert das Modul unter **Verwaltung → Einstellungen** für die eigene Organisation.
3. Unter **Verwaltung → Probenplanung → Probearten** mindestens eine Probeart anlegen.
4. Bei Bedarf unter **Checklisten-Vorlagen** eine Vorlage anlegen und veröffentlichen.

Eine Probeart bestimmt neben Name, Kurzbezeichnung und Farbe auch, ob es sich um eine Übung oder Veranstaltung handelt. Sie enthält bei Bedarf eine Standarddauer, standardmäßige Teilnehmergruppen sowie Kennzeichnungen für Checkliste, Teilnehmererfassung und Nachbereitung. Optional kann für eine Probeart die Anlage eines Übungseinsatzes erlaubt werden.

### Checklisten-Vorlagen pflegen

Vorlagen bestehen aus Versionen, Bereichen und Punkten. Neue oder geänderte Inhalte werden zunächst in einer Entwurfsversion bearbeitet und anschließend veröffentlicht. Nur veröffentlichte Versionen können einer Probe als Checkliste zugeordnet werden.

Für Punkte stehen unter anderem Checkbox, Ja/Nein, Text, Datum, Uhrzeit, Person, Auswahl, Mehrfachauswahl, Datei, Bild und Link zur Verfügung. Pflichtpunkte, eine verantwortliche Person und eine Fälligkeit relativ zum Probentermin können hinterlegt werden.

Beim Anlegen einer Probe übernimmt das System die aktive Vorlage als unabhängigen Snapshot. Spätere Änderungen an der Vorlage verändern daher keine bereits vorbereiteten oder abgeschlossenen Proben.

## Jahresplanung und Termine

Die Startseite zeigt den Plan des gewählten Jahres als Liste mit Kennzahlen. Sie kann nach Probeart, Status, verantwortlicher Person, Zeitraum sowie Titel, Thema, Objekt oder Ort gefiltert werden. Zusätzlich stehen Kalenderansicht und Druckansicht zur Verfügung.

### Neue Probe anlegen

1. Im Probenplan **Probe anlegen** wählen.
2. Probeart, Titel und Beginn eintragen; Ende oder Ganztägigkeit bei Bedarf ergänzen.
3. Thema, Ort, Objekt sowie verantwortliche und unterstützende Person zuordnen.
4. Teilnehmergruppen auswählen. Die bei der Probeart hinterlegten Standardgruppen werden vorausgewählt.
5. Beschreibung, Alarmtext, Gefahren, Hinweise und interne Bemerkungen erfassen.
6. Speichern. Der Termin wird zunächst mit dem Status **Entwurf** angelegt.

Bestehende Termine können bearbeitet, dupliziert, archiviert und gedruckt werden. Bei der **Jahresübernahme** kopiert das System alle Termine eines Quelljahres in ein Zieljahr; dabei bleibt der jeweilige Wochentag innerhalb des Monats erhalten. Die übernommenen Termine starten wieder als Entwurf.

### Statusablauf

Der vorgesehene Ablauf lautet:

`Entwurf → Geplant → In Vorbereitung → Vorbereitung abgeschlossen → Durchführung läuft → Durchgeführt → Abgeschlossen`

Eine Absage ist aus Entwurf, Planung und Vorbereitung möglich. Abgesagte Termine können wieder als Entwurf oder geplant geführt werden. Der Übergang zu **Vorbereitung abgeschlossen** prüft offene Pflichtpunkte der Checkliste; ein Probenadministrator kann bei Bedarf mit dokumentiertem Grund übersteuern.

Zum endgültigen Abschluss müssen der Teilnehmerappell vollständig erfasst sein. Ist ein Übungseinsatz verknüpft, muss dieser geschlossen sein. Bei Probearten mit verpflichtender Nachbereitung muss außerdem mindestens ein Nachbereitungsfeld ausgefüllt sein.

## Vorbereitung und Unterlagen

Die Detailseite fasst Termin, Leitung, Szenario, Checklistenfortschritt und zeitnah fällige Punkte zusammen. Die Reiter führen zu den einzelnen Arbeitsbereichen.

### Checkliste

Ist über die Probeart eine aktive Vorlage hinterlegt, wird die Checkliste beim Anlegen automatisch übernommen. Andernfalls kann im Reiter **Vorbereitung** eine veröffentlichte Vorlage ausgewählt werden.

Punkte werden direkt in der Liste bearbeitet. Der Fortschritt unterscheidet Pflicht- und optionale Punkte; nicht relevante Punkte zählen nicht als offen. Zusätzliche, nur für diesen Termin relevante Punkte lassen sich unter **Individuelle Punkte** ergänzen.

### Skizzen und Dokumente

Im Reiter **Skizze** können Bilder hochgeladen und über den integrierten Annotationseditor bearbeitet werden. Im Reiter **Dokumente** werden weitere Unterlagen zur Probe abgelegt. Die Dateien sind organisationsgeschützt und nur angemeldeten, berechtigten Benutzern zugänglich.

Wenn ein Übungseinsatz mit aktiver Lageführung verknüpft ist, kann eine Lageführungs-Momentaufnahme als bearbeitbare Probenskizze übernommen werden.

## Übungseinsatz

Bei entsprechend freigegebener Probeart kann aus dem Reiter **Übungseinsatz** ein als Übung gekennzeichneter Einsatz angelegt werden. Vor dem Anlegen wird bewusst ausgewählt, welche vorbereiteten Informationen übernommen werden sollen:

- Objekt und Adresse
- Alarmtext
- besondere Gefahren und Hinweise
- Skizzen
- Dokumente

Nach dem Anlegen lässt sich der Einsatz öffnen und starten. Ein zweiter Einsatz für dieselbe Probe benötigt eine ausdrückliche Bestätigung. Die beim Einsatz erfassten Einsatzkräfte können anschließend in den Teilnehmerappell der Probe übernommen werden.

## Teilnehmerappell

Der Reiter **Teilnehmer** öffnet den schnellen Appell. Dort werden aktive Mitglieder je ausgewählter Gruppe erfasst. Für jede Person wird ein Teilnahmestatus gesetzt; einzelne Angaben wie Funktion, Notiz sowie Kommen- und Gehenzeit können ergänzt werden.

Für eine schnelle Erfassung stehen **Alle anwesend** und **Zurücksetzen** zur Verfügung. Beim Abschließen des Appells weist das System auf noch nicht erfasste Personen hin. Eine als vollständig markierte Teilnehmererfassung ist Voraussetzung für den Abschluss der Probe.

## Nachbereitung und Historie

Im Reiter **Nachbereitung** werden festgehalten:

- was gut lief,
- Verbesserungen,
- weitere Bemerkungen sowie
- Erkenntnisse mit Kategorie, optionaler Maßnahme und Erledigt-Status.

Der Reiter **Historie** zeigt protokollierte Änderungen am Termin und an wichtigen Arbeitsständen. So bleibt nachvollziehbar, wann eine Probe angelegt, bearbeitet oder archiviert wurde.

## Öffentlichen Probenplan bereitstellen

Die öffentliche Veröffentlichung wird von einem Organisationsadministrator unter **Verwaltung → Probenplanung → Öffentlicher Plan** eingerichtet.

1. **Öffentliche Auslieferung aktivieren**.
2. Einen beschrifteten Link erzeugen und die angezeigte Lese- oder Kalender-URL kopieren.
3. Bei jedem gewünschten Termin die Option **Probe öffentlich sichtbar** aktivieren.
4. Ort und öffentliche Information nur aktivieren, wenn diese Angaben ebenfalls veröffentlicht werden dürfen.

Der öffentliche Link zeigt ausschließlich sichtbare, nicht als Entwurf markierte und nicht archivierte zukünftige Termine. Vergangene Ganztagstermine verschwinden am Folgetag, Termine mit Uhrzeit nach ihrem Beginn. Abgesagte Termine bleiben als abgesagt erkennbar. Persönliche Daten, interne Bemerkungen, Alarmtext, Gefahren, Checklisten, Teilnehmer und Dokumente werden nie veröffentlicht.

Der Kalender-Link endet auf `.ics`; die zusätzlich angebotene `webcal`-Adresse kann direkt in vielen Kalender-Apps abonniert werden. Änderungen an relevanten Termindaten werden über die Kalendersequenz aktualisiert.

Behandeln Sie öffentliche Links wie Zugangsdaten: Jeder, der sie kennt, kann den freigegebenen Plan lesen. Links können jederzeit widerrufen oder neu generiert werden. Der Klartext eines neu erzeugten Links wird nur unmittelbar nach dem Erzeugen angezeigt – daher sofort sicher ablegen.

## Druck

Über **Drucken** auf der Jahresansicht wird der gefilterte Jahresplan aufbereitet. Auf der Detailseite erzeugt **Drucken** eine Einzelansicht mit den verfügbaren Probeinformationen und dem Vorbereitungsstand.

## Hinweise

- Archivierte Termine erscheinen nicht mehr im regulären Plan und nicht öffentlich.
- Für wiederkehrende Termine eignet sich die Jahresübernahme; sie übernimmt keine Teilnehmererfassungen oder Nachbereitungen.
- Eine Checkliste ist eine Kopie der veröffentlichten Vorlage. Für allgemeine Verbesserungen die Vorlage ändern, für eine einmalige Ergänzung einen individuellen Punkt verwenden.
- Öffentliche Sichtbarkeit eines Termins allein reicht nicht aus: Die organisationsweite öffentliche Auslieferung und ein aktiver Link müssen ebenfalls vorhanden sein.
