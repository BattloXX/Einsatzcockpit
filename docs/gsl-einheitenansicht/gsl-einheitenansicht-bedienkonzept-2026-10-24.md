# ECP – Phase 1 / Auftrag 1.2: Bedienkonzept für Gruppenkommandanten

Stand: 2026-10-24 · **Verbindliche Sollvorgabe für die weitere Umsetzung**, noch keine umgesetzte Oberfläche.

Grundlage sind die [Bestandsanalyse aus Auftrag 1.1](gsl-einheitenansicht-bestandsanalyse-2026-10-24.md) und der dort untersuchte Commit `495bff3dba7bac77bcac01b290ea2d8cd7f2dd9d`. Ziel: Ein Gruppenkommandant erkennt ohne Einweisung **wohin, welcher Auftrag, welche Gefahr und was als Nächstes zu tun ist**. Dieses Konzept legt Darstellung und Bedienung fest; bestehende Statuswerte, Rechte, Disposition und GSL-Phasenautomatik bleiben Grundlage.

## 1. Gemeinsame Bedienregeln

- Die drei Hauptbereiche heißen überall **Einsätze**, **Karte**, **Meldungen**, jeweils mit Symbol und Text. Startbereich ist **Einsätze**. Die Navigation bleibt auch im Auftragsdetail erreichbar; ein zusätzlicher beschrifteter Zurück-Button führt zur vorherigen Ansicht. Browser-Zurück funktioniert ebenfalls.
- Smartphone und Tablet verwenden dieselben Begriffe, Reihenfolgen und Aktionen. Bis 760 px stehen die Bereiche unten; darüber als sichtbare Bereichsleiste oben. Ab 1.024 px darf der gewählte Bereich Liste und Detail beziehungsweise Karte nebeneinander zeigen. Eine Auswahl und ein Auftrag bleiben geräteübergreifend gleich bedienbar; Drehen ändert weder Auswahl noch Eingaben.
- Pro offenem, schreibbarem Auftrag gibt es **genau eine große, hervorgehobene Hauptaktion**, abhängig vom Status. Sekundäre Aktionen sind zurückhaltende, beschriftete ECP-Buttons. Ein Dialog zeigt ebenfalls genau eine Hauptaktion; seine Eingaben und Zurück bleiben bedienbar, die darunterliegende Ansicht ist inaktiv. Bei Lesezugang steht der Grund anstelle einer ausführbaren Statusaktion.
- Alle interaktiven Ziele dieser Ansicht sind mindestens **48 × 48 CSS-px**, Hauptaktionen mindestens 56 px hoch, Abstand zwischen Zielen mindestens 8 px. Das gilt auch für Zurück, Schließen, Aufklappen, Checkbox-Beschriftungen, Kartenmarker und Zoom. Ein kleineres Symbol darf innerhalb einer größeren Trefferfläche stehen. Keine Funktion setzt Hover, Wischen, langes Drücken oder Ziehen voraus; Karten bieten zusätzlich Zoom-Buttons und eine gleichwertige Liste.
- Art und Priorität werden getrennt dargestellt (präzisiert durch Auftrag 2.2): **Erkundung = orange (`--orange`), Lupe (`search`), Text „ERKUNDUNG“**; **Abarbeitung = blau (`--blue`), Werkzeug (`tools`), Text „AUFTRAG“**. Artchip und Artsymbol tragen die Artfarbe; Kartenrand, Markerring und ein eigener beschrifteter Prioritätschip zeigen die Dringlichkeit. Dies gilt auch für beendete Aufträge; Status ersetzt niemals die Auftragsart. Auch Kartenmarker erhalten eine sichtbare Artbeschriftung, keinen ausschließlich per Hover sichtbaren Text. Alle Darstellungen verwenden ausschließlich das bestehende Feld `auftragsart`; fehlende oder ungültige Werte erscheinen neutral als „ART UNBEKANNT“ mit Fragezeichensymbol, ohne eine Art aus Text, Phase oder Status abzuleiten.
- Unmittelbar sichtbar bleiben Auftrag, Adresse, Auftragsart, Status, Gefahren und Hauptaktion. **Weitere Informationen** öffnet per Tippen vorhandene Abschnitte für Objektinformationen, Sperrendetails, andere Einheiten, Fotos, Material und Chronik. Kritische Angaben werden niemals dort versteckt.

