# MCP-Server (Administration)

Der MCP-Server verbindet freigegebene KI-Anwendungen mit Einsatzcockpit. Er stellt je nach Rolle Werkzeuge für Objektpflege, Objekt-Dokumente und das Fahrtenbuch bereit. Die Verbindung verwendet OAuth; sie ist keine REST-API und kein Ersatz für die Freigabe im Einsatzcockpit.

## Voraussetzungen und Aktivierung

Die Instanz braucht eine von außen erreichbare HTTPS-URL. `PUBLIC_BASE_URL` muss auf genau diese öffentliche Basis-URL zeigen: Sie wird als OAuth-Issuer und für die MCP-Ressource verwendet. Reverse-Proxies müssen das originale Host- und HTTPS-Protokoll weitergeben.

MCP ist zweistufig aktiviert:

1. System-Admin: `/admin/settings` -> **KI-Anwendungen (MCP) systemweit aktivieren**. Das setzt `mcp_module_enabled`.
2. Org-Admin: im selben Bereich **MCP für diese Organisation aktivieren**. Das setzt `mcp_modul_aktiv`; der Schalter ist erst bei aktivem System-Flag verfügbar.

Zusatzlich gilt das jeweilige Fachmodul: Objekt-Werkzeuge brauchen die aktivierte Objektverwaltung, Kontakt-Werkzeuge das Kontakte-Modul und Fahrtenbuch-Werkzeuge das aktivierte Fahrtenbuch. Deaktivierte Werkzeuge werden nicht angeboten.

### GSL-Ressourcen

Bei aktivem GSL-Ressourcenmodul (`mi_feature_ressourcen`) stehen folgende Werkzeuge für Lagen der eigenen Organisation zur Verfügung. Lesen dürfen `incident_leader`, `admin`, `recorder` und `readonly`; Änderungen dürfen `incident_leader`, `admin` und `recorder`.

| Werkzeug | Zweck | Rolle |
|---|---|---|
| `gsl_ressourcen_liste` | Ressourcen einer Lage auflisten | Lesen |
| `gsl_ressource_details` | Kartendetails, Einsätze, Journal, Personal und Ausstattung lesen | Lesen |
| `gsl_ressource_aktualisieren` | Status, Abschnitt und Stammdaten ändern | Ändern |
| `gsl_ressource_fuehrer_setzen` | Gruppenkommandant oder Stellvertretung setzen | Ändern |
| `gsl_ressource_zugang_senden` | Zugang nach Bestätigung ausschließlich per SMS senden | Ändern |
| `gsl_ressource_zugang_widerrufen` | Gruppenkommandanten-Zugang widerrufen | Ändern |
| `gsl_ressource_journal` | Ressourcenjournal lesen oder manuellen Eintrag erfassen | Lesen / Ändern |
| `gsl_ressource_personal` | Personal lesen oder mit den vorhandenen Service-Aktionen pflegen | Lesen / Ändern |
| `gsl_ressource_ausstattung` | Ausstattung lesen oder mit den vorhandenen Service-Aktionen pflegen | Lesen / Ändern |
| `gsl_ressource_anlegen` | Neue Einheit samt optionalem GK, Personal und Autodruck anlegen | Ändern |
| `gsl_einheit_disponieren` | Einheit zu einer Einsatzstelle disponieren | Ändern |
| `gsl_auftrag_aendern` | Auftrag oder Reihenfolge einer Disposition ändern | Ändern |
| `gsl_auftrag_zurueckziehen` | Einheit von einer Einsatzstelle abziehen | Ändern |
| `gsl_einsatz_abschliessen` | Einsatzstelle abschließen | Einsatzleitung/Admin |
| `gsl_einsatz_wiedereroeffnen` | Abgeschlossene Einsatzstelle wieder öffnen | Einsatzleitung/Admin |
| `gsl_ressource_qr` | QR-Zugangstatus lesen oder QR-Zugang widerrufen; keine Ausgabe von Token, Link oder PIN | Lesen / Ändern |

