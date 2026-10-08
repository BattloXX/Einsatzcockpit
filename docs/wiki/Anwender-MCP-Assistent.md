# MCP-Assistent

Mit dem MCP-Server kann eine KI-Anwendung nach deiner Anmeldung gezielt mit Daten deiner Organisation arbeiten. Die Anwendung sieht nur die Werkzeuge, für die dein Konto aktuell berechtigt ist.

## Verbinden und anmelden

In claude.ai einen **Custom Connector** anlegen und als Server-URL `https://<host>/mcp` eintragen. Claude Desktop und Claude Code verwenden ebenfalls die vom Client vorgesehene MCP-Server-Konfiguration; als Streamable-HTTP-Adresse gilt dieselbe URL. Die konkrete Bedienung kann je Client abweichen.

Beim Verbinden öffnet sich die Einsatzcockpit-Anmeldung. Melde dich mit deinem Einsatzcockpit-Benutzernamen und Passwort an. Reine SSO-Konten können diesen Login nicht verwenden; Geräte-Benutzer ebenfalls nicht. Eine Verbindung kann im Einsatzcockpit unter **Profil** wieder getrennt werden.

## Berechtigungen und Werkzeuge

| Rolle | Werkzeuge |
|-------|-----------|
| `objekt_verwalter`, `org_admin`, `admin` | Objekte, zentrale Kontakte und Objekt-Dokumente |
| `kontakt_verwalter`, `org_admin`, `admin` | Zentrale Kontakte |
| `fahrtenbuch_admin`, `org_admin`, `admin` | Fahrtenbuch-Auswertung |
| `org_admin`, `admin` | Wasserstellen |
| Alle Benutzer mit aktivem Straßensperren-Modul | Straßensperren lesen und Anfahrten prüfen |
| `objekt_verwalter`, `org_admin`, `admin` | Straßensperren anlegen und pflegen |

Die verfügbaren Werkzeuge sind:

- `mcp_whoami`: zeigt Benutzer, Organisation und Rollen.
- `organisation_lesen`: liefert Stammdaten der eigenen Organisation (Name, Kürzel, Kontakt, Adresse, Farben, Fußzeile) und das Logo direkt als Bild, damit Claude es z. B. für Briefköpfe oder Berichte verwenden kann. SVG-Logos kommen als Quelltext, ohne eigenes Logo das Standardlogo.
- `fahrtenbuch_stammdaten`, `fahrtenbuch_fahrten`, `fahrtenbuch_fahrt` und `fahrtenbuch_auswertung`: lesen Stammdaten, Fahrten, Korrekturkette und Auswertungen.
- `objekt_kataloge`, `objekt_suchen`, `objekt_lesen` und `objekt_duplikate_pruefen`: lesen Objektwerte und prüfen Dubletten. `objekt_lesen` liefert alle pflegbaren Felder: Stammdaten inkl. `informationen` (der allgemeine Hinweis-/Infotext zum Objekt) und `anfahrtsweg`, alle BMA-Angaben (SMS-/Mail-Empfänger nur als „gesetzt“), Gefahren mit Details, Merkmale mit Name und Hinweis, Zusatzadressen und Wohnanlage, jeweils mit den IDs für Änderungen; mit `arbeitskopie=true` liest es eine vorhandene Arbeitskopie. Das Feld `arbeitskopie_hinweis` ist nur ein technischer Hinweis auf eine offene Arbeitskopie, kein Objekttext.
- `kontakt_suchen`, `kontakt_duplikate_pruefen`, `kontakt_lesen` und `kontakt_kategorien`: suchen, prüfen und lesen zentrale Kontakte sowie Kategorien. `kontakt_lesen` enthält Telefone, E-Mail, Kategorien, Versionsnummer und Objektzuordnungen.
- `kontakt_anlegen`, `kontakt_aktualisieren`, `kontakt_archivieren` und `kontakt_zusammenfuehren`: pflegen zentrale Kontakte. Die Aktualisierung verlangt die aktuelle `version`; bei einem Konflikt Kontakt neu laden. Archivieren bei Objektzuordnungen und jedes Zusammenführen verlangen `bestaetigt=true`.
- `objekt_anlegen` und `objekt_aktualisieren`: legen Entwürfe an oder bearbeiten Entwürfe beziehungsweise Arbeitskopien. Sie können die Objekt-Stammdaten `informationen`, `anfahrtsweg` und `revision_datum` sowie optionale Wohnanlagen-Daten (`wohneinheiten`, `geschosse`, `stiegen`, `hausverwaltung_kontakt_id`, `hinweise`) pflegen. Mit `kontakte_aendern` ändern sie Art, Sortierung oder Erreichbarkeit einer Objektkontakt-Zuordnung. Den Hinweis an einem Merkmal ändert `merkmale_aendern` (`[{id, hinweis}]`); wird ein bereits zugeordnetes Merkmal über `merkmale_hinzufuegen` mit `hinweis` übergeben, wird der Hinweis übernommen. Gefahren-Details (`un_nummer`, `stoffname`, `gefahrklasse`, `gefahrnummer`, `detail`, `links`) ändert `gefahren_aendern` (`[{id, …}]`, nur die übergebenen Felder).
- `wasserstellen_suchen`, `wasserstelle_lesen`, `wasserstelle_anlegen`, `wasserstelle_aktualisieren` und `wasserstelle_deaktivieren` (nur `org_admin`): suchen Wasserstellen (Text, Typ, Status, optional im Umkreis um `lat`/`lng`), legen sie mit Dublettenprüfung an (gleiche Bezeichnung oder gleicher Typ innerhalb 15 m; sonst `duplikat_bestaetigt=true`) und ändern einzelne Felder. Deaktivieren setzt den Status `defekt` – die Stelle erscheint dann nicht mehr auf Einsatzkarte und Einsatzinfo; ein optionaler Grund wird datiert an den Hinweis angehängt. Reaktivieren über `wasserstelle_aktualisieren` mit `status: bereit`. Löschen ist per MCP nicht möglich.
- `strassensperren_liste`, `strassensperre_lesen`, `strassensperren_kataloge`, `strassensperren_suchen`, `strassensperren_im_gebiet`, `einsatz_strassensperren`, `einsatz_anfahrtsroute_pruefen` und `strassensperren_entlang_route`: lesen sichtbare Sperren und prüfen gespeicherte oder live berechnete Anfahrten. `strassensperren_kataloge` zeigt Enum-Werte, Labels und Aliase.
- `strassensperre_anlegen`, `strassensperre_aktualisieren`, `strassensperre_deaktivieren` und `strassensperre_reaktivieren` (Objektverwaltung): pflegen eigene Sperren. Bei einer möglichen Dublette liefert das Anlegen `possible_duplicate`; vorhandene Sperre aktualisieren oder bewusst mit `duplikat_bestaetigt=true` anlegen. Löschen ist per MCP nicht möglich.
- `objekt_dokument_upload_vorbereiten`: erzeugt einen einmaligen Upload-Link (15 Minuten gültig) samt curl-Beispiel, damit große PDFs nicht als Base64 im Tool-Aufruf stehen müssen.
- `objekt_dokument_uebergeben`, `objekt_dokumente_auflisten` und `objekt_dokument_seiten_klassifizieren`: übergeben, listen und klassifizieren Objekt-PDFs. Übergabe entweder mit `upload_id` (nach dem curl-Upload) oder – für kleine Dateien – mit `inhalt_base64`. `seiten` hat das Format `[{"nr":1,"dokumentart":"bma_datenblatt","titel":null}]`; die Klassifizierung des Clients wird unverändert übernommen.
- `objekt_dokument_herunterladen`: holt das Original-PDF eines Objektdokuments (jede Version, optional mit `seite` nur eine Einzelseite). Liefert einen 15 Minuten gültigen Download-Link, den du auch im Browser öffnen kannst, samt curl-Beispiel; mit `inline=true` kommt eine kleine Datei (bis 8 MB) zusätzlich direkt als Base64. Die `dokument_id` liefert `objekt_dokumente_auflisten` (dort jetzt auch mit Dateigröße).