### Gefahren haben Vorrang

Eine gemeinsame **Gefahrenleiste** bleibt beim Scrollen im sichtbaren Kopfbereich von Einsätzen, Karte, Meldungen, Detail und Dialogen. Sie nennt Warnsymbol, „Gefahr“, kurzen Gefahrentext und Einsatzstelle; aktuelle und ausgewählte Stelle sind eindeutig zugeordnet. Weitere bekannte kritische Hinweise zu eigenen offenen Aufträgen werden ebenfalls mit Stelle genannt. Hinweise sind weder wegklickbar noch eingeklappt; mehrere Warnungen werden ausgeschrieben und lange Texte umbrechen. Ein bloßer roter Punkt oder „3 Gefahren“ genügt nicht. Die Leiste darf Inhalt und Bedienelemente nicht überdecken.

Es gibt bisher keine verlässliche Kritikalitätskennzeichnung in der Einheitenprojektion. Bis diese aus bestehenden Daten eindeutig ableitbar ist, werden **alle vorhandenen, nicht behobenen Gefahrenhinweise im bestehenden Relevanzbereich** sichtbar behandelt. Freitext wird nicht automatisch als geprüfte Gefahrenquelle klassifiziert. „Keine bekannten Gefahren“ erscheint nur nach erfolgreichem Laden; sonst „Gefahren nicht verfügbar“ beziehungsweise „Gefahren: letzter Stand HH:MM“. Bekannte Warnungen bleiben offline stehen. Akute Gefahren werden zusätzlich per Funk gemeldet; eine digitale Meldung löst keine automatische Alarmierung aus.

## 2. Ansichten

### 2.1 Einsatzübersicht – Bereich „Einsätze“

Oben stehen Einheit, Lage, Verbindungs-/Datenstand und „Noch nicht übermittelt: N“. Darunter: Gefahrenleiste, **Aktuell**, **Nächster**, **Weitere Einsätze**; **Beendet** und **Zurückgezogen** sind aufklappbar. „Beendet“ unterscheidet „Erkundet“, „Fertig gemeldet“ und „Nicht durchführbar“ ausdrücklich.

Eine Auftragskarte enthält Artchip, Einsatzstelle, vollständige Adresse, kurzen Auftragstext, Priorität, Status sowie gegebenenfalls **Neu** oder **Geändert**. Der aktuelle Auftrag zeigt seine Hauptaktion aus Abschnitt 3; andere Karten einen sekundären Button **Auftrag öffnen**. Ohne aktuellen Auftrag öffnet eine große Aktion **Auftrag öffnen** den nächsten. Ohne offene Aufträge steht „Keine offenen Aufträge“.

Es gibt höchstens einen aktuell bearbeiteten Auftrag, aber mehrere Zuweisungen und Bestätigungen. Öffnen oder Wechseln des Bereichs verändert keinen Status. Die Leitstellenreihenfolge bleibt verbindlich; keine eigene Umreihung durch Ziehen oder Entfernung. Ein neuer Auftrag verdrängt weder den geöffneten Auftrag noch einen Entwurf.

### 2.2 Auftragsdetail

Die feste Reihenfolge lautet: **Zurück und Einsatzstelle → Gefahren → Art, Adresse, Priorität, Auftrag und Status → Hauptaktion → Schnellmeldungen → Weitere Informationen**. Auftragsart, Gefahr und Status bleiben auch bei einer kleinen Zielkarte erkennbar. **Navigation starten** ist eine sekundäre Aktion; ohne Koordinaten wird die Adresse verwendet, ohne Ziel kein Navigationsbutton. Keine Karte am Nullpunkt.

Auf dem Tablet darf der obere Arbeitsbereich neben den Zusatzinformationen stehen; die Lesereihenfolge bleibt gleich. Im normalen Startausschnitt liegen Hauptaktion und Gefahren vor Galerie und Chronik. **Auftrag geändert – bitte prüfen** zeigt eine neue Führungsänderung; offene Eingaben bleiben erhalten. Beendete oder zurückgezogene Aufträge haben keine ausführbare Status-Hauptaktion; erlaubte Nachmeldungen, Fotos und Materialvorgänge bleiben sekundär erreichbar.

### 2.3 Karte

