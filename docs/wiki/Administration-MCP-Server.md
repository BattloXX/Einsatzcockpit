# MCP-Server (Administration)

Der MCP-Server verbindet freigegebene KI-Anwendungen mit Einsatzcockpit. Er stellt je nach Rolle Werkzeuge für Objektpflege, Objekt-Dokumente und das Fahrtenbuch bereit. Die Verbindung verwendet OAuth; sie ist keine REST-API und kein Ersatz für die Freigabe im Einsatzcockpit.

## Voraussetzungen und Aktivierung

Die Instanz braucht eine von außen erreichbare HTTPS-URL. `PUBLIC_BASE_URL` muss auf genau diese öffentliche Basis-URL zeigen: Sie wird als OAuth-Issuer und für die MCP-Ressource verwendet. Reverse-Proxies müssen das originale Host- und HTTPS-Protokoll weitergeben.

MCP ist zweistufig aktiviert:

1. System-Admin: `/admin/settings` -> **KI-Anwendungen (MCP) systemweit aktivieren**. Das setzt `mcp_module_enabled`.
2. Org-Admin: im selben Bereich **MCP für diese Organisation aktivieren**. Das setzt `mcp_modul_aktiv`; der Schalter ist erst bei aktivem System-Flag verfügbar.

Zusatzlich gilt das jeweilige Fachmodul: Objekt-Werkzeuge brauchen die aktivierte Objektverwaltung, Kontakt-Werkzeuge das Kontakte-Modul und Fahrtenbuch-Werkzeuge das aktivierte Fahrtenbuch. Deaktivierte Werkzeuge werden nicht angeboten.

## Reverse-Proxy und Konfiguration

Für `/mcp` ist Streaming erforderlich: `proxy_buffering off` sowie ausreichend hohe `proxy_read_timeout`- und `proxy_send_timeout`-Werte setzen. Das mitgelieferte [`deploy/nginx-snippet.conf`](../../deploy/nginx-snippet.conf) enthält die `/mcp`-Konfiguration. Die OAuth-Discovery und OAuth-Endpunkte dürfen nicht umgeleitet, geblockt oder von einem Login-Gateway abgefangen werden: `/.well-known/oauth-*`, `/authorize`, `/token`, `/register` und `/revoke` müssen die App erreichen.

| Wert | Standard | Zweck |
|------|----------|-------|
| `MCP_LOGIN_RATELIMIT` | `5/15minutes` | IP-basiertes Limit für `POST /mcp/anmelden` |
| `MCP_MAX_UPLOAD_BYTES` | 8 MB | Maximale dekodierte PDF-Größe bei MCP-Dokumenten |
| Access-Token | 1 Stunde | Gültigkeit des Zugriffstokens |
| Refresh-Token | 30 Tage | Gültigkeit des Erneuerungstokens |
| Autorisierungscode | 5 Minuten | Gültigkeit des Login-Vorgangs |

Poppler ist weiterhin für Seitenvorschauen von übergebenen PDFs notwendig. Tesseract wird für MCP-Dokumente nicht benötigt: Text und Klassifizierung werden fertig übergeben, daher startet das Einsatzcockpit weder OCR noch KI-Analyse.

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
