# Infoscreen-Agent

Der Infoscreen-Agent macht einen Ubuntu-PC zum Kiosk für vorhandene ECP-Infoscreens.
Er steuert Browser, Monitorstrom und Updates. Einsatz- und GSL-Inhalte rendert
weiterhin das Einsatzcockpit; der Agent baut keine eigene Einsatzanzeige.

```text
Org-Admin -> ECP: Displays, Zeitpläne, Übersteuerungen
                 | HTTPS: Konfiguration und aktueller Sollzustand
                 | WebSocket: Hinweis auf Änderungen
                 v
           Ubuntu-Agent -> Kiosk-Browser -> vorhandene ECP-Infoscreen-Seiten
                 | Monitorsteuerung
                 + HTTPS: technischer Heartbeat zurück zum ECP
```

| Begriff | Bedeutung |
| --- | --- |
| Gerät | Gekoppelter Ubuntu-PC mit eigener Identität, Kanal und Update-Policy |
| Display | Einzelner Monitor eines Geräts mit eigenem Alarm-Infoscreen-Token und optionalem Plan |
| Zeitplan | Wochen-Slots und Ausnahmetage in einer festgelegten Zeitzone; regelt Strom und Seitenrotation |
| Override / Übersteuerung | Zeitlich begrenzte, begründete Seitenauswahl durch einen Org-Admin |
| Release | Signiertes Agent-Paket samt Installerskript, durch Sysadmin freigegeben |
| Sollzustand | Entscheidung des ECP-Resolvers für jetzt; noch keine Bestätigung der Hardware |
| Heartbeat | Letzte technische Rückmeldung des Agenten; kann bei Verbindungsproblemen veraltet sein |

GSL, regulärer Einsatz und Nachlauf können den Ruheplan unterbrechen. Eine erlaubte
GSL-Seitenübersteuerung hat bei aktiver GSL Vorrang. Bei fehlendem Plan bleibt der
Monitor an. Physisches Ausschalten hängt vom gemessenen Hardware-Rückkanal ab;
`logical_blank` zeigt nur Schwarz und bestätigt keinen Standby.

- [Installation](Infoscreen-Agent-Installation): Voraussetzungen und Einzeiler
- [Betrieb](Infoscreen-Agent-Betrieb): Status, Prioritäten, Updates und Störungen
- [Sicherheit](Infoscreen-Agent-Sicherheit): Zugangsdaten, Signaturen und Datenschutz
- [Geräte & Pläne verwalten](Administration-Infoscreen-Agent): genaue Formularbedienung
- [Öffentliches Agent-Repository](https://github.com/BattloXX/Einsatzcockpit-Infoscreen-Agent): Quellcode und technische Agent-Anleitungen

Die Hardware-Abnahme und der Pilot am tatsächlichen Bildschirm bleiben notwendig.
Eine grüne Serveranzeige allein bestätigt weder Aufwachzeit noch ein zuverlässiges
physisches Monitor-Aus.
