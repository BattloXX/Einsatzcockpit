# Drohne / UAS

← [Zurück zur Startseite](Home)

> URL: `/uas/`  
> Zugänglich für: alle angemeldeten Benutzer (ab `recorder`)  
> Modul muss vom System-Admin und Org-Admin aktiviert sein (siehe [Administration Drohne/UAS](Administration-Drohne-UAS))

Das UAS-Modul bildet den vollständigen BOS-Drohneneinsatz gemäß **RL-UAS LFV Vorarlberg (Jänner 2024)** digital ab — von der Alarmierung über Flugbuch und Checklisten bis zur Medien-DSGVO und PDF-Exportdokumentation.

---

## Dashboard (Startseite)

**Menü → Drohne** öffnet das UAS-Dashboard.

Auf einen Blick:
- **Geräteregister-Kachel**: Anzahl aktiver Drohnen, Warnungen bei fälliger Wartung oder ablaufender Versicherung
- **Piloten-Kachel**: gesperrte Piloten (rot) und ablaufende Zertifikate (gelb)
- **Compliance-Warnliste**: alle kritischen Punkte (rot/gelb) auf einen Blick, mit Direktlink

> Gibt es Einträge in der Compliance-Warnliste, sollten diese vor dem nächsten Einsatz bereinigt werden.

---

## UAS-Einsatz starten

Ein UAS-Einsatz wird **aus einem laufenden Incident** heraus eröffnet.

1. Im Einsatz-Board den betreffenden Einsatz öffnen.
2. Schaltfläche **„Drohneneinsatz starten"** anklicken.
   - URL: `/uas/incidents/{id}/einsatz/starten`
3. Formular ausfüllen:

| Feld | Beschreibung |
|------|-------------|
| TETRA-Rufname | Funkrufname des UAS-Teams |
| Betreibernummer | Registrierungsnummer des Betreibers |
| Einsatzgrund | Kurzbeschreibung des Auftrags |
| Gesamteinsatzleiter | Name (EL der Gesamtlage) |
| Datenschutz bestätigt | Pflicht: Aufklärung der Bevölkerung bestätigen |
| Alarmierungszeitpunkt | Zeitstempel der Alarmierung |

4. **Speichern** → weiter zur Einsatz-Detailseite.

### Einsatz-Status (Workflow)

| Status | Bedeutung |
|--------|----------|
| **alarmiert** | Einsatz eröffnet, Team alarmiert |
| **angemeldet** | An-/Eintreffmeldung beim EL abgegeben |
| **im_einsatz** | Flugbetrieb aktiv (Mindestbesetzung muss erfüllt sein) |
| **abgemeldet** | Einsatz abgemeldet beim EL |

Den Status über die Schaltflächen auf der Einsatz-Detailseite weiterschalten.

> **Mindestbesetzung (RL 6.1)**: Um den Status auf „im Einsatz" zu schalten, müssen mindestens **Pilot** und **Luftraumbeobachter** zugewiesen sein und die Teamstärke muss ≥ 2 Personen betragen. Fehlt eine Rolle, erscheint eine Fehlermeldung.

---

## Team / Rollenzuweisung

Auf der Einsatz-Detailseite im Abschnitt **„Teambesetzung"**:

1. Rolle auswählen (Pilot, Luftraumbeobachter, Teamleiter, Operator, Bildschirmoperator, Gerätewart, Versorger).
2. Bei Piloten-Rollen: Pilot aus Dropdown wählen.
   - Grün = freigegeben
   - Gelb = Zertifikat läuft bald ab (dennoch einsetzbar)
   - Rot = gesperrt → **Override-Begründung** erforderlich (wird protokolliert)
3. Bei anderen Rollen: freier Helfername.
4. **Hinzufügen**.

---

## Eintreffmeldung

**Einsatz → Eintreffmeldung** (URL: `/uas/einsatz/{id}/eintreffmeldung`)

Standardisierte Meldung gemäß RL-UAS Anhang: Uhrzeit des Eintreffens, eingesetzte Drohne, Teambesetzung, Gerät, Einsatzbereitschaft. Kann als PDF exportiert werden (Anhang 8.6).

---

## Risikobewertung & Kommunikationsmatrix

Auf der Einsatz-Detailseite zwei weitere Abschnitte:

**Risikobewertung** (RL-UAS Pflicht vor jedem Einsatz):
- Gelände, Menschen, Luftraum, Wetter, Sonstiges → Gesamtbewertung

**Kommunikationsmatrix**:
- EL-Sprechgruppe, Flug-Sprechgruppe, TMO/DMO, Luftfahrt-Abstimmung

---

## Flugbuch – Flüge erfassen

