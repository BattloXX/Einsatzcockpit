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

Die verfügbaren Werkzeuge sind:

- `mcp_whoami`: zeigt Benutzer, Organisation und Rollen.
- `fahrtenbuch_stammdaten`, `fahrtenbuch_fahrten`, `fahrtenbuch_fahrt` und `fahrtenbuch_auswertung`: lesen Stammdaten, Fahrten, Korrekturkette und Auswertungen.
- `objekt_kataloge`, `objekt_suchen`, `objekt_lesen` und `objekt_duplikate_pruefen`: lesen Objektwerte und prüfen Dubletten. `objekt_lesen` liefert Zuordnungs-IDs für Kontakte, Gefahren, Merkmale und Zusatzadressen; mit `arbeitskopie=true` liest es die Kinder einer vorhandenen Arbeitskopie.
- `kontakt_suchen`, `kontakt_duplikate_pruefen`, `kontakt_lesen` und `kontakt_kategorien`: suchen, prüfen und lesen zentrale Kontakte sowie Kategorien. `kontakt_lesen` enthält Telefone, E-Mail, Kategorien, Versionsnummer und Objektzuordnungen.
- `kontakt_anlegen`, `kontakt_aktualisieren`, `kontakt_archivieren` und `kontakt_zusammenfuehren`: pflegen zentrale Kontakte. Die Aktualisierung verlangt die aktuelle `version`; bei einem Konflikt Kontakt neu laden. Archivieren bei Objektzuordnungen und jedes Zusammenführen verlangen `bestaetigt=true`.
- `objekt_anlegen` und `objekt_aktualisieren`: legen Entwürfe an oder bearbeiten Entwürfe beziehungsweise Arbeitskopien. Sie können die Objekt-Stammdaten `informationen`, `anfahrtsweg` und `revision_datum` sowie optionale Wohnanlagen-Daten (`wohneinheiten`, `geschosse`, `stiegen`, `hausverwaltung_kontakt_id`, `hinweise`) pflegen. Mit `kontakte_aendern` ändern sie Art, Sortierung oder Erreichbarkeit einer Objektkontakt-Zuordnung.
- `objekt_dokument_uebergeben`, `objekt_dokumente_auflisten` und `objekt_dokument_seiten_klassifizieren`: übergeben, listen und klassifizieren Objekt-PDFs.

## Kontrollierter Ablauf

MCP kann nie ein Objekt oder Dokument freigeben. Neue Objekte bleiben Entwürfe; bei freigegebenen Objekten entsteht eine Arbeitskopie. Neue Dokumente an einem freigegebenen Objekt warten auf Freigabe im Einsatzcockpit. Prüfe und gib dort bewusst frei. Zentrale Kontakte können per MCP gepflegt werden; beim Aktualisieren schützt die Versionsnummer vor dem Überschreiben zwischenzeitlicher Änderungen. SMS- und Mail-Freigaben, SMS-Versand sowie Kontakt-Import und -Export bleiben dabei aus und stehen nur im Einsatzcockpit zur Verfügung.

Bei "fertig übergebenen" Plänen analysiert Claude das PDF vor der Übergabe: Volltext und Seitenklassifizierung werden mitgeliefert. Das Einsatzcockpit macht danach kein OCR und keine KI-Analyse, erzeugt aber weiterhin technisch die Seitenvorschauen.

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
Werte alle aktiven, statistikrelevanten Fahrten 2026 pro Fahrzeug aus.
```

## Grenzen und Datenschutz

PDFs werden nur als Base64 im Tool-Aufruf übergeben; das dekodierte Limit beträgt standardmäßig 8 MB. Es gibt keinen Upload-Link. Kontakt-Import und -Export, SMS- und Mail-Freigaben je Objektkontakt sowie SMS-Versand sind keine MCP-Werkzeuge. Fahrtenbuchdaten enthalten Fahrernamen; Fahrtenbuch-Admins sehen diese und übermitteln sie bei einer Abfrage an die verwendete KI-Anwendung. Beachte deshalb die Datenschutz- und Freigaberegeln deiner Organisation.