Dieselben eigenen Aufträge und dieselbe Auswahl wie unter Einsätze. Artfarbe, Symbol und ausgeschriebene Bezeichnung unterscheiden Erkundung und Abarbeitungsauftrag; „Aktuell“, „Nächster“ und Rang stehen zusätzlich dabei. Tippen auf Marker oder Listeneintrag öffnet dieselbe beschriftete Vorschau mit **Auftrag öffnen** und **Navigation starten**. Kein automatischer Statuswechsel.

Sichtbare Buttons: **Mein Standort**, **Zentrieren**, **Fertige anzeigen**, Zoom sowie die Auftragsliste. Standort erst nach ausdrücklichem Tippen. Fehlende Positionen werden als Liste mit Adresse angeboten. Verbindungen heißen **Luftlinie**, externe Navigation trägt den bestehenden Sperrenhinweis. Kartenausfall blockiert Auftrag und Meldung nicht.

### 2.4 Erkundungsabschluss

**Erkundung abschließen** öffnet einen Dialog mit Einsatzstelle, Artchip und Gefahren. Er enthält **Lage vor Ort**, **Notwendige Maßnahmen**, **Foto hinzufügen**; alle Angaben bleiben wie im Bestand optional. Die einzige Hauptaktion heißt auch hier **Erkundung abschließen**, daneben **Zurück**. Kein Pflichtfoto und kein künstlicher Status „In Arbeit“ für Erkundungen.

Erst nachdem alle erfassten Texte und gewählten Fotos sicher lokal gespeichert sind, wird der Abschluss eingereiht. Bei Speicherfehler bleiben Dialog, Text und Dateien erhalten. Bericht, Foto und Abschluss bleiben getrennte Übermittlungen: „Erkundet · 1 Foto noch nicht übermittelt“ ist zulässig, eine pauschale Erfolgsmeldung trotz offener Teile nicht. Abbrechen oder Bereichswechsel bewahrt den Entwurf. Die Einsatzstelle wird dadurch nicht endgültig geschlossen.

### 2.5 Schnellmeldungen

Im Detail direkt sichtbare sekundäre Buttons: **Lagemeldung**, **Foto**, **Gefahr melden**. Unter **Weitere Meldungen** stehen **Maßnahme** und **Notiz**. Im Bereich Meldungen führt **Meldung schreiben** in denselben Ablauf; ohne ausgewählten Auftrag muss zuerst ein berechtigter Auftrag gewählt werden.

Jeder Dialog zeigt Auftrag und Einsatzstelle. „Lage unverändert“ ist eine optionale Textvorlage im Lagemeldungsdialog und wird vor dem Senden angezeigt. **Gefahr melden** nutzt die bestehende Lagemeldung mit erkennbarem Gefahrentext, ohne neuen Meldungstyp oder Alarmierungsversprechen.

Fotos erlauben Kamera oder vorhandenes Bild, Vorschau, Entfernen und optionalen Kommentar. Die Hauptaktion lautet **Meldung senden** beziehungsweise **Foto senden**. „Senden“ speichert zuerst lokal: offline folgt „Auf diesem Gerät gespeichert – noch nicht übermittelt“, nach Serverbestätigung „Übermittelt“. Fehlende Verbindung oder ein Schreibfehler leert niemals Eingaben.

### 2.6 Meldungsverlauf – Bereich „Meldungen“

Oben: **Meldung schreiben**, Filter **Alle Einsatzstellen** / konkrete Einsatzstelle und der gewählte Kontext. Darunter **Noch nicht übermittelt** mit Anzahl und **Verlauf** mit neuesten Einträgen zuerst. Detail-Link **Meldungsverlauf** öffnet denselben Bereich mit passender Stelle. Meldungen, Statusereignisse und Fotos sind anhand beschrifteter Typen unterscheidbar.

Jeder Verlaufseintrag nennt Datum/Uhrzeit, Einsatzstelle, Art des Eintrags, Text beziehungsweise Fotovorschau und verfügbaren Absender. Geräte-Erfassungszeit und späterer Eingang werden bei Offline-Nachlieferung unterschieden. Lokale Einträge tragen einen klaren Übermittlungszustand; **Auftrag bestätigt** bezeichnet ausschließlich die fachliche Auftragsbestätigung. „Übermittelt“ bedeutet keine Lesebestätigung der Einsatzleitung.

