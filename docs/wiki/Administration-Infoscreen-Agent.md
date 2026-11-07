# Infoscreen-Agent: Geraete, Displays und Plaene

[Zurueck zur Verwaltungsnavigation](Administration-Navigation)

Unter **Verwaltung > Infoscreens > Geraete & Plaene** verwalten Org-Admins die
Monitor-PCs ihrer Organisation. `admin` und `system_admin` verwenden dieselben
bestehenden Verwaltungsrechte; andere Rollen haben keinen Zugriff.

## Geraete koppeln

1. Geraetename eingeben und **Geraet hinzufuegen** waehlen.
2. Der Dialog zeigt einen einmaligen Pairing-Code (15 Minuten gueltig) und einen
   kopierbaren Installationsbefehl. **Einzeiler kopieren** verwendet die Zwischenablage;
   falls diese nicht verfuegbar ist, wird der Text zum manuellen Kopieren markiert.
3. Die Variante **EC_PAIRING_CODE** uebergibt den Code als Umgebungsvariable statt
   als Bash-Argument. Die eingefuegte Zeile kann weiterhin in der Shell-History stehen.
4. Bei Bedarf **Neuen Code erzeugen** waehlen; der vorige Code wird ungueltig.
5. Der offene Dialog fragt alle 5 Sekunden den Status ab und zeigt nach dem Pairing
   **gekoppelt**, bei aktiver Verbindung **verbunden**. Schliessen beendet das Polling.

**Installer noch nicht freigeschaltet:** S4 bereitet den Dialog vor. Der
`install.sh`-Endpunkt und die Paketauslieferung entstehen in S6. Der Banner bleibt
sichtbar, solange `INFOSCREEN_AGENT_INSTALLER_ENABLED=false` (Standard).
`INFOSCREEN_AGENT_KEY_FINGERPRINT` ist optional und standardmaessig leer; nur ein
konfigurierter Wert erscheint als `--key-fingerprint`. Der Server stammt aus
`PUBLIC_BASE_URL` (Fallback `APP_BASE_URL`), niemals aus dem Browser-Host-Header.

Die Liste zeigt Presence bzw. einen Heartbeat der letzten drei Minuten als online,
ausser das Geraet ist gesperrt. OS, Version und letzter Heartbeat sowie gemeldete
Seiten-, Monitor- und Backendzustaende helfen bei der Diagnose. "Bestaetigt" bezieht
sich auf den Rueckkanal der Monitorsteuerung. Die Update-Diagnose folgt in S6.

**Sperren** widerruft Zugangsdaten und trennt bestehende Verbindungen.
**Entsperren** und **Token rotieren / neu koppeln** erzeugen einen neuen Pairing-Code.
Ein neuer Geraete-Token wird nie im Browser angezeigt. Loeschen verlangt eine
Bestaetigung und entfernt auch die Displays des Geraets. Pairing-Code-Erzeugung
wird ohne den Code protokolliert.

## Displays

Ein Geraet kann mehrere Displays mit eindeutigen Display-Keys besitzen. Im
Unterbereich **Displays** lassen sich Name, eigener Alarm-Infoscreen-Token,
Zeitplan, Monitorsteuerung, Nachlauf (0-1440 Minuten), Offline-Aufwecken
(1-1440 Minuten, leer = deaktiviert), GSL-Override-Erlaubnis und Aktivierung setzen.
Ein Alarm-Token kann nur einem Display zugeordnet werden; belegte Tokens sind
gekennzeichnet. Tokens und Plaene anderer Organisationen sind nicht waehlbar.

## Zeitplaene

Jeder Plan hat Name, IANA-Zeitzone (z. B. `Europe/Vienna`) und Ruhemodus.
Slots gelten an den angekreuzten Wochentagen (Mo-So). Start und Ende sind lokale
Wanduhrzeiten in der **Plan-Zeitzone**, auch wenn die Organisation eine andere
Zeitzone verwendet. Ende vor Start bedeutet ueber Mitternacht; Ende gleich Start
ist ungueltig. Sommer-/Winterzeit verarbeitet der vorhandene Server-Resolver.

Jeder Slot legt Monitor an/aus, Prioritaet, Label und optional einen Datumsbereich
fest. Eine Rotation besteht aus eigenen aktiven Infoscreen-URLs oder dem eingebauten
Wetter. Ueber die Seitenauswahl und **Seite anhaengen** werden Eintraege angefuegt;
Reihenfolge, Aendern und Entfernen sind direkt an jeder Rotationszeile moeglich.

