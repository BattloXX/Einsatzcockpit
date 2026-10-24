# GSL-Einheitenansicht

← [Zurück zur Ressourcenverwaltung](Anwender-GSL-Ressourcenverwaltung)

> URL: `/einheit` · für Geräteprofil **Einheit**, persönlichen GK-Zugang oder QR-Zugang.

Die Einheitenansicht ist die Arbeitsansicht für genau eine disponierte Einheit. Sie zeigt deren Aufträge, Einsatzorte und Rückmeldungen; die Führungs- und Ressourcenverwaltung bleibt davon getrennt.

## Zugang und Geräte

| Zugang | Verwendung |
|---|---|
| Persönlicher GK-Link | Der Gruppenkommandant öffnet den Link aus SMS oder kopierter Nachricht. Ein SMS-Bestätigungscode kann in den [GSL-Einstellungen](Administration-GSL-Einstellungen) vorgeschrieben sein. |
| QR-Zugang | Der QR-Code vom Ausdruck öffnet die eigene Einheit; eine PIN kann erforderlich sein. |
| Geräteprofil **Einheit** | Ein angemeldetes Fahrzeug- oder Tabletgerät arbeitet im Einheitenmodus. |
| Gesamtansicht auf dem Einheiten-Tablet | Die Gesamtansicht ist nur lesend. |

GK-Link und QR-Zugang sehen und bearbeiten ausschließlich die eigene Einheit. Personal und Ausstattung darf ein GK nur pflegen, wenn dies in den GSL-Einstellungen erlaubt ist; der QR-Zugang darf es nie.

## Bereiche und Ansicht

Die Bereiche heißen **Einsätze**, **Karte** und **Meldungen**. Sie werden auf Smartphone und Tablet über dieselben beschrifteten Schaltflächen gewechselt. Auf breiten Tablets stehen weitere Auftragskarten nebeneinander.

Die Standardansicht verwendet große Touchflächen und gut lesbare Schrift. Sie folgt der hellen oder dunklen Darstellung Ihres Geräts/Browsers. Unter **Weitere Funktionen** stehen Gesamtansicht, vorhandene Einheitenwechsel und Bedienhinweise. GK-/QR-Nutzer finden dort auch **Abmelden** und die optionale Mobilnummer-Bestätigung. Es gibt keinen zusätzlichen Einfachmodus.

| Bereich | Inhalt |
|---|---|
| **Einsätze** | Aktueller Auftrag als große Karte, sonst der nächste. Alle weiteren offenen Aufträge folgen als kompakte Karten; beendete und zurückgezogene Aufträge lassen sich aufklappen. |
| **Karte** | Aufträge der eigenen Einheit, Standortfunktion und Navigation. |
| **Meldungen** | Noch nicht übermittelte und zuletzt übermittelte Einträge, Synchronisieren sowie Fehler- und Konfliktbehandlung. Lagemeldungen, Fotos und die Chronik stehen weiterhin im jeweiligen Auftrag zur Verfügung. |

Jede offene Auftragskarte zeigt Art, Einsatzstelle/Adresse, kurzen Auftragstext, bekannte Gefahren am Ziel und Bearbeitungsstand. **ERKUNDUNG** erscheint orange mit Lupe, **AUFTRAG** blau mit Werkzeug. Dieselbe Kennzeichnung steht im Detail, in Dialogen, auf Kartenmarkern und bei eindeutig zugeordneten Übermittlungen. **Auftrag öffnen** zeigt den vollständigen Auftrag und alle bisherigen Aktionen. Öffnen bestätigt oder startet keinen Auftrag.

Auftragsart und Dringlichkeit sind unabhängig: Die Art färbt das Symbol und die Artbeschriftung, die Priorität den Kartenrand beziehungsweise Markerring. **Priorität: Sofort / Dringend / Normal / Aufschiebbar** steht zusätzlich als Text dabei. Fehlt die gespeicherte Art oder ist sie unbekannt, erscheint neutral **ART UNBEKANNT**; die Ansicht errät keine Art aus Auftragstext oder Status. Bestehende gespeicherte Auftragsarten werden unverändert verwendet.