Bei der Disposition kann die Auftragsart Erkundung oder Abarbeitung angegeben werden. Ohne Angabe ergibt die Phase Eingegangen/Erkundung eine Erkundung, sonst eine Abarbeitung. Das Abschließen kann aktive Dispositionen nur mit dem Parameter `aktive_zurueckziehen=true` zurückziehen; das Wiedereröffnen ist eine Verwaltungsaktion.

MCP-Antworten enthalten niemals Zugangstoken, Links, Token-Hashes oder PINs. Der SMS-Versand liefert nur Versandstatus und -protokoll. Telefonnummern sind für reine Leser maskiert. Schreibvorgänge benötigen eine aktive Lage. QR-Zugänge können über MCP nur abgefragt oder widerrufen, nicht ausgestellt, angezeigt oder gedruckt werden.

Ist das Straßensperren-Modul aktiviert, erhalten alle Benutzer die lesenden Tools `strassensperren_liste`, `strassensperre_lesen`, `strassensperren_kataloge`, `strassensperren_suchen`, `strassensperren_im_gebiet`, `strassensperren_kennzahlen`, `einsatz_strassensperren`, `einsatz_anfahrtsroute_pruefen` und `strassensperren_entlang_route`. `objekt_verwalter` erhalten zusätzlich `strassensperre_anlegen`, `strassensperre_aktualisieren`, `strassensperre_deaktivieren`, `strassensperre_reaktivieren`, `strassensperre_beenden`, `strassensperre_teams_senden`, `strassensperre_freigabelink`, `strassensperre_geometrie_ermitteln`, `strassensperre_geometrie_bestaetigen`, `strassensperre_dokument_upload_vorbereiten`, `strassensperre_dokument_uebergeben` und `strassensperre_entwurf_aus_pdf`; Löschen ist per MCP nicht verfügbar. Erkennt das Anlegen eine wahrscheinliche Verlängerung, Änderung oder Dublette (gleicher Abschnitt mit überlappendem oder bis zu 14 Tage anschließendem Zeitraum oder gleiches Aktenzeichen), liefert es `possible_update` mit Kandidaten und fertigen `felder` für `strassensperre_aktualisieren`. Alternativ ersetzt `ersetzt_road_closure_id` die alte Sperre (sie wird deaktiviert und verlinkt), oder `als_neu_bestaetigt=true` legt bewusst zusätzlich an (`duplikat_bestaetigt` bleibt als Alias). Anlegen und relevante Adressänderungen liefern zusätzlich `adressvalidierung` mit dem OSM-Prüfstatus. Empfohlener Ablauf für eine behördliche Verordnung: `strassensperre_entwurf_aus_pdf` → Felder prüfen → `strassensperre_anlegen` (ermittelt den Abschnitt aus OSM) → bei Qualität ≠ hoch `strassensperre_geometrie_bestaetigen` → `strassensperre_dokument_uebergeben`. Erlaubte Werte liefert `strassensperren_kataloge`.

Für Katastrophenpläne müssen MCP und das Katastrophenplan-Modul auf System- und Organisationsebene aktiv sein. Die folgenden Werkzeuge verlangen `KATPLAN_LESE_ROLLEN` (`readonly`, `recorder`, `breathing_supervisor`, `incident_leader`, `objekt_verwalter`, `katplan_verwalter`, `katplan_freigeber`; einschließlich Admins).

| Werkzeuge | Zugriff |
|-----------|---------|
| `katplan_liste`, `katplan_lesen` | Sichtbare Pläne, Kopf und Gliederung |
| `katplan_suchen`, `katplan_orte_suchen` | Text- und Umkreissuche |
| `katplan_elemente`, `katplan_element_lesen` | Strukturierte Elemente |
| `katplan_quellseite_bilder` | Sichtbare Quellen wie Dokumentauszug; maximal 20 eingebettete Bilder (ab 200 px), optional 8 JPEG-Vorschauen bis 512 px |
| `katplan_dokument_auszug` | Quellseiten und kurzlebige PDF-Downloads mit erneuter Live-Rechteprüfung |
| `katplan_verknuepfungen` | Live-Links sowie ausgehende und eingehende Beziehungen |