**Einsatz → Neuer Flug** (URL: `/uas/einsatz/{id}/flug/neu`)

Für jeden Flug:

| Feld | Beschreibung |
|------|-------------|
| Startzeit / Landezeit | Genauer Zeitstempel |
| Pilot | Aus dem zugewiesenen Team |
| Gerät | Eingesetzte Drohne |
| Luftraum | VLOS/BVLOS, Höhe, Koordinaten Startpunkt |
| Durchführungsgrundlage | STS/Spezialgenehmigung/Szenario |
| Wetterdaten | Wind, Sicht, Temperatur |
| Missionsziel | Freitext |
| Besonderheiten / Vorfälle | Freitext |

Nach dem Speichern erscheint der Flug in der Flugbuch-Liste des Einsatzes.

### Vor- und Nachflugcheckliste (4-Augen)

Auf der Flug-Detailseite:
1. **Vorflugcheckliste** → alle Punkte abhaken → bestätigen (Unterschrift/Name 1. Person).
2. **Gegenzeichnen** durch eine 2. Person (4-Augen-Prinzip).
3. Nach dem Flug: **Nachflugcheckliste** analog.

> Ein Flug gilt erst als vollständig dokumentiert, wenn beide Checklisten 4-Augen-bestätigt sind.

---

## Notfall- und Unfall-Workflow

**Einsatz → Neues Ereignis** (URL: `/uas/einsatz/{id}/ereignis/neu`)

Bei Zwischenfällen während des Einsatzes:

| Art | Verwendung |
|-----|-----------|
| **Notfall** | Sicherheitsrelevante Situation ohne Personenschaden |
| **Unfall** | Unfall mit Sach- oder Personenschaden |

**Meldekette (4-Stufen)**:
1. Interne Erfassung im Tool
2. Meldung an Einsatzleitung
3. Meldung an ACG (Austro Control)
4. Nachbericht / Abschlussdokumentation

**ACG-Meldung**: Direktlink zum ACG-Meldeformular, vorausgefüllte Daten aus dem Ereignis. Als PDF exportierbar (Anhang 8.4).

---

## Karte des UAS-Einsatzes

**Einsatz → Karte** (URL: `/uas/einsatz/{id}/karte`)

- Kartenobjekte anlegen: Startplatz, Landebereich, Sperrbereich, Beobachtungspunkt, Auftragsgebiet
- GeoJSON-Export für externe Systeme
- **Landebefehl-Banner**: rotes Overlay aktivieren, das auf allen Geräten sichtbar sofortigen Landebefehl signalisiert

---

## Medien / Aufnahmen

**Einsatz → Medien** (URL: `/uas/einsatz/{id}/medien`)

DSGVO-konformer Umgang mit Drohnenaufnahmen gemäß RL-UAS:

| Status | Bedeutung |
|--------|----------|
| **erfasst** | Datei hochgeladen, noch nicht geprüft |
| **begruendet** | Rechtsgrundlage für Aufbewahrung dokumentiert |
| **zur_loeschung** | Löschfrist abgelaufen, Löschung freigegeben |
| **geloescht** | Datei gelöscht, Eintrag verbleibt als Nachweis |

Die **Löschfrist-Ampel** zeigt an, welche Dateien bald gelöscht werden müssen (gelb) oder überfällig sind (rot).

---

## PDF-Export

**Einsatz → PDF** oder über den jeweiligen Bereich:

| Anhang | Inhalt |
|--------|--------|
| **8.1 Flugbuch** | Alle Flüge des Einsatzes mit Zeitstempeln und Pilotenangaben |
| **8.2 Checkliste** | Vor- und Nachflugchecklisten mit 4-Augen-Bestätigung |
| **8.3 Einsatzprotokoll** | Gesamtprotokoll (Team, Status, Kommunikation, Risiko) |
| **8.4 ACG-Unfallmeldung** | Vorausgefülltes ACG-Meldeformular |
| **8.5 Wartungsbuch** | Wartungshistorie einer Drohne |
| **8.6 Eintreffmeldung** | Standardmeldung gemäß RL-UAS |

---

## Hinweise

- Das UAS-Modul greift auf die Stammdaten (Geräte, Piloten) zu, die vorab unter **Drohne → Geräteregister** und **Drohne → Piloten** eingepflegt wurden.
- Compliance-Status (Piloten-Freigabe, Wartungsampel, Versicherungsablauf) wird bei jedem Seitenaufruf automatisch berechnet — keine manuelle Aktualisierung nötig.
- Alle Einträge landen im Audit-Log und sind über die [Zeitreise](Administration-Audit-Log-und-Zeitreise) nachvollziehbar.
