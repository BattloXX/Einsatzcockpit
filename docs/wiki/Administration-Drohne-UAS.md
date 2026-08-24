# Drohne / UAS – Administration

← [Zurück zur Startseite](Home)

> URL: `/uas/`  
> Zugänglich für: `org_admin`, `admin`, `system_admin`

---

## Modul aktivieren

Das UAS-Modul ist zweistufig gesichert: erst System-Admin, dann Org-Admin.

### Schritt 1 – System-Admin schaltet das Modul frei

Im System-Admin-Bereich:

**System-Einstellungen → UAS-Modul aktivieren** → Schalter einschalten → Speichern.

Dieser Schalter steuert, ob das Modul überhaupt für irgendeine Organisation verfügbar ist. Ohne diesen Schalter sehen alle Orgs das Modul nicht.

### Schritt 2 – Org-Admin aktiviert das Modul für die eigene Org

**Einstellungen → UAS-Modul** → Schalter „UAS-Modul für diese Organisation aktivieren" → Speichern.

Erst dann erscheint der Menüpunkt **„Drohne"** im Hauptmenü und `/uas/` ist erreichbar. Ohne Aktivierung liefert das Modul einen HTTP-404-Fehler.

---

## Geräteregister

**Drohne → Geräteregister** (URL: `/uas/geraete`)

Liste aller Drohnen der Organisation mit Einsatzbereitschafts-Ampel:

| Ampel | Bedeutung |
|-------|----------|
| **Grün** | Einsatzbereit — Wartung aktuell, Versicherung gültig |
| **Gelb** | Wartung oder Versicherung läuft bald ab |
| **Rot** | Wartung überfällig oder Versicherung abgelaufen → nicht einsatzbereit |

### Neue Drohne anlegen

**+ Gerät** (URL: `/uas/geraete/neu`)