Standard ist die freigegebene Fassung; Arbeitsstände sind nur für Planbearbeiter mit `fassung="arbeitsstand"` verfügbar. Vertrauliches sehen nur Planverwalter, Planfreigeber, Einsatzleiter und Admins. Ohne Kontaktverzeichnisrecht werden ausschließlich Dienstnummern einsatzrelevanter Kontakte angezeigt. Objektdetails erfordern zusätzlich das Objektmodul und dessen Leserecht. Alle erfolgreichen Katplan-Toolaufrufe werden mit `mcp.katplan_*` auditiert; Freigabewerkzeuge gibt es nicht.

Quellreferenzen liefern höchstens 30 Seiten mit geschützten Bild-, Thumbnail- und Text-URLs.
Bei Freigaben bleibt die URL über `version_id` an diese Fassung gebunden. Arbeitsquellen
sind nur für Bearbeiter sichtbar; das Bilderwerkzeug verwendet dieselbe Sichtbarkeit wie
`katplan_dokument_auszug`.

Schreibende Werkzeuge verlangen `katplan_verwalter`, `katplan_freigeber` oder Admin und ändern nur den Arbeitsstand:

| Werkzeuge | Zugriff |
|-----------|---------|
| `katplan_element_entwurf_anlegen`, `katplan_element_entwurf_aendern` | Elemente anlegen/ändern mit Versionsprüfung; Adresse als Objekt (`strasse`, `hausnummer`, `plz`, `ort`), optional `verorten` (Geokodierung, Ort der Organisation als Vorauswahl, immer ungeprüft) |
| `katplan_verknuepfung_setzen`, `katplan_verknuepfung_entfernen` | Idempotente Links auf Plan-/Elementebene, orggebundene Ziele, Telefonregel auch in Schreibantworten |
| `katplan_beziehung_setzen`, `katplan_beziehung_entfernen` | Elementbeziehungen per Kennung im Plan oder UUID innerhalb der Organisation |
| `katplan_stammdaten_aendern` | Teiländerungen am Plankopf mit `plan_version` aus dem Arbeitsstand, Kontakt nur aus eigener Organisation |
| `katplan_quellseite_verknuepfen` | Bis 50 Quellzuordnungen; optionale Elementversion, Fehler je Eintrag, `streng` für vollständigen Abbruch, `pruefen` für Rollback |
| `katplan_anhang_aus_quellseite` | Seite, eingebettetes Bild oder relativer Ausschnitt über Anhangspipeline mit Quota, Thumbnail und MCP-Protokoll; `pruefen` entfernt auch Dateien |
| `katplan_anhang_upload_vorbereiten`, `katplan_anhang_uebergeben` | Fotos/PDFs über die Anhangspipeline (MIME-Prüfung, Quota, Vorschaubild) |
| `katplan_vorschlaege_aus_dokument` | Strukturierte Vorschläge zu einer Quellseite in die Import-Prüfung (keine Elemente) |

Alle Schreibzugriffe werden im Planprotokoll mit Herkunft `mcp` und im Audit-Log (`mcp.katplan_…`) geführt. Kein Werkzeug kann einreichen, freigeben, archivieren oder freigegebene Versionen ändern.

Entwurfswerkzeuge melden mit `geaendert`, `unveraendert` (Ändern), `verworfen`,
`gespeichert` und `element` ihre Wirkung. `streng=true` rollt bei Datenverlust zurück;
`pruefen=true` berechnet einen vollständigen Vorschlag und rollt alle fachlichen Änderungen
inklusive neuer Kapitel, Phasen und Planprotokolle zurück. Der erfolgreiche Trockenlauf
erscheint weiterhin im Audit-Log. Für Versionsprüfungen `version` aus
`katplan_element_lesen(fassung="arbeitsstand")` verwenden, nicht `element_version_id`.
Zahlen in Typdaten werden als Text erhalten; `null`/`""` leeren Adressfelder,
`adresse=null` alle Adressfelder, `geometrie=null` den Ort und
`quelle_dokument_id=null` den Quellbezug samt nicht explizit übergebenen Seiten.

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

### Einsätze auslesen

Die lesenden Einsatz-Werkzeuge stehen allen Einsatzrollen ab `readonly` zur Verfügung; Admin- und Organisations-Admin-Rollen erfüllen diese Berechtigung ebenfalls. Sie liefern ausschließlich eigene und über `IncidentOrg` kollaborierende Einsätze der Token-Organisation. Auch System-Admins lesen damit nicht organisationsübergreifend.

