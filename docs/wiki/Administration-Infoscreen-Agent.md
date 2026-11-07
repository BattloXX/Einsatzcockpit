# Infoscreen-Agent: Geraete, Displays und Plaene

## Repo-Modus (Standard seit S9)

Standard ist `INFOSCREEN_AGENT_DISTRIBUTION=github`: Das ECP steuert nur Freigabe und Zeitpunkt. Unter **Updates & Backups -> Agent-Releases** legt der Sysadmin eine Version (Kanal/Arch) an oder gibt eine automatisch erkannte Version mit 10/50/100 % frei. Ruecknahme auf 0, Sperre und critical sind dort ebenfalls moeglich. Die Tabelle zeigt erste Sichtung und Geraeteanzahl. Aenderungen senden einen Update-Hinweis und schreiben Audit. Schluessel/Dateiimport/Upload-Tokens sind unter **Verwalteter Modus (optional)** eingeklappt. Mit `managed` bleiben diese sichtbar und die bisherige Installation bleibt aktiv.

## Bisheriger Ablauf und weitere Details

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

**Installer-Freigabe:** Ein bestaetigter aktiver Release-Schluessel, ein gueltiges
Skript und ein freigegebenes stable-Release lassen den Banner verschwinden.
Der Altweg ohne DB-Schluessel braucht `INFOSCREEN_AGENT_INSTALLER_ENABLED=true`.
`INFOSCREEN_AGENT_KEY_FINGERPRINT` hat Vorrang, danach folgt der wirksame aktive
DB-Schluessel und zuletzt der erste Setting-Public-Key. Der Server stammt aus
`PUBLIC_BASE_URL` (Fallback `APP_BASE_URL`), niemals aus dem Browser-Host-Header.

Die Liste zeigt Presence bzw. einen Heartbeat der letzten drei Minuten als online,
ausser das Geraet ist gesperrt. OS, Version und letzter Heartbeat sowie gemeldete
Seiten-, Monitor- und Backendzustaende helfen bei der Diagnose. "Bestaetigt" bezieht
sich auf den Rueckkanal der Monitorsteuerung. Update-Status und Fehlercode stehen ebenfalls am Geraet.

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
Overrides und Updateverwaltung sind in den folgenden Abschnitten beschrieben.

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

Details: [Betrieb und Prioritäten](Infoscreen-Agent-Betrieb) und
[Sicherheit](Infoscreen-Agent-Sicherheit).

## Releases und Updates (S6)

**Systemadministratoren** verwalten signierte Agent-Releases unter
**Updates & Backups > Agent-Releases**. Upload-Tokens fuer CI werden einmalig
angezeigt und koennen jederzeit widerrufen werden. Neue Uploads sind mit 0 %
noch nicht freigegeben. 10/50/100 % erweitern die feste Testgruppe; **Release
sperren** zieht eine Version fuer weitere Downloads zurueck. Fingerprints der
konfigurierten Schluessel stehen auf derselben Seite.

**Org-Administratoren** waehlen unter **Infoscreens > Geraete & Plaene > Geraete**
pro Geraet stable/beta, optional eine fixe Version und die Updatesperre.
**Jetzt pruefen** weckt den Agent; ein Einsatz oder eine Sperre wird dadurch
nicht umgangen. Letzter Update-Status und fester Fehlercode stehen am Geraet.
Das Wartungsfenster gilt in der Org-Zeitzone (Default 02:00–04:30), auch ueber
Mitternacht. **Kritische Updates erlauben** gibt nur die Ausnahme vom Fenster
frei. Einsatz/GSL/Nachlauf bleiben geschuetzt.

Der Installer-Hinweis verschwindet automatisch, sobald ein wirksamer aktiver
DB-Schluessel, ein gueltiges Skript und ein freigegebenes stable-Release vorhanden sind. Die Erstinstallation verwendet einen gueltigen
Pairing-Code und die neueste freigegebene Version ohne Canary-Begrenzung;
erst das Koppeln verbraucht den Code. Details fuer Betrieb und CI:
[Installation](Infoscreen-Agent-Installation) und [Betrieb](Infoscreen-Agent-Betrieb).

Weitere Anleitungen: [Überblick](Infoscreen-Agent), [Installation](Infoscreen-Agent-Installation),
[Betrieb](Infoscreen-Agent-Betrieb), [Sicherheit](Infoscreen-Agent-Sicherheit).


## GitHub-Import und Schluessel (S8)

Im selben Tab **Updates & Backups > Agent-Releases** waehlt der Sysadmin
**Schluessel aus Repository uebernehmen**. Die Vorschau zeigt den SHA256-Fingerprint.
Mit der lokalen Ausgabe der Schluesselerzeugung vergleichen und exakt eintippen
oder einfuegen, dann **Schluessel bestaetigen und aktivieren** waehlen. Alternativ
**Schluessel manuell einfuegen** (Ed25519-PEM). Ohne Bestaetigung wird nichts gespeichert.

**Releases von GitHub holen** zeigt importierte, uebersprungene und abgelehnte
Releases samt Fehlercode und letztem Abgleich ohne Neuladen. `key_not_pinned`
bedeutet: zuerst den Schluessel-Schritt abschliessen. Neue Releases bleiben auf 0 %.
Bei `rate_limited` spaeter erneut versuchen. Upload-Tokens bleiben optional.

Vorhandene aktive Keys werden niemals durch den Repo-Abruf ersetzt oder ergaenzt.
Bei Rotation zuerst den bisherigen DB-Key deaktivieren, dann den neuen Fingerprint
bestaetigen. Keys werden nie geloescht; Bestaetigung und Statuswechsel bleiben im Audit.
Setting-Keys haben bei gleicher Key-ID Vorrang. Der optionale taegliche Abgleich
wird durch den Betrieb aktiviert und gibt keine Releases automatisch frei.
