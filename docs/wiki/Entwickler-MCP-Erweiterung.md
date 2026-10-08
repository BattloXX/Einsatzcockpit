# MCP-Server erweitern

Die MCP-Implementierung liegt in `app/mcp/`: `server.py` stellt den OAuth-Provider und Streamable-HTTP-Server bereit, `registry.py` beschreibt Werkzeuge und `context.py` lädt den Live-Kontext. Die Tool-Module registrieren sich beim Import. OAuth-Clients, Codes und Tokens stehen in `app/models/mcp.py`; Geheimnisse werden nur gehasht gespeichert.

`app/main.py` hängt die OAuth-Discovery-Routen vor dem Static-Mount ein und setzt die Auth-Middleware auf die Haupt-App. Der Streamable-HTTP-Handler wird pro Lifespan neu gebaut, weil sein Session-Manager nur einmal gestartet werden darf. Diese Reihenfolge für `/.well-known` und der Lifespan-Mechanismus dürfen nicht verloren gehen.

## Neues Werkzeug

1. Ein Tool-Modul unter `app/mcp/tools/` anlegen und in `server.py` importieren.
2. Den Handler mit `register_tool(name=..., description=..., required_roles=..., module_check=...)` registrieren.
3. In `server.py` den passenden `@server.tool`-Adapter anlegen, der ausschließlich `_call_registered_tool()` aufruft.
4. Fachliche Eingaben validieren und erwartete Nutzungsfehler als `ValueError` melden. Der Handler erhält `MCPContext` mit Datenbank, Benutzer und `org_id`.

`load_live_context()` prüft Benutzer, Organisation, MCP-Flags und Rollen bei jedem Aufruf. `module_check` entscheidet zusätzlich pro Fachmodul; `list_tools()` blendet Werkzeuge mit fehlender Live-Berechtigung aus.

## Werkzeuge und Berechtigungen

| Bereich | Werkzeuge | Rollen und Modul-Check |
|---------|-----------|------------------------|
| Allgemein | `mcp_whoami`, `organisation_lesen` | `readonly` |
| Objekte | `objekt_kataloge`, `objekt_suchen`, `objekt_lesen`, `objekt_duplikate_pruefen`, `objekt_anlegen`, `objekt_aktualisieren` | `objekt_verwalter`, Objekt-Modul |
| Objekt-Dokumente | `objekt_dokument_upload_vorbereiten`, `objekt_dokument_uebergeben`, `objekt_dokumente_auflisten`, `objekt_dokument_seiten_klassifizieren`, `objekt_dokument_herunterladen` | `objekt_verwalter`, Objekt-Modul |
| Kontakte | `kontakt_suchen`, `kontakt_duplikate_pruefen`, `kontakt_lesen`, `kontakt_kategorien`, `kontakt_anlegen`, `kontakt_aktualisieren`, `kontakt_archivieren`, `kontakt_zusammenfuehren` | `kontakt_verwalter` oder `objekt_verwalter`, Kontakte-Modul |
| Fahrtenbuch | `fahrtenbuch_stammdaten`, `fahrtenbuch_fahrten`, `fahrtenbuch_fahrt`, `fahrtenbuch_auswertung` | `fahrtenbuch_admin`, Fahrtenbuch-Modul |
| Wasserstellen | `wasserstellen_suchen`, `wasserstelle_lesen`, `wasserstelle_anlegen`, `wasserstelle_aktualisieren`, `wasserstelle_deaktivieren` | `org_admin` (wie Admin-UI), kein Modul |
| Straßensperren | `strassensperren_liste`, `strassensperre_lesen`, `strassensperren_kataloge`, `strassensperren_suchen`, `strassensperren_im_gebiet`, `einsatz_strassensperren`, `einsatz_anfahrtsroute_pruefen`, `strassensperren_entlang_route` | `readonly`, Straßensperren-Modul |
| Straßensperren pflegen | `strassensperre_anlegen`, `strassensperre_aktualisieren`, `strassensperre_deaktivieren`, `strassensperre_reaktivieren` | `objekt_verwalter`, Straßensperren-Modul; kein Löschen; Anlage und Adressänderungen liefern `adressvalidierung` |
| Straßensperren-Geometrie & Verordnungen | `strassensperre_geometrie_ermitteln`, `strassensperre_geometrie_bestaetigen`, `strassensperre_entwurf_aus_pdf`, `strassensperre_dokument_upload_vorbereiten`, `strassensperre_dokument_uebergeben` | `objekt_verwalter`, Straßensperren-Modul; Uploads mit `zweck` `strassensperre`/`entwurf`, Downloads mit Token-Art `strassensperre` |

`required_roles` wird mit `has_role(user, *required_roles)` geprüft: Bei mehreren Rollen genügt eine davon. `system_admin`, `admin` und `org_admin` bestehen die Prüfung ebenfalls. Kontakt-Werkzeuge prüfen ausschließlich das Kontakte-Modul; sie dürfen nicht an das Objekt-Modul gekoppelt werden.

## Pflichten für Handler

