# Nachschlagewerke

← [Zurück zur Startseite](Home)

> URL: `/nachschlagewerke`
> Zugänglich für: alle angemeldeten Nutzer der Organisation

Das **Nachschlagewerk** liefert am Einsatzort schnelle Fachinfos — **offline verfügbar**, sobald die Seite einmal geladen wurde:

- **Gefahrgut** — UN-Nummer oder Stoffname nachschlagen
- **Rettungsdatenblätter** — Rettungskarten für die technische Rettung
- **Karten-Overlays** — Evakuierungsradius und Ausbreitung direkt in der Lagekarte

Aufruf über **📖 Dokumentation → 📚 Nachschlagewerke** (Desktop und mobil).

---

## Gefahrgut suchen

1. **Nachschlagewerke → Gefahrgut** öffnen
2. Ins Suchfeld tippen:
   - **UN-Nummer** (z. B. `1203`) — auch Teileingaben wie `12` zeigen passende Treffer
   - **Stoffname** (z. B. `Benzin`, `Chlor`) — Groß-/Kleinschreibung und Umlaute egal (`heizoel` findet „Heizöl")
3. Treffer erscheinen **sofort während des Tippens**. Ein Klick auf einen Treffer klappt die Details auf:
   - Stoffname, Gefahrklasse, Klassifizierungscode, **Gefahrnummer (Kemler)**, Verpackungsgruppe
   - **ERICard** (einsatztaktische Interventionskarte mit Sofortmaßnahmen) und **BAM Gefahrgut** als weiterführende Links

> Die Suche funktioniert **auch ohne Internet**, sobald die Gefahrgut-Seite einmal online geladen wurde (der Datensatz wird im Gerät zwischengespeichert). Die ERICard-Links selbst brauchen dagegen Netz.

**Badges verstehen:**
| Badge | Bedeutung |
|-------|-----------|
| `UN 1203` | UN-Nummer (4-stellig) |
| `GN 33` | Gefahrnummer / Kemlerzahl |
| `Kl. 3` | ADR-Gefahrklasse |
| `VG II` | Verpackungsgruppe |

---

## Rettungsdatenblatt (Fahrzeug) finden

1. **Nachschlagewerke → Rettungsdatenblätter** öffnen
2. Ins Suchfeld **Hersteller und/oder Modell** tippen (z. B. „VW Golf", „Tesla", „Audi e-tron").
   Treffer erscheinen **sofort** aus dem Katalog von **Euro Rescue** (Euro NCAP/CTIF, >2000 Fahrzeuge).
   Die Suche funktioniert auch **ohne Netz**, sobald die Seite einmal geladen war.
3. Beim passenden Fahrzeug auf **„📄 Öffnen"** — die Karte (bevorzugt **deutsch**) wird geladen,
   gespeichert und geöffnet. Ab jetzt ist sie **offline** verfügbar und erscheint unter
   **„Bereits gespeichert (offline verfügbar)"**.

> **Modell nicht dabei?** Unten **„Modell nicht im Katalog? Direktabruf & externe Quellen"**
> aufklappen: dort Hersteller/Modell/Baujahr eingeben und (falls konfiguriert) direkt laden oder
> **Links** auf offizielle Quellen (Euro Rescue, ADAC …) folgen.

> Ob Karten automatisch geladen werden, hängt von der Konfiguration eurer Organisation ab (siehe [Administration](Administration-Nachschlagewerke)). Ohne hinterlegte Quelle stehen nur die Deep-Links bereit.

---

## Karten-Overlays (in der Lageführung)

In der **[Lageführungskarte](Anwender-Lagefuehrung)** eines Einsatzes stehen bei aktivem Modul zwei zusätzliche Werkzeuge unter **„Weitere Werkzeuge"** bereit.

### ☣️ Evakuierungsradius

Zeichnet konzentrische Gefahrenzonen um einen Punkt (Richtwerte nach **ERG 2020**):

1. Werkzeug aufklappen, ein **Preset** wählen:
   - **Klein (50 m)** — kleine Freisetzung, Sofort-Sperrbereich
   - **Groß (100 m)** — große Freisetzung
   - **Tank/Brand (100/800 m)** — Sperrbereich rot + Evakuierungs-/Warnbereich orange
   - oder einen **eigenen Radius** in Metern eingeben
2. Auf die **Karte klicken** → die Zonen werden gesetzt

Jede Zone ist ein eigener Kreis — sie synchronisieren sich live zu allen Geräten, erscheinen in der Chronologie und im **Kartendruck/PDF-Lagebericht**.

### 🌬 Ausbreitung (Wind)

Zeichnet die windbezogene Ausbreitungsfläche (der Wind „bläst" die Fahne vom Quellpunkt weg — Richtung = Windrichtung + 180°). Die aktuelle Windrichtung kommt automatisch aus den Wetterdaten des Einsatzorts.

1. Werkzeug aufklappen, **Modell** wählen:
   - **Kegel (Richtwert)** — einfacher Ausbreitungskegel, nur **Reichweite** in Metern nötig
   - **Gauß (Konzentration)** — physikalisches Ausbreitungsmodell mit **Freisetzung** (g/s), **Stabilitätsklasse** (A–F) und **Grenzwert** (mg/m³)
2. Auf den **Quellpunkt** in der Karte klicken → die Fläche wird gezeichnet

> Sind keine Winddaten verfügbar, wird mit **Richtung Nord** gezeichnet (Hinweis im Label). Die Werte sind **Richtwerte zur Lageorientierung**, kein Ersatz für Messungen vor Ort.

---

## Offline nutzen

- Die **Gefahrgut-Suche** und die **Rettungskarten-Modellsuche** funktionieren ohne Netz, sobald die Seite einmal geladen wurde.
- **Bereits geöffnete Rettungskarten** bleiben offline abrufbar.
- Die **Karten-Overlays** werden im Einsatz gespeichert und stehen wie alle Lagekarten-Elemente offline zur Verfügung.
- **Kartenkacheln** (Hintergrundkarte) benötigen weiterhin Internet.

---

## Verwandte Seiten

- [Nachschlagewerke (Administration)](Administration-Nachschlagewerke) — Aktivierung, Datenquellen-URLs
- [Lageführung](Anwender-Lagefuehrung) — die Lagekarte mit den Overlays
- [Objekte](Anwender-Objekte) — Objekt-Gefahren mit derselben Gefahrgut-Anreicherung