Im Kopf stehen der Name der Einheit, offene Aufträge sowie Verbindung und letzter Abgleich. **Noch nicht übermittelt** kennzeichnet lokale Änderungen. Bei fehlender Kartenposition oder älterem Offline-Stand können Gefahrenhinweise unvollständig sein; die Ansicht zeigt dies ausdrücklich an. Freigegebene Personal- und Ausstattungspflege bleibt unter **Meine Einheit – Personal und Ausstattung** erreichbar.

Neue Aufträge tragen **NEU**, geänderte **GEÄNDERT**. Im Detail weist „Auftrag wurde geändert – bitte prüfen“ auf einen geänderten Auftragstext hin. Bei einer neuen Reihenfolge erscheint ein Hinweisbanner; Priorität und von der Leitstelle vorgegebene Reihenfolge bleiben maßgeblich.

## Aufträge bearbeiten

Alle zugewiesenen Aufträge lassen sich vor der Übernahme vollständig öffnen. Adresse und Karte, Gefahren, freigegebene Objekthinweise, Zufahrt/Sperren, Bilder und Pläne sowie der berechtigte Meldungsverlauf sind dafür erreichbar. Das Lesen verändert keinen Status. Über GK-/QR-Link bleibt die Anzeige anderer Einheiten und fremder Meldungen eingeschränkt; die Ansicht weist darauf hin.

| Bearbeitungsstand | Bedeutung |
|---|---|
| **Zugewiesen** | Die Einsatzleitung hat den Auftrag zugeteilt. |
| **Übernommen / bestätigt** | Der Gruppenkommandant hat den Empfang bestätigt. Mehrere Aufträge dürfen diesen Stand haben. |
| **Aktiv · Anfahrt / Vor Ort / In Arbeit** | Die Einheit bearbeitet diesen Auftrag als primären Auftrag. Grundsätzlich ist genau ein Auftrag gleichzeitig aktiv. |
| **Fertig gemeldet** beziehungsweise **Erkundet** | Der eigene Auftrag ist beendet; die Einsatzleitung entscheidet über den Abschluss der Einsatzstelle. |

**Auftrag übernehmen** bestätigt ausschließlich den Empfang. Danach führt die Hauptaktion über **Anfahrt melden** und **Vor Ort melden** zur Bearbeitung. Ein Auftrag ist entweder **Erkundung** oder **Abarbeitung**. Die verfügbaren Status folgen der Auftragsart und dem aktuellen Status.

Im geöffneten Auftrag bleibt die große Hauptschaltfläche beim Scrollen erreichbar. **Einheitenstatus** bezeichnet den Gesamtstatus der Ressource; **Auftrag** zeigt den vom Server bestätigten Stand dieses einzelnen Auftrags. Bei Offline-Aktionen steht der gewünschte Schritt getrennt unter **Lokal vorgemerkt**, bis er übermittelt wurde. Die Schaltfläche zeigt dann **Übermittlung ausstehend** und verhindert weitere Statusaktionen dieses Auftrags.

Auftragsart, Einsatzstelle und bekannte Gefahren bleiben beim Scrollen langer Texte im Kopf erreichbar. Die rote Gefahrenleiste führt zu den vollständigen Umgebungs- und Objekthinweisen vor Karte und Auftragstext. **Meldung an Einsatzleitung** und **Foto** stehen direkt beim Auftrag. Seltene Aktionen wie **Maßnahme**, **Notiz**, **Nicht durchführbar** und freigegebenes **Material** finden Sie unter **Weitere Aktionen**; die bisherige **Lagemeldung** bleibt dort ebenfalls erreichbar.

| Auftragsart | Primäre Aktionen | Weitere Aktionen |
|---|---|---|
| Erkundung | **Auftrag übernehmen → Anfahrt melden → Vor Ort melden → Erkundungsergebnis erfassen → Erkundung abschließen** | Weitere erlaubte Status, Lagemeldung, Foto und Navigation |
| Abarbeitung | **Auftrag übernehmen → Anfahrt melden → Vor Ort melden → In Arbeit setzen → Fertig melden** | Weitere erlaubte Status, Lagemeldung, Foto und Navigation |

Unter **Weitere Status** bleiben erlaubte direkte Schritte erreichbar, etwa **Vor Ort melden**, wenn die Einheit bereits dort ist. Beim Start eines anderen Auftrags erscheint **Unterbrechen und wechseln** mit beiden Einsatzstellen. Der bisherige Auftrag bleibt übernommen. Neue dringende Aufträge werden angezeigt, wechseln aber nicht automatisch die aktive Bearbeitung. Übernehmen und Wechseln ändern weder Priorität noch Reihenfolge der Einsatzleitung.