Verweildauer: 5-3600 Sekunden. Maximal 30 Rotationseintraege, 200 Slots und 366
Ausnahmetage je Plan. Ueberlappungen gleicher Prioritaet erzeugen eine Warnung;
sie werden nur mit ausdruecklicher Bestaetigung gespeichert. Hoehere Prioritaet
gewinnt, bei Gleichstand entscheidet der bestehende Resolver nach Startzeit.
Das Wochenraster zeigt die Wochentage in sieben Spalten mit 24-Stunden-Zeitachse;
Ausnahmen und Gueltigkeitsbereiche stehen in den zugehoerigen Formularen.

**Ausnahmetage** ersetzen den normalen Plan fuer ein Datum vollstaendig. Eine
leere Ersatz-Slot-Liste bedeutet **ganzer Tag aus**. Mit **Ersatz-Slot hinzufuegen**
werden Start, Ende, Monitorzustand, Prioritaet, Label und Rotation wie bei normalen
Slots bearbeitet. So kann etwa ein Feiertag nur von 08:00 bis 12:00 Uhr
Wetter anzeigen und den Monitor danach ausschalten.

Auch bei Ersatz-Slots bleiben Luecken aus. Eigene aktive URLs sind in der
Seitenauswahl verfuegbar. Zum Bearbeiten einen vorhandenen Slot oder Ausnahmetag aufklappen.

## Vorschau

Unter **Was zeigt Monitor X?** einen Monitor, Wochentag und Uhrzeit waehlen.
Die Vorschau verwendet den naechsten passenden Wochentag in der Plan-Zeitzone
und beruecksichtigt auch aktuelle Einsatz-/GSL-Aktivitaet. Sie zeigt Modus,
Monitorzustand, Grund und Rotation. Sie speichert nichts und erhoeht weder
Revision noch Konfigurationsversion.

Nach gespeicherten Aenderungen aktualisiert sich die Verwaltung ohne F5.
Betroffene Geraete erhalten eine neue Konfigurationsversion und einen Wake-Hinweis.
Overrides, Releases, Updateverwaltung und Installer-Endpunkte folgen in S5/S6.

## Uebersteuerung und URL-Allowlist (S5)

Im Unterbereich **Uebersteuerung** koennen Org-Admins Seiten fuer ein Display oder
alle Displays der eigenen Organisation planen. Von/bis beziehen sich auf die
Org-Zeitzone; Ablauf ist Pflicht, maximal 8 Stunden. Die Begruendung ist Pflicht,
auch beim Aufheben. Keine Einsatzdetails oder personenbezogenen Angaben eingeben.
Aktuelle, geplante und abgelaufene/aufgehobene Eintraege stehen getrennt in der Liste.

**GSL-Seite** wirkt nur bei aktiver GSL und fuer Displays mit erlaubter
GSL-Uebersteuerung. Mit dem GSL-Ende endet sie dauerhaft. Ein neuer regulaerer
Einsatz unterbricht sie bei aktiver GSL nicht. **Manuell** wirkt erst nach GSL,
Einsatz und Nachlauf. Schnellaktionen **Wetter fuer 30 min / 2 h** brauchen dieselbe
Begruendung. Die Live-Anzeige zeigt alle 15 Sekunden die serverseitige Sollwirkung;
sie veraendert keine Revision. Heartbeat-Diagnose zeigt separat den Geraetezustand.

Wetter und Statistik verwenden bestehende Infoscreen-Tokens als Referenz; bei
Wetter darf die Referenz leer bleiben, um das konfigurierte Wetter zu verwenden.
Die Statistik muss aktiviert sein. Lagekarte/Einsatzstellen werden mangels eigener
tokenbasierter Infoscreen-Seite noch nicht angeboten.

Fuer **Freigegebene URL** zuerst den exakten Host unter **URL-Allowlist** freigeben.
Nur HTTPS auf Port 443 ohne Zugangsdaten, IP-Adressen oder interne Ziele ist erlaubt.
Unicode-Hosts werden in Punycode gespeichert. DNS muss beim Freigeben und beim
Anlegen des Overrides ausschliesslich oeffentliche Ziele ergeben. DNS-Fehler werden
angezeigt; es wird nichts gespeichert. Entfernen eines Hosts beendet die Wirkung
der zugehoerigen Custom-Overrides. Abgelaufene Eintraege werden nach 30 Tagen
bereinigt; Anlegen und Aufheben bleiben im Audit protokolliert.

Details: [Entscheidungsmatrix und Sicherheitsgrenzen](../architecture/infoscreen-agent-overrides.md).
