# MCP-Server (Administration)

Der MCP-Server verbindet freigegebene KI-Anwendungen mit Einsatzcockpit. Er stellt je nach Rolle Werkzeuge für Objektpflege, Objekt-Dokumente und das Fahrtenbuch bereit. Die Verbindung verwendet OAuth; sie ist keine REST-API und kein Ersatz für die Freigabe im Einsatzcockpit.

## Voraussetzungen und Aktivierung

Die Instanz braucht eine von außen erreichbare HTTPS-URL. `PUBLIC_BASE_URL` muss auf genau diese öffentliche Basis-URL zeigen: Sie wird als OAuth-Issuer und für die MCP-Ressource verwendet. Reverse-Proxies müssen das originale Host- und HTTPS-Protokoll weitergeben.

MCP ist zweistufig aktiviert:

1. System-Admin: `/admin/settings` -> **KI-Anwendungen (MCP) systemweit aktivieren**. Das setzt `mcp_module_enabled`.
2. Org-Admin: im selben Bereich **MCP für diese Organisation aktivieren**. Das setzt `mcp_modul_aktiv`; der Schalter ist erst bei aktivem System-Flag verfügbar.

Zusatzlich gilt das jeweilige Fachmodul: Objekt-Werkzeuge brauchen die aktivierte Objektverwaltung, Kontakt-Werkzeuge das Kontakte-Modul und Fahrtenbuch-Werkzeuge das aktivierte Fahrtenbuch. Deaktivierte Werkzeuge werden nicht angeboten.

Ist das Straßensperren-Modul aktiviert, erhalten alle Benutzer die lesenden Tools `strassensperren_liste`, `strassensperre_lesen`, `strassensperren_kataloge`, `strassensperren_suchen`, `strassensperren_im_gebiet`, `strassensperren_kennzahlen`, `einsatz_strassensperren`, `einsatz_anfahrtsroute_pruefen` und `strassensperren_entlang_route`. `objekt_verwalter` erhalten zusätzlich `strassensperre_anlegen`, `strassensperre_aktualisieren`, `strassensperre_deaktivieren`, `strassensperre_reaktivieren`, `strassensperre_beenden`, `strassensperre_teams_senden`, `strassensperre_freigabelink`, `strassensperre_geometrie_ermitteln`, `strassensperre_geometrie_bestaetigen`, `strassensperre_dokument_upload_vorbereiten`, `strassensperre_dokument_uebergeben` und `strassensperre_entwurf_aus_pdf`; Löschen ist per MCP nicht verfügbar. Erkennt das Anlegen eine wahrscheinliche Verlängerung, Änderung oder Dublette (gleicher Abschnitt mit überlappendem oder bis zu 14 Tage anschließendem Zeitraum oder gleiches Aktenzeichen), liefert es `possible_update` mit Kandidaten und fertigen `felder` für `strassensperre_aktualisieren`. Alternativ ersetzt `ersetzt_road_closure_id` die alte Sperre (sie wird deaktiviert und verlinkt), oder `als_neu_bestaetigt=true` legt bewusst zusätzlich an (`duplikat_bestaetigt` bleibt als Alias). Anlegen und relevante Adressänderungen liefern zusätzlich `adressvalidierung` mit dem OSM-Prüfstatus. Empfohlener Ablauf für eine behördliche Verordnung: `strassensperre_entwurf_aus_pdf` → Felder prüfen → `strassensperre_anlegen` (ermittelt den Abschnitt aus OSM) → bei Qualität ≠ hoch `strassensperre_geometrie_bestaetigen` → `strassensperre_dokument_uebergeben`. Erlaubte Werte liefert `strassensperren_kataloge`.

## Reverse-Proxy und Konfiguration