| Feld | Pflicht | Beschreibung |
|------|:-------:|-------------|
| Bezeichnung | ✓ | Interner Name (z.B. „DJI Mavic 3T #1") |
| Hersteller | | |
| Typ / Modell | | |
| Registriernummer | | EU-Registrierungsnummer (UAS-Operator-ID) |
| CE-Klasse | | C0–C6 gemäß EU-Drohnenverordnung |
| Unterkategorie | | A1/A2/A3 (Betriebskategorie offen) |
| MTOM (g) | | Maximale Startmasse in Gramm |
| Leergewicht (g) | | |
| Wärmebildkamera | | Checkbox |
| Allwettertauglich | | Checkbox |
| Versicherungspolizze | | Polizzennummer |
| Versicherung gültig bis | | Ablaufdatum → steuert Ampel |
| SYBOS-ID | | Verknüpfung mit SYBOS-Ressourcenverwaltung |
| Beschaffungsdatum | | |
| Tauschintervall (Jahre) | | Standard: 7 Jahre |
| Notizen | | |

Nach dem Speichern wird automatisch ein **QR-Code** generiert, mit dem das Gerät per Scan direkt aufgerufen werden kann.

### Gerät bearbeiten / Status setzen

Auf der Geräte-Detailseite: **Bearbeiten** → alle Felder änderbar.

**Status**-Werte:
- `aktiv` — im Geräteregister sichtbar, für Einsätze wählbar
- `ausser_betrieb` — nicht für Einsätze wählbar (z.B. in Reparatur)
- `ausgemustert` — dauerhaft deaktiviert

---

## Wartungsbuch

Auf der Geräte-Detailseite: **Neue Wartung** (URL: `/uas/geraete/{id}/wartung/neu`)

### Wartungsarten

| Art | Checklisten-Punkte | Nächste Fälligkeit |
|-----|-------------------|--------------------|
| **Monatliche Sichtkontrolle** | 10 Standardpunkte | + 30 Tage |
| **Jahresservice** | 10 + 4 erweiterte Punkte | + 365 Tage |
| **Reparatur** | frei | manuell |
| **Sonstige** | frei | manuell |

### Checklisten-Punkte (monatlich)

- Propeller / Rotoren: Beschädigungen, Risse, Verbiegungen
- Motoren: Lagerspiel, Geräusche, Verschmutzung
- Rahmen / Arme: Risse, Brüche, Verbindungselemente
- Akkus: Aufblähung, Beschädigungen, Kapazitätsverlust
- Ladegeräte und Kabel: Zustand, Isolierung
- Kamera / Gimbal: Befestigung, Funktion, Sauberkeit
- Fernsteuerung: Akku, Display, Antennen, Verbindungstest
- Failsafe-Einstellungen (RTH, Lost-Link) geprüft
- Firmware / Software: aktueller Stand
- Lagerung: Transportkoffer, Temperaturbedingungen

Beim Jahresservice kommen hinzu: Herstellerservice, Motortausch nach Stundenplan, Registrierung/Kennzeichnung aktuell, Versicherungsnachweis.

### Wartungserfassung

Jede Wartung erfasst:
- Datum, Wartungsart, Prüfer (Name)
- Ergebnis: **i.O.** / **nicht i.O.** / **bedingt i.O.**
- Je Prüfpunkt: erledigt (Checkbox) + optionale Bemerkung
- Gesamtbemerkung / Freitext

Die nächste Fälligkeit wird automatisch berechnet und steuert die Wartungsampel auf der Geräteliste.

---

## Pilotenregister

**Drohne → Piloten** (URL: `/uas/piloten`)

Liste aller registrierten Piloten mit Freigabe-Ampel:

| Ampel | Bedeutung |
|-------|----------|
| **Grün** | Vollständig freigegeben |
| **Gelb** | Zertifikat läuft in ≤ 30 Tagen ab |
| **Rot** | Pflichtdokument fehlt oder abgelaufen → gesperrt |

### Neuen Piloten anlegen

**+ Pilot** (URL: `/uas/piloten/neu`)

| Feld | Beschreibung |
|------|-------------|
| Nachname, Vorname | |
| Geburtsdatum | |
| Mitglied verknüpfen | Optional: Verknüpfung mit Mitglied aus dem Mannschaftsregister |
| Ist Truppführer | Checkbox |
| A1/A3-Zertifikat | Lizenznummer + Ablaufdatum |
| A2-Zertifikat | Lizenznummer + Ablaufdatum |
| BOS-Ausbildung (Stufe 1–3) | Stufenbezeichnung + Datum + Rezertifizierung bis |
| LFV-zugelassen | Checkbox (Freigabe durch LFV Vorarlberg) |
| Qualifikationen | Teamleiter / Pilot / Operator (Mehrfachauswahl) |
| Aktiv | Deaktivierte Piloten erscheinen nicht im Einsatz-Dropdown |
| Notizen | Freitext |

### Freigabeprinzip

Ein Pilot gilt als **freigegeben (grün)**, wenn:
- mindestens ein gültiges EU-Zertifikat (A1/A3 oder A2) vorhanden ist
- BOS-Ausbildung dokumentiert ist
- kein abgelaufenes Pflichtdokument vorliegt

Fehlt eine Voraussetzung → **gesperrt (rot)**. Beim Einsatz kann ein gesperrter Pilot mit **Override-Begründung** (Sicherheitsnachweis) trotzdem zugewiesen werden; die Begründung wird protokolliert.

### Flugbewegungen manuell eintragen

Auf der Pilot-Detailseite im Abschnitt **Flugbewegungen**: manuelle Einträge für Ausbildungs-, Übungs- oder Einsatzflüge außerhalb des Systems (z.B. frühere Flüge oder externe Einsätze).

---

## Compliance-Dashboard

Das Dashboard auf `/uas/` wird bei jedem Aufruf neu berechnet und zeigt:

| Kachel | Inhalt |
|--------|--------|
| Wartungen fällig (rot) | Geräte mit überfälliger Wartung |
| Wartungen ablaufend (gelb) | Geräte mit Wartung in ≤ 14 Tagen |
| Versicherung ablaufend | Geräte mit Versicherung in ≤ 30 Tagen |
| Piloten gesperrt (rot) | Piloten ohne gültige Freigabe |
| Zertifikate ablaufend (gelb) | Piloten mit bald ablaufenden Zertifikaten |

> **Empfehlung**: Dashboard vor jedem Einsatz prüfen und alle roten Warnungen bereinigen.

---

## PDF-Export Wartungsbuch

Auf der Geräte-Detailseite → **PDF Wartungsbuch** → komplette Wartungshistorie als PDF (Anhang 8.5 gem. RL-UAS).

---

## Modul deaktivieren

**Einstellungen → UAS-Modul** → Schalter ausschalten.

Das Modul ist dann für diese Org nicht mehr sichtbar. Alle Daten (Geräte, Piloten, Einsätze) bleiben erhalten und können nach erneuter Aktivierung wieder abgerufen werden.
