# Objekte (Objektverwaltung)

Die Objektverwaltung hält Einsatzunterlagen zu wichtigen Objekten bereit — Betriebe mit
Brandmeldeanlage, Wohnanlagen, öffentliche Gebäude, landwirtschaftliche Objekte. Alles, was
die Mannschaft bei BMA-Alarm oder Brandeinsatz sofort braucht: Gefahren, Schlüsselsafe-Standort,
Melderpläne/Laufkarten, Ansprechpartner, Anfahrt.

> Das Modul erscheint nur, wenn es für deine Organisation aktiviert ist
> (siehe [Administration → Objektverwaltung](Administration-Objektverwaltung)).
> Einstieg: **Dokumentation → 🏢 Objekte** in der Hauptnavigation.

## Objektliste

`/objekte` zeigt alle Objekte deiner Organisation:

- **Suche** über Name, Vulgoname, Adresse oder Nummer (`OBJ-0042`)
- **Filter**: Kategorie, Status, Merkmal, „Revision fällig"
- **Ampel-Badges** je Objekt: `BMA` (gelb), `FSD` (grün, Schlüsselsafe), `🔑` (Schlüsselbox),
  `BSP` (rot, Brandschutzplan vorhanden), `⚠ n` (Anzahl erfasster Gefahren)
- **Vollständigkeits-Balken**: wie komplett sind die Kerndaten gepflegt (Tooltip zeigt, was fehlt)
- **Mappe drucken**: mehrere Objekte per Checkbox auswählen → ein Sammel-PDF für die Fahrzeugmappe

Entwürfe sehen nur Objektverwalter und Org-Admins; freigegebene Objekte sieht jeder angemeldete
Benutzer der Organisation.

## Objekt pflegen (Desktop)

Die Detailseite ist in Abschnitte gegliedert (linke Navigation), jeder Abschnitt wird einzeln
mit dem Stift-Symbol bearbeitet und gespeichert — kein Riesenformular:

| Abschnitt | Inhalt |
|-----------|--------|
| **Stammdaten** | Name, Vulgoname, Kategorie, Adresse, Koordinaten (automatisches Geocoding), Informationen, Anfahrtsweg, Revisionsdatum |
| **Gefahren** | Strukturierte Einträge aus dem Gefahren-Katalog (EX, Gas, Chemie mit UN-Nr., Hochspannung, PV, NH3, …) mit Freitext-Detail — erscheinen als Chips in Einsatzansicht, Infoscreen und Druck |
| **BMA & Schlüssel** | BMA-/RFL-Nr., BMZ- und FBF-Standort, Laufkarten-Ablageort, Schlüsselsafe/FSD (Standort + Inhalt), Betreiber-Benachrichtigung |
| **Merkmale** | Katalog-Merkmale mit Hinweis (Schlüsselbox „beim Nebeneingang", Tiefgarage, Drehleiterstellplatz, Sprinkler, …) |
| **Kontakte** | Brandschutzbeauftragte, Betreiber, Hausverwaltung, Schlüsselträger — mehrere Telefonnummern je Kontakt, im Einsatz als Klick-zum-Anrufen-Buttons |
| **Wohnanlage** | Wohneinheiten, Geschosse, Stiegen, Hausverwaltung; Hinweise-Feld mit DSGVO-Leitplanke (nur einsatztaktisch, sparsam, sachlich) |
| **Zugänge / Stiegen** | Zusatzadressen — wichtig bei Wohnanlagen, jede Stiege hat oft eine eigene Adresse (fließt ins Alarm-Matching ein!) |
| **Lagekarte** | siehe unten |
| **Dokumente** | siehe unten |
| **Einsätze** | Historie aller mit dem Objekt verknüpften Einsätze („schon wieder Fehlalarm im Farbenlager?") |
| **Protokoll** | Feldgenaues Änderungsprotokoll: wer hat wann welches Feld von → auf geändert |

**Status-Workflow:** Entwurf → Freigegeben ⇄ In Überarbeitung → Archiviert. Nur freigegebene
Objekte erscheinen im Alarm-Matching, im Offline-Sync und für normale Benutzer. Das
**Revisionsdatum** erinnert automatisch (Benachrichtigung + Filter „Revision fällig"), wenn ein
Objekt nachgefasst werden soll.

## Dokumente: PDF hochladen und klassifizieren

Unterlagen kommen fast immer als Sammel-PDF (150+ Seiten Brandschutzpläne und Melderpläne).
Der Ablauf:

1. **Hochladen** per Drag & Drop (mehrere PDFs, je max. 100 MB / 300 Seiten)
2. Das System **zerlegt jede Seite automatisch** in ein durchsuchbares Einzelblatt mit Vorschaubild
3. **Klassifizieren** in der Galerie: Seiten per Checkbox mehrfach auswählen → Bulk-Zuweisung von
   *Dokumentart* (BMA Datenblatt, BMA Melderplan, Brandschutzplan, Gefahrgutdatenblatt, Lageplan,
   Objektinformation), *Titel* („Melderplan EG Nord"), *Melderlinie(n)*, *Stand-Datum* und dem Flag
   **„Bei Einsatz drucken"**
4. **Nutzen**: Filter-Chips mit Zählern („Alle (150) / Brandschutzplan (23) / BMA Melderplan (121)"),
   Suche über Titel/Melderlinie, Fullscreen-**Viewer** mit Wischen/Pfeiltasten und Zoom (Tablet!),
   Einzelseiten-Download oder zusammengestelltes Sammel-PDF

Unklassifizierte Seiten sind gelb umrandet. Optional schlägt die
[KI-Klassifizierung](Administration-Objektverwaltung#ki-dokumentklassifizierung) Dokumentart, Titel
und Melderlinien vor — Vorschläge müssen immer manuell bestätigt werden.

## Objekt-Lagekarte

Über „Karte bearbeiten" öffnet sich der Symbol-Editor: Symbol in der Palette anklicken, dann auf
die Karte klicken. Verfügbar sind u. a. FSD/Schlüsselsafe, Schlüsselbox, BMZ, FBF, Brandschutzplan-Ablage,
Drehleiter-Stellplatz, Objektfunk, Sammelplatz, Haupt-/Nebenzugang, Stiege, Aufzug,
Gefahren-Dreiecke (EX/Gas/Chemie/Strom/PV) und Hydranten. Marker sind verschiebbar; Linien und
Flächen zeichnest du mit den Werkzeugen links oben. Die Karte erscheint read-only in der
Einsatzansicht, am Alarm-Infoscreen und (vereinfacht) am Objektblatt.

## Im Einsatz

**Automatische Verknüpfung:** Bei jeder Einsatzanlage (Alarm-API, LIS/Leitstelle, manuell) sucht
das System das passende Objekt — zuerst über die **BMA-Nummer im Alarmtext** („bmz 1044"), dann
über die **Adresse** (inklusive Stiegen-Zusatzadressen), zuletzt über **geografische Nähe** (nur
als Vorschlag). Am Einsatz-Board erscheint das **Objekt-Panel** in der Sidebar: Vorschläge
bestätigen oder lösen dürfen Einsatzleiter und Objektverwalter; manuell verknüpfen geht dort auch.

**Einsatzansicht** (`Objektinfo`-Button am Board, für Tablet/Handy im Fahrzeug optimiert),
Priorität von oben nach unten:

1. **Gefahren-Chips** mit Piktogramm und UN-Nummer
2. **Gelber BMA/FSD-Block**: BMZ, FBF, Schlüsselsafe-Standort + Inhalt, Laufkarten-Ablageort
3. **Melderpläne/Laufkarten** — ein Tipp öffnet den Viewer, dort nach Melderlinie suchen
4. **Kontakte** mit großen Anruf-Buttons
5. Lagekarte, Dokumente, Anfahrtsweg, letzte Einsätze

Die Einsatzansicht wird von der PWA offline vorgehalten (zuletzt besuchte Objekte); die
**Android-App** precacht zusätzlich alle freigegebenen Objekte **inklusive PDFs** (Sync alle
6 Stunden) — Objektinfo funktioniert damit auch im Funkloch.

**Objektblatt drucken:** Aus Detail, Liste oder Einsatzansicht als A4-PDF — mit Gefahren-Piktogrammen,
BMA/FSD-Kasten, Kontakten, Karte und QR-Code zur Einsatzansicht. Optional hängt das System alle
Seiten mit „Bei Einsatz drucken"-Flag an (Laufkartenmappe fürs Fahrzeug).

## Alarm-Infoscreen

Für den Wandmonitor im Gerätehaus gibt es eine eigene Vollbild-Alarmansicht — Einrichtung siehe
[Administration → Objektverwaltung](Administration-Objektverwaltung#alarm-infoscreen).