Für `/mcp` ist Streaming erforderlich: `proxy_buffering off` sowie ausreichend hohe `proxy_read_timeout`- und `proxy_send_timeout`-Werte setzen. Das mitgelieferte [`deploy/nginx-snippet.conf`](../../deploy/nginx-snippet.conf) enthält die `/mcp`-Konfiguration. Die OAuth-Discovery und OAuth-Endpunkte dürfen nicht umgeleitet, geblockt oder von einem Login-Gateway abgefangen werden: `/.well-known/oauth-*`, `/authorize`, `/token`, `/register` und `/revoke` müssen die App erreichen.

| Wert | Standard | Zweck |
|------|----------|-------|
| `MCP_LOGIN_RATELIMIT` | `5/15minutes` | IP-basiertes Limit für `POST /mcp/anmelden` |
| `MCP_MAX_UPLOAD_BYTES` | 8 MB | Maximale dekodierte PDF-Größe bei Übergabe als Base64 |
| `MCP_UPLOAD_MAX_BYTES` | 50 MB | Maximale PDF-Größe beim Upload per Upload-Link (zusätzlich begrenzt durch `objekt_pdf_max_bytes`) |
| `MCP_UPLOAD_TOKEN_MINUTEN` | 15 | Gültigkeit eines Upload-Tokens |
| `MCP_UPLOAD_RETENTION_STUNDEN` | 24 | Nicht übergebene Uploads werden danach automatisch gelöscht |
| `MCP_UPLOAD_RATELIMIT` | `20/minute` | Limit für `POST /api/mcp/uploads/{upload_id}` |
| `MCP_DOWNLOAD_TOKEN_MINUTEN` | 15 | Gültigkeit eines Download-Links für Objektdokumente |
| `MCP_DOWNLOAD_INLINE_MAX_BYTES` | 8 MB | Bis zu dieser Größe liefert `objekt_dokument_herunterladen(inline=true)` das PDF zusätzlich als Base64 |
| `MCP_DOWNLOAD_RATELIMIT` | `30/minute` | Limit für `GET /api/mcp/downloads/{token}` |
| `MCP_LOGO_INLINE_MAX_BYTES` | 2 MB | Bis zu dieser Größe liefert `organisation_lesen` das Organisationslogo als Bild |
| Access-Token | 1 Stunde | Gültigkeit des Zugriffstokens |
| Refresh-Token | 30 Tage | Gültigkeit des Erneuerungstokens |
| Autorisierungscode | 5 Minuten | Gültigkeit des Login-Vorgangs |

### Dokument-Upload per curl

Große PDFs übergibt der KI-Client nicht als Base64, sondern zweistufig: `objekt_dokument_upload_vorbereiten` liefert `upload_id`, `upload_url`, einen einmaligen `upload_token` und ein fertiges curl-Beispiel; der Rechner des Benutzers lädt die Datei mit `curl -X POST -H "Authorization: Bearer <token>" -F "datei=@<pfad>" <upload_url>` hoch, danach übergibt `objekt_dokument_uebergeben(upload_id=...)` das PDF. Der Endpunkt `POST /api/mcp/uploads/{upload_id}` braucht keine Sitzung; er ist ausschließlich über das Upload-Token geschützt (nur Hash gespeichert, einmalig nutzbar, an Organisation, Benutzer und Objekt gebunden, 15 Minuten gültig, nur PDF).

Voraussetzungen im Betrieb:

- `/api/mcp/uploads/` muss von außen erreichbar sein und darf nicht hinter Basic-Auth oder einem Login-Gateway liegen.
- Der Reverse-Proxy muss dort Bodys bis mindestens 50 MB zulassen (`client_max_body_size 55m`). Beide Vorlagen unter `deploy/` enthalten den passenden `location`-Block.
- Ein periodischer Job löscht nicht übergebene Uploads nach 24 Stunden samt Datei (Ablage unter `OBJEKT_MEDIA_DIR/_mcp_uploads/`).

