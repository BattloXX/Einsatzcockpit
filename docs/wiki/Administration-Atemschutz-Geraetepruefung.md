# Atemschutz-Geräteprüfung – Administration

← [Zurück zur Startseite](Home)

> URL-Bereich: `/admin/atemschutz-pruefung/` und `/admin/settings`  
> Zugänglich für: `org_admin`, `admin`, `system_admin`

Die Administration verwaltet die Geräte-Stammdaten, die Empfänger für Defektmeldungen und den öffentlichen Token-/QR-Zugang. Die Erfassung und Auswertung beschreibt [Atemschutz-Geräteprüfung](Anwender-Atemschutz-Geraetepruefung).

---

## Modul aktivieren

Unter **Organisation** (`/admin/settings`) im Abschnitt **Atemschutz-Geräteprüfung** die Option **Atemschutzgeräteprüfung für diese Organisation aktivieren** anhaken und die Organisationseinstellungen speichern.

| Option | Beschreibung |
|--------|-------------|
| **Atemschutzgeräteprüfung für diese Organisation aktivieren** | Aktiviert das Modul für die eigene Organisation (`atemschutz_pruefung_modul_aktiv`); Voraussetzung für das Öffnen des öffentlichen Erfassungsformulars |

Das Modul wird ausschließlich je Organisation gesteuert und hat keinen zusätzlichen systemweiten Freigabeschalter. Die Aktivierung erfolgt in den Organisationseinstellungen, nicht im Einstellungsformular für die Wart-Benachrichtigung.

---

## Übersicht Admin-Bereich

Im Modul führt **Einstellungen** zu den Geräte-Stammdaten. Darunter stehen die Unterbereiche **Geräte-Stammdaten**, **Token / QR** und **Allgemein** zur Verfügung.

| URL | Funktion |
|-----|---------|
| `/admin/settings` | Modul für die Organisation aktivieren |
| `/admin/atemschutz-pruefung/geraete` | Geräte anlegen und bearbeiten |
| `/admin/atemschutz-pruefung/token` | Organisations-Token erzeugen oder erneuern; Link und QR-Code anzeigen |
| `/admin/atemschutz-pruefung/einstellungen` | E-Mail-Adresse und Teams-Webhook für Defektmeldungen |
| `/atemschutz-pruefung` | Prüfprotokolle, Filter und Exporte |
| `/atemschutz-pruefung/geraete` | Geräteübersicht mit letzter Prüfung |

---

## Geräte anlegen und bearbeiten

**Einstellungen → Geräte-Stammdaten** (`/admin/atemschutz-pruefung/geraete`)

Die Liste enthält alle Geräte der eigenen Organisation, sortiert nach Gerätenummer.

### Neues Gerät

Im Bereich **Neues Gerät** die Angaben ausfüllen und **Hinzufügen** wählen.

| Feld | Pflicht | Beschreibung |
|------|:-------:|-------------|
| **Gerätenummer** | ✓ | Kennzeichnung des Pressluftatmers; wird in Auswahl und Protokollen angezeigt |
| **Bezeichnung** | | Ergänzende Bezeichnung; das Anzeigelabel besteht aus Nummer und, sofern vorhanden, Bezeichnung |

Neue Geräte sind standardmäßig aktiv. Im Erfassungsformular werden nur aktive Geräte angeboten.

### Bestehendes Gerät

Beim Gerät **Bearbeiten** aufklappen, Angaben ändern und **Speichern** wählen.

| Feld | Beschreibung |
|------|-------------|
| **Nummer** | Gerätenummer ändern |
| **Bezeichnung** | Ergänzende Bezeichnung ändern oder leeren |
| **Aktiv** | Checkbox für den Aktivstatus des Geräts; der aktuelle Status wird auch in der Liste angezeigt |

Geräteanlage und Änderungen werden im Audit-Protokoll festgehalten.

---

## Allgemeine Einstellungen

**Einstellungen → Allgemein** (`/admin/atemschutz-pruefung/einstellungen`)

Das Einstellungsformular enthält zwei optionale Felder für die org-weite Wart-Benachrichtigung. Mit **Speichern** übernehmen; leere Eingaben entfernen die jeweilige Konfiguration.

| Feld | Beschreibung |
|------|-------------|
| **Atemschutzgerätewart – E-Mail** | Empfängeradresse für Defektmeldungen per E-Mail. Der Versand verwendet die SMTP-Konfiguration der Organisation oder ersatzweise die allgemeine SMTP-Konfiguration |
| **Teams-Webhook** | Incoming-Webhook-URL für Microsoft Teams; erhält bei einem Defekt eine Karte mit den Angaben zur Prüfung und einem Link zum Protokoll |