`einsatz_objekte` und `einsatz_objekt_dokument_herunterladen` benötigen zusätzlich das aktivierte Objektmodul. Objekt-Dokumente sind nur für aktuelle, freigegebene Versionen eines mit dem Einsatz verknüpften Basisobjekts verfügbar; Arbeitskopien bleiben ausgeschlossen. Die beiden Einsatz-Download-Werkzeuge geben einen 15 Minuten gültigen Link aus. Dieser benötigt keine Sitzung, prüft beim Abruf aber Benutzerstatus, Rolle, Einsatzsichtbarkeit, Modulstatus und Dokument- beziehungsweise Medienzuordnung erneut.

`einsatz_auftrag_anlegen` und `einsatz_meldung_anlegen` benötigen `incident_leader` oder `recorder`; `admin`, `org_admin` und `system_admin` sind wie im Board ebenfalls berechtigt. Sie dürfen nur sichtbare, nicht abgeschlossene Einsätze ändern. Die Aktionen werden als `einsatz.mcp_auftrag_angelegt` beziehungsweise `einsatz.mcp_meldung_angelegt` im Audit-Log festgehalten.

Zentrale Kontakte dürfen per MCP angelegt, aktualisiert, archiviert und zusammengeführt werden. Das Archivieren eines Kontakts mit Objektzuordnungen erfordert `bestaetigt=true`; das Zusammenführen erfordert dies immer. SMS- und Mail-Freigaben je Objektkontakt sowie der SMS-Versand bleiben ausschließlich im Einsatzcockpit. Kontakt-Import und -Export stehen ebenfalls nur in der Oberfläche zur Verfügung.

## Verbindungen trennen und Fehlersuche

Benutzer sehen verbundene Clients im **Profil** und können sie dort einzeln trennen. Das widerruft die zugehörige Token-Familie; die KI-Anwendung muss sich danach erneut anmelden.

Bei Problemen zuerst prüfen: HTTPS und `PUBLIC_BASE_URL`, die beiden MCP-Flags, das passende Fachmodul und die Rolle des Benutzers. Bei Discovery- oder Verbindungsfehlern Proxy-Logs auf nicht durchgereichte `/.well-known`- bzw. OAuth-Routen und auf Buffering bei `/mcp` kontrollieren. Bei fehlenden Werkzeugen sind meist Rolle oder Fachmodul die Ursache. Bei PDF-Fehlern Base64, das 8-MB-Limit, PDF-Gültigkeit, Seitenzahl und die Poppler-Installation prüfen.

### Dokument-Download

`objekt_dokument_herunterladen` liefert für ein Objektdokument (jede Version, auch wartende Dokumente und archivierte Objekte; optional nur eine Einzelseite) einen signierten Link `GET /api/mcp/downloads/{token}`. Der Link braucht keine Sitzung, ist 15 Minuten gültig und im Browser klickbar. Beim Abruf werden Benutzer, Rolle `objekt_verwalter`, MCP- und Objekt-Modul erneut geprüft – ein Rechteentzug wirkt also sofort, auch auf schon ausgegebene Links. Die Antwort ist `Cache-Control: no-store`. Das Ausstellen eines Links wird als `objekt.mcp_download_vorbereitet` im Audit-Log festgehalten. Für den Pfad ist im Reverse-Proxy keine Sonderkonfiguration nötig.

Verknüpfungen und Beziehungen setzen sowie Plan-Stammdaten ändern unterstützen
`pruefen=true` (Rollback, `gespeichert=false`). Die Kopfdatenantwort enthält `plan`,
`plan_version`, `geaendert`, `unveraendert` und `verworfen`; unbekannte Felder werden
abgewiesen. Optionales kann mit `null` oder leer geleert werden, Titel und Gefahrenarten
bleiben Pflicht. Die gemeinsame Stammdatenlogik bewahrt Versionsprüfung, Feldprotokoll,
Wiedervorlage und automatische Überarbeitung auch für die Oberfläche. Ein neuer
Qualitätshinweis meldet Telefonnummern im Beschreibungstext trotz Kontaktverknüpfung.