Poppler ist weiterhin für Seitenvorschauen von übergebenen PDFs notwendig. Tesseract wird für MCP-Dokumente nicht benötigt: Text und Klassifizierung werden fertig übergeben, daher startet das Einsatzcockpit weder OCR noch KI-Analyse. Nur wenn `seiten[]` fehlt oder unvollständig ist, wird für die fehlenden Seiten – sofern die KI-Klassifizierung der Organisation aktiv ist – ein KI-Vorschlag erzeugt; die Antwort weist das als `klassifizierung_quelle: "server"` aus.

## Sicherheitsmodell

- Nur öffentliche PKCE-Clients (`token_endpoint_auth_method=none`) dürfen sich dynamisch registrieren.
- OAuth-Codes und Access-/Refresh-Tokens liegen ausschließlich gehasht in der Datenbank. Beim Refresh wird die bisherige Token-Familie widerrufen.
- Benutzer, Organisation, Modulstatus und Rollen werden bei jedem Tool-Aufruf live geladen. Rechteentzug, Deaktivierung oder Sperre wirkt daher sofort.
- Die MCP-Anmeldung verwendet Benutzername und Passwort, den vorhandenen Login-Lockout und das eigene Rate-Limit. Ein reines SSO-Konto kann sich hier nicht anmelden.
- Geräte-Benutzer sind ausgeschlossen. Alle Tool-Abfragen bleiben auf die Organisation des Tokens beschränkt; sensible Klartextfelder werden nicht ausgegeben.

Schreibvorgänge erzeugen Audit-Einträge mit `objekt.mcp_*` beziehungsweise `kontakt.mcp_*`, etwa `objekt.mcp_angelegt`, `objekt.mcp_aktualisiert`, `objekt.mcp_dokument_übergeben`, `kontakt.mcp_angelegt` und `kontakt.mcp_aktualisiert`.

Zentrale Kontakte dürfen per MCP angelegt, aktualisiert, archiviert und zusammengeführt werden. Das Archivieren eines Kontakts mit Objektzuordnungen erfordert `bestaetigt=true`; das Zusammenführen erfordert dies immer. SMS- und Mail-Freigaben je Objektkontakt sowie der SMS-Versand bleiben ausschließlich im Einsatzcockpit. Kontakt-Import und -Export stehen ebenfalls nur in der Oberfläche zur Verfügung.

## Verbindungen trennen und Fehlersuche

Benutzer sehen verbundene Clients im **Profil** und können sie dort einzeln trennen. Das widerruft die zugehörige Token-Familie; die KI-Anwendung muss sich danach erneut anmelden.

Bei Problemen zuerst prüfen: HTTPS und `PUBLIC_BASE_URL`, die beiden MCP-Flags, das passende Fachmodul und die Rolle des Benutzers. Bei Discovery- oder Verbindungsfehlern Proxy-Logs auf nicht durchgereichte `/.well-known`- bzw. OAuth-Routen und auf Buffering bei `/mcp` kontrollieren. Bei fehlenden Werkzeugen sind meist Rolle oder Fachmodul die Ursache. Bei PDF-Fehlern Base64, das 8-MB-Limit, PDF-Gültigkeit, Seitenzahl und die Poppler-Installation prüfen.

### Dokument-Download

`objekt_dokument_herunterladen` liefert für ein Objektdokument (jede Version, auch wartende Dokumente und archivierte Objekte; optional nur eine Einzelseite) einen signierten Link `GET /api/mcp/downloads/{token}`. Der Link braucht keine Sitzung, ist 15 Minuten gültig und im Browser klickbar. Beim Abruf werden Benutzer, Rolle `objekt_verwalter`, MCP- und Objekt-Modul erneut geprüft – ein Rechteentzug wirkt also sofort, auch auf schon ausgegebene Links. Die Antwort ist `Cache-Control: no-store`. Das Ausstellen eines Links wird als `objekt.mcp_download_vorbereitet` im Audit-Log festgehalten. Für den Pfad ist im Reverse-Proxy keine Sonderkonfiguration nötig.