Übernehmen, Anfahrt, Vor Ort und In Arbeit benötigen keinen zusätzlichen Bestätigungsdialog. **Fertig melden** und **Bearbeitung unterbrechen** verlangen eine ausdrückliche Entscheidung; **Nicht durchführbar** benötigt einen Grund. Bei einer Phasensperre steht **Freigabe ausstehend** mit einer Erklärung anstelle einer ausführbaren Statusaktion. Beendete und zurückgezogene Aufträge sowie reine Lesezugänge zeigen ihren Stand ohne ausführbaren Statusschritt. Statusaktionen wechseln nicht selbstständig die Seite.

**Erkundungsergebnis erfassen** öffnet das Ergebnisformular, ohne den Auftrag zu beenden. Wählen Sie ein Ergebnis aus. Bei Maßnahmen, akuter Gefahr oder weiterer Erkundung beschreiben Sie zusätzlich kurz die Lage oder notwendige Maßnahmen. Bei **Keine Maßnahmen erforderlich** genügt die Auswahl. Fotos bleiben optional. Erst **Erkundung abschließen** reiht Ergebnis, Fotos und Abschluss gemeinsam in die lokale Übermittlung ein. Bei Speicherfehlern bleiben Angaben und gewählte Fotos im Formular; es wird kein halber Abschluss gespeichert. Textentwürfe werden auf diesem Gerät je Auftrag wiederhergestellt. Ein Entwurf zu einer älteren Auftragsversion erhält einen Prüfhinweis.

Objekthinweise und Pläne stammen aus bestätigten Objektverknüpfungen und freigegebenen produktiven Daten. Entwürfe und wartende beziehungsweise alte Dokumentversionen sind nicht verfügbar. Pläne lassen sich unmittelbar aus dem Auftrag öffnen, ohne die Objektverwaltung aufzurufen.

Bei Bedarf kann ein Auftrag als **Nicht durchführbar** gemeldet werden; dafür ist ein Grund anzugeben. Zu jedem Auftrag stehen außerdem der Auftragstext der Einsatzleitung, Adresse, weitere Einheiten an der Stelle, bekannte Gefahren und Sperren am Ziel zur Verfügung.

## Lagemeldung, Fotos und Material

Unter **Meldungen → Meldung an Einsatzleitung** oder direkt im Auftrag wählen Sie eine Schnellmeldung und tippen auf **Erfassen**. Eine Ergänzung ist bei normalen Schnellmeldungen freiwillig. Kontrollieren Sie den angezeigten Einsatz; Fotos werden ihm automatisch zugeordnet. Bei ausgewählten Fotos bleibt die Einsatzwahl gesperrt. **Diktieren** erscheint, wenn der Browser die vorhandene Spracheingabe unterstützt. Die Spracheingabe Ihrer Gerätetastatur können Sie ebenfalls nutzen.

Formulare zeigen Einsatzstelle und Adresse im Kopf. Bei Speicherfehlern bleiben Text und Fotos im Formular erhalten. Antippen außerhalb oder Escape schließt das Formular nicht. Nach dem Speichern kehren Sie an die Ausgangsstelle derselben Ansicht zurück; eine Offline-Meldung bleibt bis zur Übermittlung entsprechend gekennzeichnet.

Bei **Erkundung** stehen **Maßnahmen erforderlich**, **Keine Maßnahmen erforderlich**, **Akute Gefahr festgestellt** und **Weitere Erkundung erforderlich** bereit. Bei **Abarbeitung** stehen **Arbeit läuft**, **Auftrag erledigt**, **Unterstützung benötigt** und **Auftrag nicht durchführbar** bereit. **Auftrag erledigt** öffnet die Abschlussbestätigung; bei **Nicht durchführbar** müssen Sie den Grund angeben. Auch bei der Abschlussbestätigung und **Nicht durchführbar** können Sie Text und Fotos ergänzen. Bereits im Meldungsdialog gewählte Fotos und Texte werden übernommen und erst mit der abschließenden Bestätigung gespeichert. Eine normale Meldung verändert den Bearbeitungsstand nicht. Gefahr und Unterstützungsbedarf werden im GSL-Board hervorgehoben.