Sind beide Felder gesetzt, werden beide Kanäle angesprochen. Sind beide leer, erfolgt keine Benachrichtigung.

### Auslöser und Versandprotokoll

Eine Meldung wird nach dem Speichern ausgelöst, wenn mindestens einer dieser Prüfpunkte nicht in Ordnung ist:

| Prüfpunkt | Bedingung für Nicht in Ordnung |
|-----------|-------------------------------|
| **Flaschendruck** | Unter 270 bar |
| **Sichtprüfung** | Auswahl Nicht in Ordnung |
| **Hochdruck-Dichtprüfung** | Druckabfall über 10 bar/min |
| **Warnsignal-Prüfung** | Unter 50 oder über 60 bar |
| **Gerät einsatzbereit** | Auswahl Nicht in Ordnung |

Bei einem Defekt ist die Zusatzinfo im Erfassungsformular verpflichtend. Die Grenzwerte sind im Datenmodell festgelegt und werden nicht über dieses Einstellungsformular geändert.

Die Meldung enthält Gerät, fehlerhafte Prüfpunkte, Atemschutzträger, Datum, Ort, Zusatzinfo und den Link zum Protokoll. Der Versand läuft im Hintergrund; ein Versandfehler beeinträchtigt das gespeicherte Prüfprotokoll nicht.

Versandergebnisse werden als **gesendet** oder **fehler** mit Kanal, Empfänger und Zeitpunkt protokolliert. Vorhandene Einträge erscheinen in der Detailansicht unter **Wart-Benachrichtigung**.

---

## Token und QR-Code

**Einstellungen → Token / QR** (`/admin/atemschutz-pruefung/token`)

Der Organisations-Token ermöglicht die Prüfungserfassung ohne Benutzer-Login, etwa an einer Tablet-Station.

### Token erzeugen

1. **Token generieren** wählen.
2. Die Seite zeigt den aktuellen Link nach dem Muster `https://einsatz.example.com/ap/{token}` und den zugehörigen QR-Code.
3. Den Link auf dem Tablet als Lesezeichen oder Startseite hinterlegen oder das Formular durch Scannen des QR-Codes öffnen.

Der QR-Code ist als PNG unter `/admin/atemschutz-pruefung/token/qr.png` abrufbar und verweist auf dasselbe Organisationsformular. Jeder mit dem gültigen Link kann ohne Anmeldung Prüfungen für diese Organisation erfassen, sofern das Modul aktiviert ist.

### Token erneuern

**Token rotieren (neuer Link)** ersetzt den vorhandenen Token durch einen neuen zufälligen Token.

> **Token rotieren** macht den alten Link ungültig. Alle ausgegebenen Links und QR-Codes müssen danach neu verteilt beziehungsweise ersetzt werden.

Die Token-Erneuerung wird im Audit-Protokoll festgehalten. Das Erzeugen eines Tokens ersetzt nicht die Aktivierung des Moduls in den Organisationseinstellungen.

---

## Berechtigungen

| Funktion | Berechtigung |
|----------|-------------|
| **Öffentliches Erfassungsformular öffnen** | Gültiger Organisations-Token und aktiviertes Modul; kein Login erforderlich |
| **Prüfung speichern** | Gültiger Token oder angemeldeter Benutzer; bei Anmeldung wird die Organisation des Benutzers verwendet |
| **Prüfprotokolle, Details und Geräteübersicht lesen** | Alle angemeldeten Benutzer, jeweils für die eigene Organisation |
| **Einzel-PDF, Druck-PDF und XLSX-Export** | Alle angemeldeten Benutzer, jeweils für die eigene Organisation |
| **Geräte, Benachrichtigungseinstellungen und Token verwalten** | `org_admin`, `admin`, `system_admin` |
| **Modul in den Organisationseinstellungen aktivieren** | `org_admin`, `admin`, `system_admin` |

Die Admin-Routen verwenden `require_role("admin")`; die zentrale Rollenprüfung lässt dabei auch `org_admin` und `system_admin` zu. Eine eigene Fachrolle für die Administration der Atemschutz-Geräteprüfung ist dort nicht vorgesehen. Die Verwaltungsabfragen bleiben auf die Organisation des angemeldeten Benutzers beschränkt.