- Jede Abfrage und jedes Objekt strikt mit `context.org_id` scopen; bei Worker-Threads den Tenant-Kontext vor dem Zugriff setzen.
- Keine Freigabe auslösen und außerhalb einer erfolgreichen Transaktion nichts committen. Bei mehreren Änderungen atomar arbeiten und bei Fehlern rollbacken.
- Objekt-Änderungen mit `quelle="mcp"` markieren und einen passenden `objekt.mcp_*`-Audit-Eintrag schreiben; Kontaktänderungen über `kontakt_service` ausführen und mit `kontakt.mcp_*` auditieren.
- Nur sichere Ausgabefelder zurückgeben: keine Token, Zugangsdaten oder unnötigen Kontakt-Klartext. Objekt-Werkzeuge dürfen keine Kontaktstammdaten oder Freigaben nebenbei ändern.
- `objekt_lesen` muss für veränderbare Objektkinder deren IDs liefern. Bei vorhandener Arbeitskopie liefert es deren ID; `arbeitskopie=true` liest deren Kinder. `objekt_aktualisieren` bearbeitet Kontaktzuordnungen ausschließlich über `kontakte_aendern` mit `zuordnung_id` und ändert keine Kontaktstammdaten. Lesen und Schreiben bleiben symmetrisch: Jedes Feld, das `objekt_anlegen`/`objekt_aktualisieren` annimmt, liefert `objekt_lesen` mit gleichem Namen (Ausnahme: BMA-SMS/-Mail nur als `*_gesetzt`). Kinder ändern über `merkmale_aendern`/`gefahren_aendern`/`kontakte_aendern`; IDs der Basis werden auf die Arbeitskopie aufgelöst.
- Wasserstellen-Werkzeuge nutzen `erstelle_wasserstelle`/`aktualisiere_wasserstelle` aus `wasserstelle_service` (gleiche Validierung wie die Admin-UI) und auditieren mit `wasserstelle.mcp_*`; Löschen bleibt der Admin-UI vorbehalten.
- Kontakt-Werkzeuge dürfen Kontaktstammdaten ändern. `kontakt_aktualisieren` braucht die aktuelle Version; `kontakt_archivieren` bei Objektzuordnungen und `kontakt_zusammenfuehren` benötigen `bestaetigt=true`. SMS- und Mail-Freigaben je Objektkontakt, SMS-Versand sowie Kontakt-Import und -Export sind nicht per MCP umzusetzen.

## Tests und Checkliste

Die Muster in `tests/test_mcp_fahrtenbuch.py` und `tests/test_mcp_objekte.py` nutzen die Helfer `_mcp`, `_oauth_tokens` und `_rufe` aus `tests/test_mcp_fundament.py`. Neue Werkzeuge mindestens auf Rollenmatrix, deaktiviertes Modul, Tenant-Trennung, Ausgabefelder, Validierungsfehler und die gewünschte Transaktionswirkung testen.

Vor dem Merge prüfen:

- Tool ist registriert, importiert und hat `required_roles` plus gegebenenfalls `module_check`.
- Live-Rechte, Org-Scoping, sensible Felder und Fehlerpfade sind abgedeckt.
- Schreibpfade markieren Quelle und Audit und können nichts freigeben.
- Kontaktmodul-Check, Rollenmatrix für `kontakt_verwalter` und `objekt_verwalter`, Versionskonflikt, Dubletten- und Bestätigungsfälle testen. Sicherstellen, dass kein Kontakt-Werkzeug `ObjektKontaktFreigabe` setzt oder SMS versendet.
- OAuth-Discovery bleibt vor `/.well-known`-Static-Mount; der Lifespan-Test bleibt grün.
- Anwender- und Administrationsdokumentation benennt nur tatsächlich angebotene Werkzeuge.
- Upload-Endpunkt: `POST /api/mcp/uploads/{upload_id}` (`app/mcp/upload_router.py`, Service `app/services/mcp_upload_service.py`, Tabelle `mcp_upload`) ist bewusst öffentlich und nur über das gehashte, einmalige Upload-Token geschützt; er ist von der CSRF-Prüfung ausgenommen und scopet ausschließlich über die Zeile des Tokens (Organisation, Benutzer, Objekt). Neue öffentliche Routen brauchen einen Cross-Org-Test (`tests/test_public_tenant_isolation.py`).
- `objekt_dokument_uebergeben` nimmt genau eines von `inhalt_base64` oder `upload_id`. Ist `seiten[]` vollständig, läuft keine KI; fehlende Seiten werden ohne Dokumentart angelegt und (bei aktiver KI-Klassifizierung) nur als Vorschläge nachgezogen. Antwortfeld `klassifizierung_quelle` ist `client` oder `server`.
- `objekt_aktualisieren` löst die ID einer Arbeitskopie auf das Basisobjekt auf und bildet Zuordnungs-IDs der Basis beim Entfernen auf die Arbeitskopie ab; fachliche Fehler müssen als `ValueError` bzw. `ToolError` auftreten, sonst maskiert das SDK die Meldung.