Über die Auftragsansicht können Lagemeldungen und Fotos übermittelt werden. Ist der Geräteverleih für die Lage aktiviert, lässt sich dort auch Material für die Einsatzstelle erfassen und zurückmelden; Materialfotos gehören zu diesem Vorgang.

## Meldungsverlauf

Im Auftrag zeigt **Verlauf der Einsatzstelle** Meldungen und Fotos von alt nach neu. Unter **Meldungen** finden Sie die wichtigen Meldungen der eigenen Einheit, neueste zuerst. Neue Aufträge, Änderungen, Rückfragen, Gefahren, Unterstützungsbedarf und Übertragungsfehler sind hervorgehoben. Normale Statusmeldungen erscheinen kompakt beim Auftrag.

Eigene Einträge zeigen ausdrücklich **Übermittelt** oder dass sie **nur auf diesem Gerät** liegen. Offline bleibt der zuletzt geladene Verlauf sichtbar. **Ältere Meldungen laden** ergänzt weitere Einträge; **Meldungen aktualisieren** lädt den aktuellen Stand. Bilder können direkt geöffnet werden. Die bestehenden Sendeaktionen stehen unter **Übermittlungen** bereit.

**Auftrag öffnen** führt zum nachgewiesenen Auftrag. **Meldung zur Einsatzstelle** kennzeichnet ältere oder allgemeine Einträge ohne eindeutige Auftragsreferenz; sie werden keinem gerade geöffneten Auftrag zugeschrieben. GK-/QR-Zugänge behalten ihre bisherigen eingeschränkten Sichtrechte.

## Karte und Navigation

| Kartenelement | Bedeutung |
|---|---|
| Orange Lupe mit **ERKUNDUNG** | Erkundungsauftrag. |
| Blaues Werkzeug mit **AUFTRAG** | Abarbeitungsauftrag. |
| Farbig umrandeter Marker und **Priorität: …** | Dringlichkeit, unabhängig von der Auftragsart. |
| Größerer Marker mit weißem Außenring und **● Aktuell** | Laufender Auftrag der Einheit. |
| **▶ Nächster** | Vom System als nächster Auftrag geführter Auftrag. |
| Nummern | Weitere offene Aufträge in der angezeigten Reihenfolge. |
| Haken und gestrichelter Markerring | Beendeter Auftrag, falls eingeblendet. |
| Gestrichelte Verbindung | Luftlinie zwischen offenen Aufträgen, keine befahrbare Route. |
| Zahl direkt am Marker | Mehrere eigene Aufträge an derselben Position; das Kartenfenster bietet jeden einzeln an. |
| **!** und rote gestrichelte Sperrenlinie beziehungsweise **×** | Bekannte Gefahren und Straßensperren am gewählten Ziel. |

Der aktuelle beziehungsweise nächste Auftrag ist vorausgewählt. **Ziel wählen** in der Liste oder Antippen eines Markers zeigt Adresse, Auftrag und bekannte Gefahren/Sperren dieses Ziels. **Navigation starten** ist als große Schaltfläche erreichbar. **Auftrag öffnen** zeigt den vollständigen Auftrag. **Zur Einsatzübersicht** führt zurück. Die Auswahl ändert keinen Bearbeitungsstand und keine Reihenfolge.

Die Liste unter der Karte macht alle eigenen Aufträge einzeln erreichbar, auch ohne Kartenposition oder bei überlagerten Markern. Ohne Koordinaten bleibt die Adresse zur Navigation verfügbar; fehlen auch Adressdaten, erklärt ein Hinweis die fehlende Navigation. **Alle Einsatzstellen zeigen** passt den Ausschnitt an. Mit **Fertige einblenden** werden abgeschlossene Aufträge ergänzt. Nummern und Luftlinien folgen der Reihenfolge der Einsatzleitung, auch bei aktiver Standortanzeige.

**Mein Standort** fragt die Position erst nach Antippen im Browser ab; sie wird weder gespeichert noch an den ECP-Server gesendet. **Zentrieren** richtet die Karte auf diesen Standort aus. **Standort aus**, Verlassen der Karte und Wechsel in den Hintergrund beenden die Standortanzeige. Eine verweigerte Freigabe verhindert die Bedienung der Karte nicht.

