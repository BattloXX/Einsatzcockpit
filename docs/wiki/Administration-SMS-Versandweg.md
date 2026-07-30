# SMS-Versandweg administrieren

Je Organisation wird ein primaerer SMS-Provider und optional ein Fallback
gewaehlt. Ohne Konfiguration bleibt das bisherige SMS-Gateway primaer und es
gibt keinen Fallback.

Im EUS-Geraete-Login einen Zugang vom Typ `MessageSendApi` anlegen. Ueber das
Schluesselsymbol Client ID und Client Secret kopieren; das Secret ist nur bei
der Anlage sichtbar. Serveradresse ohne Pfad eintragen. OAuth 2.0 wird
empfohlen, Basic Authentication ist alternativ moeglich.

Der Fallback wird nur versucht, wenn der primaere Provider nicht erfolgreich
versendet. "Kein Fallback" verhindert bewusst jeden zweiten Versandversuch.

Fehlerbilder:

- HTTP 401/403: Secret falsch, abgelaufen oder Zugang gesperrt.
- Timeout: Serveradresse, Netzwerk, DNS und Firewall pruefen.
- EUS wird uebersprungen: Block aktivieren und Zugangsdaten vervollstaendigen.
