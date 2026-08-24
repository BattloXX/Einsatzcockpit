# Geräteverleih – Administration

← [Zurück zur Startseite](Home)

> URL: `/admin/verleih-artikel`  
> Zugänglich für: `org_admin`, `system_admin`  
> Modul-Aktivierung: **Einstellungen → GSL-Einstellungen → Geräteverleih aktivieren**

---

## Modul aktivieren

**Admin → Einstellungen → GSL-Einstellungen** → Schalter „Geräteverleih" einschalten → Speichern.

Das Modul erscheint dann im GSL-Board für Einsätze dieser Organisation.

---

## Artikel verwalten

**URL:** `/admin/verleih-artikel`

Artikel sind die Stammdaten des Geräteverleihs — einzelne Gerättypen mit optionalem Bestand.

### Neuen Artikel anlegen

**+ Artikel** (URL: `/admin/verleih-artikel/neu`)

| Feld | Pflicht | Beschreibung |
|------|:-------:|-------------|
| **Bezeichnung** | ✓ | Anzeigename des Artikels (z.B. „Atemschutzgerät PA-90") |
| **Barcode / QR-Code** | | Barcode oder QR-Code-Inhalt für Scan-Erfassung im Verleih |
| **Einheit** | | Maßeinheit: Stück / Satz / Karton / kg / l |
| **Bestand** | | Maximaler Bestand (Soll-Menge) |
| **Beschreibung** | | Freitext |
| **Aktiv** | | Inaktive Artikel erscheinen nicht im Verleih-Formular |

Nach dem Speichern wird automatisch ein **QR-Code** für den Scan-Modus generiert.

### Artikel bearbeiten / deaktivieren

Bestehende Artikel können jederzeit bearbeitet werden. Das Deaktivieren eines Artikels entfernt ihn aus dem Verleih-Formular, löscht aber keine historischen Ausleihen.

---

## Stücklisten verwalten

**URL:** `/admin/verleih-stuecklisten`

Stücklisten sind vordefinierte Pakete aus mehreren Artikeln. Typische Beispiele:
- „Standard-Ausrüstung Trupp" (Handleuchte, Schutzhandschuhe, Funkgerät)
- „Einsatzkoffer Sanitäts" (Verbandsmaterial, Sauerstoff, Beatmungsbeutel)

### Neue Stückliste anlegen

**+ Stückliste**

| Feld | Pflicht | Beschreibung |
|------|:-------:|-------------|
| **Bezeichnung** | ✓ | Name der Stückliste |
| **Beschreibung** | | Freitext |
| **Positionen** | | Artikel + Menge (beliebig viele) |

Positionen können nach dem Anlegen der Stückliste hinzugefügt, bearbeitet und gelöscht werden.

---

## Verleih-Übersicht (Admin)

**URL:** `/admin/verleih-uebersicht`

Org-Admin-Übersicht aller Ausleihen über alle aktiven Großschadenslagen:

| Spalte | Beschreibung |
|--------|-------------|
| **Lage** | Zugehörige Großschadenslage |
| **Empfänger** | Einsatzstelle oder Einheit |
| **Material** | Ausgegeben |
| **Status** | Offen / Teilweise zurück / Vollständig zurück |
| **Zeitstempel** | Ausgabezeitpunkt |

---

## SMS-Erinnerungen

Das System kann automatisch **SMS-Erinnerungen** an Empfänger mit offenen Ausleihen senden (wenn das SMS-Gateway konfiguriert ist):

- Erinnerung wird nach konfigurierbarer Zeit automatisch ausgelöst
- Manuelle Erinnerung ist über die Ausleihe-Detailseite jederzeit möglich

→ Siehe [Installation SMS-Gateway](Installation-SMS-Gateway)