**Navigation starten** öffnet Google Maps mit dem gewählten Ziel beziehungsweise seiner Adresse. Externe Navigation berücksichtigt ECP-Sperren nicht automatisch; Zufahrt anhand der Hinweise prüfen und bei Bedarf mit der Einsatzleitung klären. Fehlende Sperrendaten werden als nicht verfügbar angezeigt. Offline erscheinen bereits geladene Sperren als letzter Stand; sie sind keine bestätigte freie Zufahrt. Die Kartengrundlage und externe Navigation können eine Netzverbindung benötigen.

## Offline arbeiten

Statuswechsel, Meldungen, Fotos und Materialvorgänge werden zunächst in einer Offline-Outbox auf diesem Gerät gespeichert. Nicht bestätigte Einträge sind als **Nur lokal gespeichert** gekennzeichnet. Eine Erfolgsmeldung bedeutet erst dann eine Übermittlung, wenn der Server den Vorgang bestätigt hat.

**Nur lokal gespeichert** bedeutet, dass die Daten auf diesem Gerät erhalten sind und noch keine Serverbestätigung vorliegt. **Übertragung fehlgeschlagen** zeigt einen fehlgeschlagenen Versuch; Netzfehler werden erneut versucht, fachliche Konflikte müssen geprüft werden. **Übermittelt** erscheint erst mit Servernachweis. Ohne offene Einträge ist **Keine offenen Übermittlungen** die neutrale Anzeige; **Alles übermittelt** setzt vorhandene eigene Quittungen voraus.

Unter **Meldungen → Übermittlungen** können Sie **Jetzt synchronisieren**, erneut senden, bei Fehlern oder Konflikten kopieren sowie Einträge verwerfen. Offline zeigt die Seite den zuletzt geladenen Stand.

Bei Wiederverbindung oder WebSocket-Reconnect prüft die Ansicht zunächst den aktuellen Zugang und beginnt danach automatisch mit den Netzrückständen. **Jetzt synchronisieren** stößt denselben geprüften Abgleich an. Sie müssen normale Netzfehler nicht einzeln erneut senden. Bleibt eine Übertragung ohne Antwort, wird sie mit derselben Kennung wiederholt; das verhindert doppelte Servereinträge. Ein bereits geladener Auftrag kann auch nach einem echten Offline-Neustart wieder geöffnet werden.

Abgelaufene GK-/QR-Zugänge sperren ausstehende Einträge, ohne Text oder Fotos zu löschen. Die Einträge werden nicht mit einem neuen Zugang automatisch freigegeben. Bei geänderter Einheitenzuordnung werden alte Einträge ebenfalls nicht für die neue Einheit gesendet. Hinweise der Ansicht beachten und bei Bedarf die Einsatzleitung verständigen. Ein bestätigter Abschluss bestätigt nicht gleichzeitig noch ausstehende Fotos; deren Übermittlungsstand bleibt getrennt sichtbar.

Auch mehrere Übernahmen lassen sich offline erfassen. Sie tragen **nur auf diesem Gerät** beziehungsweise **noch nicht übermittelt**, bis die Einsatzleitung den Serverstand sehen kann. Während eine Statusmeldung aussteht, verhindert die Ansicht doppelte Statusaktionen desselben Auftrags. Ein inzwischen geänderter oder zurückgezogener Auftrag wird beim Synchronisieren als Konflikt angezeigt. **Auftrag prüfen** öffnet den aktuellen Stand; erst eine neue bewusste Aktion übernimmt ihn. Ein veralteter Wechsel darf keinen inzwischen anderen aktiven Auftrag still unterbrechen.

Lokale und zuletzt übermittelte Einträge zeigen die Auftragsart, wenn der verknüpfte Auftrag im berechtigten Datenbestand vorliegt. Chronik und Fotogalerie gehören dagegen zur **Einsatzstelle** und können mehrere Aufträge betreffen; sie erhalten keine aus dem gerade geöffneten Auftrag abgeleitete Artkennzeichnung.

## Simulation

Administratoren können eine Einheit simuliert öffnen. In einer Übung ist die Simulation schreibbar; in einer Echtlage bleibt sie nur lesend. Simulationen werden als solche im Journal/Audit kenntlich gemacht.
