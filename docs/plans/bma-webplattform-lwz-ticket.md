# LWZ-Ticket: Maschinenzugang für den BMA-Webplattform-Import

## Hintergrund

Der Login der BMA-Webplattform (`dibos.lwz-vorarlberg.at/LWZ_BMA_Webplattform`) läuft
über das DIBOS-Portal und hat dort eine Anti-Roboter-Verifizierung (Friendly Captcha).
Ein automatisierter, täglicher Abruf kann sich damit nicht selbst anmelden. Aktuell
(siehe [`docs/wiki/Administration-Objektverwaltung.md`](../wiki/Administration-Objektverwaltung.md#bma-webplattform-import-landeswarnzentrale-vorarlberg))
wird stattdessen ein manuell im Browser erzeugtes Session-Cookie hinterlegt — funktional,
aber wartungsintensiv, weil unklar ist, wie lange eine Session gültig bleibt.

Die Org hat mit dem DIBOS-EventHub-Servicekonto (`app/services/dibos/`) bereits eine
maschinelle Anbindung an ein anderes LWZ-System. Ziel dieses Tickets: einen ähnlichen,
offiziellen Zugang für die BMA-Webplattform zu bekommen, damit `bma_client.py` von
Cookie-basiertem Login auf ein echtes Servicekonto umgestellt werden kann — der Rest der
Pipeline (Parser, Abgleich, Review-Queue, Datenmodell) bleibt dabei unverändert.

## Ticket-Text (Vorlage, über "Ticket an LWZ erstellen" in der BMA-Webplattform oder
per Mail an die LWZ-IKT-Kontaktstelle)

> **Betreff:** Anfrage Maschinenzugang BMA-Webplattform (automatisierter Datenabgleich)
>
> Wir pflegen für unsere Feuerwehr eine eigene Einsatzleitsoftware (Einsatzcockpit) und
> möchten die dort geführten Objektdaten (Brandmeldeanlagen, Kontaktpersonen) automatisiert
> mit der BMA-Webplattform abgleichen, um Doppelpflege und veraltete Kontaktdaten im
> Einsatzfall zu vermeiden.
>
> Der reguläre Login der BMA-Webplattform läuft über das DIBOS-Portal und hat dort eine
> Anti-Roboter-Verifizierung — ein automatisierter, täglicher Abruf ist damit technisch
> nicht möglich. Für unsere DIBOS-EventHub-Anbindung (Elvis-Tracing) nutzen wir bereits
> ein von Ihnen vergebenes Servicekonto (Gateway-Konto + Org-Servicekonto,
> WS-Security-UsernameToken).
>
> Gibt es für die BMA-Webplattform einen vergleichbaren, offiziellen maschinellen
> Zugangsweg (z. B. ein API-Token, ein von der Anti-Roboter-Prüfung ausgenommenes
> Servicekonto, oder ein regelmäßiger Datenexport)? Benötigt würden lesend:
> - die Anlagenliste (BMA-Nummer, Bezeichnung, Adresse, RFL-Status)
> - die Kontaktpersonen je Anlage (Brandschutzbeauftragte(r), BMA Alarmperson)
>
> Der Zugriff wäre rein lesend, einmal täglich, für unsere eigene Organisation.

## Nach Rückmeldung der LWZ

- **Offizieller Maschinenzugang verfügbar:** `app/services/bma_import/bma_client.py`
  auf den neuen Auth-Mechanismus umstellen (analog `dibos_client.py`), Zugangsdaten in
  `OrgBmaImportConfig` ergänzen (Fernet-verschlüsselt, Muster `OrgDibosConfig`). Parser,
  Sync-Logik, Datenmodell, UI bleiben unverändert — der Cookie-Login in
  `settings_bma_import.html` entfällt oder bleibt als Fallback.
- **Kein offizieller Zugang, aber Datenexport (Excel/CSV):** Rückfallweg laut
  ursprünglichem Plan — `bma_parser.py` ist quellenagnostisch geschnitten, ein
  Datei-Upload-Endpoint (Muster: syBOS-Mannschaftsimport, `ui_admin.py`) könnte dieselbe
  `parse_anlagen`/`parse_kontakte`-Logik auf eine hochgeladene Datei statt auf die
  Live-API anwenden.
- **Kein Zugang möglich:** Cookie-Login bleibt der einzige Weg — Keepalive-Intervall
  (`BMA_IMPORT_KEEPALIVE_INTERVAL_S`) ggf. nachjustieren, sobald die tatsächliche
  Session-Lebensdauer aus dem Betrieb bekannt ist.