Die bestehende Chronik ist **stellenbezogen**, nicht eindeutig dispatchbezogen. Bei Erkundung und Abarbeitung an derselben Stelle heißt sie daher „Meldungen zur Einsatzstelle“; Einträge werden nicht doppelt oder einer erfundenen Auftragsart zugeordnet. Die Sicht hält die bestehenden Rechte ein; „Alle Einsatzstellen“ erweitert keine Berechtigung. Begrenzte Historie wird mit „Letzte verfügbare Meldungen“ bezeichnet, nicht als vollständiges Archiv.

## 3. Statusaktionen und verbindliche Begriffe

Die Tabelle definiert die **geführte Standardfolge im UI**, keine neuen serverseitigen Pflichtschritte. Unter **Weitere Status** bleiben vom Backend erlaubte direkte Sprünge, Rücksetzungen und **Nicht durchführbar** erreichbar, etwa „Vor Ort melden“, wenn die Einheit bereits dort ist. Ein Grund ist bei „Nicht durchführbar“ Pflicht. „Auftrag bestätigen“ bestätigt den Empfang, beginnt keine Anfahrt und aktiviert keinen anderen Auftrag.

| Bestehender Status | Sichtbares Statuswort | Hauptaktion Erkundung | Hauptaktion Abarbeitungsauftrag |
|---|---|---|---|
| `zugewiesen` | Zugewiesen | Auftrag bestätigen → `bestaetigt` | Auftrag bestätigen → `bestaetigt` |
| `bestaetigt` | Bestätigt | Anfahrt melden → `anfahrt` | Anfahrt melden → `anfahrt` |
| `anfahrt` | Anfahrt | Vor Ort melden → `vor_ort` | Vor Ort melden → `vor_ort` |
| `vor_ort` | Vor Ort | Erkundung abschließen → `abgeschlossen` | Arbeit beginnen → `in_arbeit` |
| `in_arbeit` | In Arbeit | Kein gültiger Erkundungszustand; Aktualisierung anbieten | Fertig melden → `abgeschlossen` |
| `abgeschlossen` | Erkundet / Fertig gemeldet, je Art | Keine Statusaktion | Keine Statusaktion |
| `nicht_durchfuehrbar` / zurückgezogen | Nicht durchführbar / Zurückgezogen | Keine Statusaktion | Keine Statusaktion |

Entscheidend bleiben die serverseitig erlaubten Übergänge und Zugangsrechte. Bei Phasensperre steht der verständliche Grund direkt an der gesperrten Aktion, etwa „Abarbeitung erst nach Erkundung möglich“. Bei unbekanntem oder ungeprüftem Zustand werden keine zulässigen Status erfunden. Offline bekannte Schritte können lokal eingereiht werden; Serverstand und lokaler Status bleiben unterscheidbar. Derselbe noch ausstehende Schritt kann nicht mehrfach ausgelöst werden.

Läuft ein anderer Auftrag, zeigt ein Startversuch **Auftrag wechseln?** mit beiden Einsatzstellen; nur **Unterbrechen und wechseln** bewirkt den bestehenden Wechsel. Bestätigung allein benötigt keinen Wechsel. **Fertig melden** öffnet eine kurze Bestätigung: „Auftrag fertig melden? Die Einsatzleitung schließt die Einsatzstelle ab.“ Zurückziehen und endgültiges Schließen bleiben Führungsentscheidungen.

Übermittlungsbegriffe in allen Ansichten: **Noch nicht übermittelt**, **Übermittelt**, **Fehler**, **Konflikt**, **Zugang beendet**. Technische Begriffe wie Outbox, Dispatch, UUID oder HTTP-Code erscheinen nicht im Bedienablauf. Bei Netzfehler gibt es **Erneut senden**, bei fachlichem Konflikt eine Erklärung mit **Auftrag prüfen**, bei Bedarf **Text kopieren** und bestätigungspflichtigem **Verwerfen**. Ein neuer fachlicher Versuch ist eine bewusste neue Aktion; Netz-Retry bleibt idempotent. Bei beendetem Zugang stoppt Versand, lokale Inhalte bleiben zur Sicherung erhalten.

## 4. Komponentenstruktur und Wiederverwendung