## Kontrollierter Ablauf

MCP kann nie ein Objekt oder Dokument freigeben. Neue Objekte bleiben Entwürfe; bei freigegebenen Objekten entsteht eine Arbeitskopie. Neue Dokumente an einem freigegebenen Objekt warten auf Freigabe im Einsatzcockpit. Prüfe und gib dort bewusst frei. Zentrale Kontakte können per MCP gepflegt werden; beim Aktualisieren schützt die Versionsnummer vor dem Überschreiben zwischenzeitlicher Änderungen. SMS- und Mail-Freigaben, SMS-Versand sowie Kontakt-Import und -Export bleiben dabei aus und stehen nur im Einsatzcockpit zur Verfügung.

Bei "fertig übergebenen" Plänen analysiert Claude das PDF vor der Übergabe: Volltext und Seitenklassifizierung werden mitgeliefert. Das Einsatzcockpit macht danach kein OCR und keine KI-Analyse, erzeugt aber weiterhin technisch die Seitenvorschauen.

Große PDFs laufen zweistufig: Claude ruft `objekt_dokument_upload_vorbereiten` auf, lädt die Datei mit dem gelieferten `curl`-Befehl von deinem Rechner hoch und übergibt sie danach mit `upload_id` und der fertigen Seitenklassifizierung. Voraussetzung ist, dass Claude auf deinem Rechner Befehle ausführen darf und dein Einsatzcockpit von dort erreichbar ist. Wenn du Kontakte zu einem Objekt hinzufügst, die schon fast identisch existieren, nennt die Fehlermeldung den neuen Kontakt und die Kandidaten mit Namen; dann entweder die vorhandene `kontakt_id` verwenden oder `duplikat_bestaetigt=true` setzen. Für `objekt_aktualisieren` genügt die ID des Objekts oder seiner Arbeitskopie.

Beispiele:

```text
Analysiere diese Pläne, prüfe Objekt- und Kontakt-Dubletten, lege einen Objektentwurf an oder aktualisiere die Arbeitskopie und übergib die klassifizierten PDFs.
```

```text
Suche den zentralen Kontakt der Hausverwaltung, lies ihn mit Version, ergänze die Erreichbarkeit und aktualisiere ihn nur, wenn keine andere Änderung dazwischenkam.
```

```text
Prüfe die beiden Kontakte auf Dubletten. Zeige mir die Daten und Objektzuordnungen, bevor du sie mit bestaetigt=true zusammenführst.
```

```text
Lies Objekt 123 einschließlich Arbeitskopie, hänge Kontakt 45 als Hausverwaltung an, ändere anschließend dessen Erreichbarkeit oder entferne die Zuordnung anhand ihrer zuordnung_id.
```

```text
Lade das PDF ~/Downloads/bma-datenblatt.pdf über den Upload-Link ins Objekt 123, klassifiziere jede Seite und übergib es an die Arbeitskopie.
```

```text
Werte alle aktiven, statistikrelevanten Fahrten 2026 pro Fahrzeug aus.
```

```text
Trage morgen von 07:00 bis 18:00 eine Vollsperre der Bregenzer Straße zwischen Hausnummer 12 und 38 ein.
```

```text
Gibt es für Einsatz 2026-471 Probleme bei der Anfahrt?
```

## Grenzen und Datenschutz

PDFs werden entweder als Base64 im Tool-Aufruf (dekodiert bis 8 MB) oder über einen einmaligen Upload-Link (bis 50 MB, 15 Minuten gültig, an Benutzer und Objekt gebunden) übergeben; nicht übergebene Uploads werden nach 24 Stunden gelöscht. Kontakt-Import und -Export, SMS- und Mail-Freigaben je Objektkontakt sowie SMS-Versand sind keine MCP-Werkzeuge. Fahrtenbuchdaten enthalten Fahrernamen; Fahrtenbuch-Admins sehen diese und übermitteln sie bei einer Abfrage an die verwendete KI-Anwendung. Beachte deshalb die Datenschutz- und Freigaberegeln deiner Organisation.
