# Förderstrecken-Planer – Anwendung

← [Zurück zur Startseite](Home)

> URL: `/foerderstrecke/`
> Zugänglich für: `recorder` und höher (Planung), `org_admin` (Geräte/Kalibrierung)

Der Förderstrecken-Planer berechnet Löschwasserförderung über lange Wegstrecken:
maximale Fördermenge, Pumpenstandorte, Druckprofil mit Hochpunkt-Prüfung,
Maschinisten-Sollwerte und Materialbedarf.

---

## 1. Überblick

Jede Förderstrecke besteht aus **Ansaugpunkt → Pumpen/Relais → Auslass**. Der Planer
rechnet die Hydraulik segmentweise (25-m-Auflösung) und prüft dabei u. a.:

- Eingangsdruck jeder Folgepumpe ≥ 1,5 bar
- Drucklinie ≥ 0,5 bar an Hochpunkten (Strömungsabriss-Warnung)
- Saughöhen-Bilanz inkl. Seehöhen-Korrektur (harte Grenze 7,5 m)
- Betriebsdruck ≤ Grenze des Schlauchtyps
- Engpass durch die schwächste Pumpe im Strang

---

## 2. Eine Strecke planen

1. **Bezeichnung** eingeben (z. B. „Versorgung Objekt X aus der Ach").
2. **Ansaugpunkt**: Seehöhe, geodätische Saughöhe (Pumpe über Wasserspiegel),
   Saug-k-Wert und Anzahl paralleler Saugleitungen.
3. **Stationen** hinzufügen. Je Station:
   - **Typ**: Quellpumpe · Verstärkerpumpe (geschlossene Reihe) · Pufferstation
     (offene Reihe, mit Behältervolumen) · Übergabe-/Verteilstation.
   - **Pumpe** aus dem Katalog + Drehzahlstufe.
   - **Leitung danach**: Schlauchtyp, Länge, Anzahl paralleler Leitungen, Höhendifferenz.
   - Die letzte Station hat keinen Abschnitt (Auslass).
4. **Route zeichnen** (optional): mit dem Linien-Werkzeug auf der Karte. Gesamtlänge und
   Höhenprofil werden übernommen und gleichmäßig auf die Abschnitte verteilt (danach
   je Abschnitt anpassbar).
5. **Berechnen** klicken.

---

## 3. Ergebnis lesen

- **Fördermenge (Q)** groß mit Ampel: grün = ok, gelb = Warnungen, rot = nicht machbar
  bzw. Abriss-/Hochpunkt-Gefahr.
- **Höhenprofil + Drucklinie**: Gelände (grau), Druck p(s) (blau), Grenzlinien 1,5 bar
  und max. Betriebsdruck; rote Punkte markieren Hochpunkte mit Abrissgefahr.
- **Stationstabelle**: Ausgangsdruck-Soll, Eingangsdruck Folgepumpe, **DBV** (= Eingangsdruck
  Folgepumpe + 0,5 bar) — die Werte für die Maschinisten.
- **Material**: Schlauchmeter (inkl. Reserve) und Stückzahl, Leitungsvolumen, Füllzeit.
- **Warnungen** im Klartext (z. B. „Kuppe bei km 1,2: Druck 0,3 bar").

---

## 4. Speichern, PDF, Maschinisten-Zettel

- **Speichern** legt die Strecke (Entwurf) ab. Gespeicherte Strecken erscheinen in der Liste.
- **PDF** („Einsatzplan Wasserförderung"): Kartenausschnitt, Höhenprofil, Stationstabelle
  mit Soll-Drücken und Funkrufname-Freifeld, Materialliste, Füllzeit.
- **🔗 Zettel** erzeugt einen **login-freien Link** (Maschinisten-Zettel): jede Station mit
  Soll-Drücken, DBV, Standort-Link und Nachbarstationen — ideal per QR an die Pumpen verteilt.
  Ein neuer Link widerruft den alten.

---

## 5. Modus B (Bedarf)

Ziel-Fördermenge vorgeben → der Planer schlägt Pumpenanzahl/-standorte vor und warnt,
wenn der eigene Gerätepark nicht reicht. *(In Vorbereitung — aktuell steht Modus A
„was schaffen wir?" zur Verfügung.)*

---

## 6. Mit einem Einsatz verbinden

Über **Mit Einsatz verbinden** (linkes Panel) lässt sich die Strecke einem laufenden
Einsatz zuordnen. Damit:

- erscheint der Einsatzort als 📍-Marker auf der Karte des Planers,
- erscheint die Förderstrecke in der Lageführung des Einsatzes als Auftrag
  (Sidebar-Tab „Layer") mit Direktlink, PDF und Status,
- erscheint die geplante **Route inkl. Pumpenstandorte** dort zusätzlich als eigener,
  ein-/ausblendbarer Kartenlayer **🚰 Förderstrecke** — sichtbar für alle Nutzer der
  [Lageführung](Anwender-Lagefuehrung), inklusive WYSIWYG-Kartendruck.

Die Verknüpfung lässt sich jederzeit im Dropdown ändern oder auf „— kein —" zurücksetzen.

---

## 7. Karte vergrößern

Der Button **⤢ Karte vergrößern** neben den Kartenwerkzeugen schaltet die Karte auf ein
bildschirmfüllendes Overlay um (z. B. für präzises Zeichnen am Tablet) — alle Werkzeuge
(Routing, Zeichnen, Pumpe setzen) bleiben dabei bedienbar. **⤡ Verkleinern** oder `Esc`
kehrt zur normalen Ansicht zurück.

Siehe auch: [Administration](Administration-Foerderstrecken-Planer) für Geräte-Pflege und Kalibrierung.