```text
Einheitenansicht
├─ Kopf: Einheit, Lage, Datenstand, offene Übermittlungen
├─ Bereichsleiste: Einsätze | Karte | Meldungen
├─ Gefahrenleiste: in jeder Ansicht und im Dialog sichtbar
├─ Einsätze: Auftragskarten → ausgewähltes Auftragsdetail
│  ├─ Artchip + Auftrag + Status + eine Hauptaktion
│  ├─ Schnellmeldungen
│  └─ Weitere Informationen (bestehende Detailabschnitte)
├─ Karte: gleiche Auswahl + Vorschau + gleichwertige Liste
├─ Meldungen: Filter + lokale Einträge + Stellenchronik
└─ Dialog: Kontext/Gefahren + Eingabe + eine Hauptaktion + Zurück
```

| Vorhandener Baustein | Verwendung |
|---|---|
| [`_fab_styles.html`](../app/templates/_fab_styles.html): `fab-header`, `fab-card`, `fab-subcard`, `fab-grid` | Kopf, Auftragskarten, Informationsstufen und breites Layout. |
| [`_einheit_styles.html`](../app/templates/einheit/_einheit_styles.html): Chips, Tabbar, Aktionen, `einheit-warning--alarm` | Art-/Statusanzeige, Bereichsleiste, Gefahren und Übermittlungszustand; Größen/Farben einheitenspezifisch ergänzen. |
| [`tailwind.input.css`](../app/static/css/tailwind.input.css): `btn`, `btn--primary`, `btn--ghost`, `form-control`, Designtokens | Vorhandene Button-/Formensprache, Hauptbutton bleibt ECP-Primärbutton; Artfarbe ist separat. |
| [`_person_select_styles.html`](../app/templates/components/_person_select_styles.html): `person-flyout` | Bestehende Dialoghülle, mit sichtbarem Kontext, mindestens 48-px-Schließen, Fokusführung und Rückkehr zum Auslöser. |
| [`einheit.html`](../app/templates/einheit/einheit.html), `icon()` / vorhandene Tabler-Symbole | Bestehende Teilansichten/`details` nutzen; keine neue Icon- oder Komponentenbibliothek. |
| [`einheit_modus.js`](../app/static/js/einheit_modus.js), [`einheit_karte.js`](../app/static/js/einheit_karte.js), [`einheit_outbox.js`](../app/static/js/einheit_outbox.js) | Gemeinsame Auswahl, Statusableitung, Karte, Entwürfe und Versand weiterverwenden. |

Für die Umsetzung sind kleine additive Leseprojektionen nötig: Gefahrenkurzinfos für die Übersicht, zulässige Status/Rückzug im Offline-Zustand sowie Chronik-IDs und Erfassungszeiten für einen verlässlichen Verlauf. Bestehende Stellenlogs und lokale Übermittlungen bleiben die Datenquellen; kein neues Chat-/Auftragsmodell. Keine neue Datenbankmigration für dieses Konzept. Änderungen an geteilten Styles werden auf die Einheitenansicht begrenzt.

## 5. Akzeptanzkriterien für die Umsetzung

