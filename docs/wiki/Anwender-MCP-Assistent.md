# MCP-Assistent

Mit dem MCP-Server kann eine KI-Anwendung nach deiner Anmeldung gezielt mit Daten deiner Organisation arbeiten. Die Anwendung sieht nur die Werkzeuge, für die dein Konto aktuell berechtigt ist.

## Verbinden und anmelden

In claude.ai einen **Custom Connector** anlegen und als Server-URL `https://<host>/mcp` eintragen. Claude Desktop und Claude Code verwenden ebenfalls die vom Client vorgesehene MCP-Server-Konfiguration; als Streamable-HTTP-Adresse gilt dieselbe URL. Die konkrete Bedienung kann je Client abweichen.

Beim Verbinden öffnet sich die Einsatzcockpit-Anmeldung. Melde dich mit deinem Einsatzcockpit-Benutzernamen und Passwort an. Reine SSO-Konten können diesen Login nicht verwenden; Geräte-Benutzer ebenfalls nicht. Eine Verbindung kann im Einsatzcockpit unter **Profil** wieder getrennt werden.

## Berechtigungen und Werkzeuge

| Rolle | Werkzeuge |
|-------|-----------|
| `objekt_verwalter`, `org_admin`, `admin` | Objekte, zentrale Kontakte und Objekt-Dokumente |
| `fahrtenbuch_admin`, `org_admin`, `admin` | Fahrtenbuch-Auswertung |

Die verfügbaren Werkzeuge sind:

- `mcp_whoami`: zeigt Benutzer, Organisation und Rollen.
- `fahrtenbuch_stammdaten`, `fahrtenbuch_fahrten`, `fahrtenbuch_fahrt` und `fahrtenbuch_auswertung`: lesen Stammdaten, Fahrten, Korrekturkette und Auswertungen.
- `objekt_kataloge`, `objekt_suchen`, `objekt_lesen` und `objekt_duplikate_prüfen`: lesen Objektwerte und prüfen Dubletten.
- `kontakt_suchen` und `kontakt_duplikate_prüfen`: suchen zentrale Kontakte ohne Telefon- oder E-Mail-Klartext und prüfen Dubletten.
- `objekt_anlegen` und `objekt_aktualisieren`: legen Entwürfe an oder bearbeiten Entwürfe beziehungsweise Arbeitskopien.
- `objekt_dokument_übergeben`, `objekt_dokumente_auflisten` und `objekt_dokument_seiten_klassifizieren`: übergeben, listen und klassifizieren Objekt-PDFs.

## Kontrollierter Ablauf

MCP kann nie ein Objekt oder Dokument freigeben. Neue Objekte bleiben Entwürfe; bei freigegebenen Objekten entsteht eine Arbeitskopie. Neue Dokumente an einem freigegebenen Objekt warten auf Freigabe im Einsatzcockpit. Prüfe und gib dort bewusst frei. Bestehende zentrale Kontakte werden durch MCP nicht verändert; es können nur neue Kontakte angelegt und zugeordnet werden. SMS- und Mail-Freigaben bleiben dabei aus.

Bei "fertig übergebenen" Plänen analysiert Claude das PDF vor der Übergabe: Volltext und Seitenklassifizierung werden mitgeliefert. Das Einsatzcockpit macht danach kein OCR und keine KI-Analyse, erzeugt aber weiterhin technisch die Seitenvorschauen.

Beispiele:

```text
Analysiere diese Pläne, prüfe Objekt- und Kontakt-Dubletten, lege einen Objektentwurf an oder aktualisiere die Arbeitskopie und übergib die klassifizierten PDFs.
```

```text
Werte alle aktiven, statistikrelevanten Fahrten 2026 pro Fahrzeug aus.
```

## Grenzen und Datenschutz

PDFs werden nur als Base64 im Tool-Aufruf übergeben; das dekodierte Limit beträgt standardmäßig 8 MB. Es gibt keinen Upload-Link. Kontakte lassen sich nur neu anlegen, nicht überschreiben. Fahrtenbuchdaten enthalten Fahrernamen; Fahrtenbuch-Admins sehen diese und übermitteln sie bei einer Abfrage an die verwendete KI-Anwendung. Beachte deshalb die Datenschutz- und Freigaberegeln deiner Organisation.
