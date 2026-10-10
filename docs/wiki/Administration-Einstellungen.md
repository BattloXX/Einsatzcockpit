# Einstellungen

← [Zurück zur Startseite](Home)

> URL: `/admin/settings`  
> Zugänglich für: `org_admin`, `admin`, `system_admin`

## Organisations-Einstellungen

Jede Organisation kann ihre eigenen Einstellungen verwalten.

### Logo hochladen

**Einstellungen** → **Logo** → Datei auswählen (PNG, JPG, SVG, WebP — max. 2 MB)

Das Logo wird angezeigt:
- Im Header der App (neben dem Organisationsnamen)
- Auf dem Deckblatt des PDF-Einsatzberichts
- Auf der About-Seite

Hochgeladene Logos werden in `app/static/img/uploads/` gespeichert und sind beim System-Update **geschützt** (werden nicht überschrieben).

### Organisationsname

Der angezeigte Name der Organisation in der gesamten App und in Berichten.

### Primärfarbe

Die Akzentfarbe der Organisation:
- Linker Streifen auf Fahrzeugkarten im Board
- Wird auch für eigene Fahrzeuge im Multi-Org-Einsatz genutzt

### Kontaktdaten

Für Impressum und PDF-Berichte: E-Mail, Telefon, Adresse.

### Footer-Text

Erscheint im Footer jedes PDF-Einsatzberichts (z.B. „FF Wolfurt — Rathausstraße 1 — 6922 Wolfurt").

### Karte / Fallback-Standort

Der Fallback-Standort bestimmt, wo der Karten-Picker im Adress-Bearbeitungs-Dialog startet, wenn noch keine Koordinaten am Einsatz gesetzt sind und Geocoding keinen Treffer liefert.

**Felder**: Breitengrad (lat) + Längengrad (lng) in Dezimalgrad.  
**Karten-Picker**: Marker auf der Karte verschieben oder direkt auf die Karte klicken.  
**Standard**: Wolfurt (47.4664 / 9.7416) — falls nicht konfiguriert.

Details zur Lagekarte.info-Integration: [Lagekarte.info](Anwender-Lagekarte)

## Auto-Schließen (Einsatz automatisch beenden)

Einsätze können automatisch geschlossen werden, wenn sie für eine konfigurierbare Zeit inaktiv waren.

| Einstellung | Beschreibung |
|-------------|-------------|
| **Auto-Schließen aktiviert** | Einschalten für diese Org (NULL = globaler Standard) |
| **Nach Stunden** | Einsatz nach X Stunden ohne Aktivität schließen |
| **Toleranzzeit (Minuten)** | Zusätzliche Karenzzeit vor dem Schließen |

Wenn die Org-Einstellung auf NULL steht, gelten die globalen Werte aus den System-Einstellungen.

> Einsätze mit aktiver Atemschutzüberwachung werden nie automatisch geschlossen.

## System-Einstellungen (nur system_admin)

Auf der Einstellungsseite sieht der System-Admin zusätzlich:

- **Aktuelle App-Version**
- **Schnelllinks**: Organisationen verwalten, System-Update, About

Globale Auto-Schließ-Defaults (gelten für alle Orgs, die kein eigenes Limit gesetzt haben) werden in den System-Einstellungen konfiguriert.

## Lokale Wetterstation

Orgs mit einer Davis Vantage Pro 2 Plus (oder kompatibler Station) können diese über **Admin → Einstellungen → Wetterstation** anbinden.

**Station anlegen:**
1. „Station hinzufügen" — Name eingeben, optionale GPS-Koordinaten
2. Der einmalige **Push-Token** (`wxst_...`) und die fertige **Meteobridge-URL** werden angezeigt
3. URL in **Meteobridge PRO RED → Services → Custom Push** eintragen, Intervall 5 min, Methode GET

> Token wird nur einmalig angezeigt — sofort in Meteobridge eintragen.

**Token rotieren:** „Token neu generieren" widerruft den alten Token sofort.

**Station entfernen:** Löscht Station und Token; historische Zeitreihen werden beim nächsten Retention-Lauf bereinigt.

Vollständige Anleitung: [Lokale Wetterstation (Admin)](Administration-Wetterstation)

## System-Update (nur system_admin)

Neue Versionen können über `/admin/system/update` per ZIP-Upload eingespielt werden.

## About-Seite

`/admin/about` — Versions-Info, Autoren, Changelog.

Zugänglich für alle angemeldeten Benutzer.


---

## GSL: Gruppenkommandanten-Zugang

Unter **Einstellungen → Großschadenslage** steuert die Karte „Gruppenkommandanten-Zugang“:

| Einstellung | Wirkung |
|---|---|
| Zugang aktiv | Hauptschalter, standardmäßig **aus**. Ausschalten widerruft alle bestehenden Zugänge der Organisation. |
| Nachrichtenvorlage | Text für SMS und Kopieren, mit Platzhaltern, Live-Zähler der SMS-Segmente und Vorschau. Der Link wird in Protokollen geschwärzt. |
| Automatisch senden | SMS beim Zuweisen eines Gruppenkommandanten (einmal pro GK/Nummer). Im Übungsbetrieb wird keine echte SMS gesendet. |
| Gültigkeit, Limits | Laufzeit des Links und der Sitzung, Versandlimits. |
| SMS-PIN | Optionaler zweiter Faktor beim Einlösen; Sperre nach Fehlversuchen. |
| Ressourcen pflegen | Erlaubt dem GK, Personal und Ausstattung der eigenen Einheit zu ändern. |
| Notbremse | Widerruft sofort alle Zugänge der Lage/Organisation. |

| Auftrags-SMS automatisch | Sendet bei neuem/geändertem/zurückgezogenem Auftrag eine SMS an den Gruppenkommandanten (eigene Vorlage mit Platzhaltern `{ereignis}`, `{gsl}`, `{einheit}`, `{einsatzstelle}`, `{auftrag}`, `{link}`; `{link}` ist Pflicht). Voraussetzung: Zugang aktiv und Mobilnummer vorhanden. |
| QR-Zugang aktiv | Erlaubt QR-Ausdrucke für Einheiten (Standard: aus). Ausschalten widerruft alle QR-Zugänge. |
| QR-Gültigkeit (Stunden) | 1–168, Standard 72. |
| QR-PIN | Optional; die PIN wird nur der Einsatzleitung angezeigt, nie gedruckt. |

**Serverkonfiguration:** `GSL_ZUGANG_KEY` (eigener, geheimer Schlüssel für die Ableitung der Zugangslinks, mindestens 32 Zeichen). Ein Wechsel des Schlüssels macht alle ausgegebenen Links ungültig. Ohne Wert gilt in Produktion ein Fehler beim Ausstellen.