| ID | Prüfbares Kriterium |
|---|---|
| AK01 | Auf Smartphone und Tablet sind genau Einsätze, Karte und Meldungen erreichbar. Wechsel/Zurück/Drehen erhält Auswahl und Entwurf und schreibt keinen Status. |
| AK02 | Jede Auftragskarte, jeder Kartenmarker und jeder Detail-/Dialogkopf zeigen ERKUNDUNG mit orangefarbener Lupe beziehungsweise AUFTRAG mit blauem Werkzeug. Fehlende/ungültige Art erscheint neutral als ART UNBEKANNT. Priorität bleibt mit eigenem Rand/Ring und Klartext für beide Arten erkennbar; Status ist separat. Stellenchronik und Stellenfotos erhalten keine erfundene Auftragsart; eindeutig verknüpfte Übermittlungen verwenden die Art des berechtigten Auftrags. |
| AK03 | Genau eine Hauptaktion entspricht der Statustabelle und den Serverrechten. Zwei zugewiesene Aufträge können bestätigt werden, ohne den laufenden zu ersetzen. Start eines zweiten aktiven Auftrags verlangt die ausdrückliche Wechselentscheidung. |
| AK04 | Eine bekannte Gefahr ist beim Scrollen, Bereichswechsel und in jedem Dialog ohne weiteren Klick als Text mit Stelle sichtbar. Sie ist nicht schließbar. Offline-/Ladefehler zeigt Datenstand beziehungsweise fehlende Daten statt fälschlich Entwarnung. |
| AK05 | Bei 360/390 px Smartphonebreite, 768 px Tablet-Hochformat und 1.024 px Querformat messen alle interaktiven Ziele mindestens 48 × 48 CSS-px. Alle Aktionen sind per Tippen und Tastatur erreichbar; kein Hover-/Gestenzwang. |
| AK06 | Im Prüffall 390 × 844 px mit kurzem Auftrag und einer Gefahr stehen Gefahr, Art, Adresse und Hauptaktion vor der Galerie/Chronik im ersten sichtbaren Arbeitsbereich. Lange Texte, mehrere Gefahren und 200 % Textvergrößerung bleiben vollständig erreichbar; kein horizontaler Seitenscroll und keine Überdeckung. |
| AK07 | Öffnen, neuer Auftrag oder Standortvorschlag verändert weder Leitstellenreihenfolge noch aktuellen Auftrag automatisch. „Neu“/„Geändert“ funktionieren auch beim ersten und nächsten Auftrag und vergleichen die richtige Auftrags-ID. |
| AK08 | Erkundungsabschluss akzeptiert optionale Angaben ohne Pflichtfoto, setzt niemals `in_arbeit` und schließt die Stelle nicht endgültig. Simulierter lokaler Speicherfehler erhält Text/Dateien und verhindert das Einreihen des Abschlusses. Offene Fotos bleiben nach Statusbestätigung sichtbar ausstehend. |
| AK09 | Schnellmeldungen benennen die Zielstelle vor dem Senden. Eine ausschließlich strukturierte Meldung kommt vollständig an. Ein Foto kann aus Kamera oder Datei gewählt werden. Ein erfolgloses Speichern leert keine Eingabe. |
| AK10 | Meldungen unterscheiden lokale Einträge von serverseitigem Verlauf und zeigen Stelle, Datum/Zeit und verfügbaren Absender. Erkundung/Abarbeitung derselben Stelle erzeugen keine doppelte Chronik. QR/GK-Link sehen keine fremden Einheiteninhalte; eine begrenzte Historie ist gekennzeichnet. |
| AK11 | Im Ablauf Online → Funkloch → Reload → Synchronisieren bleiben Entwürfe und offene Einträge erhalten. „Übermittelt“ erscheint erst nach Serverbestätigung. Wiederholtes Senden erzeugt keinen Doppeleintrag; Konflikt und Zugangsende werden verständlich behandelt. |
| AK12 | Polling bei ausgefallenem WebSocket und Listen-304 aktualisiert geöffnete Details einschließlich Gefahr und Status. Geänderte Aufträge überschreiben keine Eingabe; verspätete Antworten zeigen keinen zuvor geöffneten Auftrag an. |
| AK13 | QR bietet keine Personal-/Ausstattungspflege an; GK nur bei Freigabe. Geräte-Gesamtansicht und Echtlagen-Simulation bleiben lesend. Bestehende Backend-Rechte gelten ebenso für direkte Medienaufrufe. |
| AK14 | Drei Personen ohne ECP-Einweisung finden im Prototyp selbstständig den nächsten Auftrag, benennen Art und Gefahr, bestätigen den Auftrag, erfassen eine Lagemeldung und erkennen eine noch nicht übermittelte Meldung. Fehler werden vor UI-Abnahme dokumentiert und behoben. |

Prüfung bei Umsetzung: vorhandene Service-/API-/Outbox-Tests aus Auftrag 1.1 erhalten; um Komponenten-Verhaltenstests und Browserabläufe für diese Kriterien ergänzen. Eine vollständige Abnahme benötigt zusätzlich Safari und echte Android-Tablets. **In Auftrag 1.2 wird nur dieses Konzept erstellt; die Akzeptanzkriterien sind noch nicht als bestandene UI-Tests zu verstehen.** Vor späterer Freigabe sind insbesondere die Bestandsbefunde F1–F9 und F12/F13 zu Initialisierung, Datenverlust, Datenstand und Zugriffsgrenzen zu beheben.
