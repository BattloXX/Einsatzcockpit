# Alarm-Infoscreen

← [Zurück zur Startseite](Home)

> Verwaltung: `/infoscreen-alarm/verwaltung`  
> Zugänglich für: Organisationsadministratoren

Der Alarm-Infoscreen zeigt Alarmdaten auf einem Wandmonitor im Feuerwehrhaus. Jeder Monitor verwendet eine eigene, dauerhaft kopierbare Token-URL. Behandeln Sie diese URL wie einen internen Zugang, da sie ohne Login funktioniert.

## Monitor anlegen und URL verwenden

Unter **Alarm-Infoscreen-Verwaltung** legen Sie Monitor-Tokens an und aktivieren oder sperren sie bei Bedarf. Die zugehörige URL öffnen Sie auf dem Monitor im Vollbild. Der Monitor aktualisiert sich über WebSocket und mit einem Polling-Fallback, wenn neue Alarmdaten eintreffen.

## Ruheansicht und URL-Rotation

Solange kein aktiver Einsatz angezeigt wird, kann der Monitor eine Uhr, Wetterinformationen oder die letzten Einsätze zeigen. Zusätzlich lassen sich frei definierte URLs in einer Reihenfolge pro Monitor hinterlegen. Jede URL hat eine Anzeigedauer; dadurch kann ein Monitor etwa mehrere Informationsseiten nacheinander zeigen. Die Wetteransicht wird bei aktivierter Wetteroption zusätzlich in die Rotation aufgenommen.

## Alarmansicht

Bei einem aktiven Einsatz wechselt der Infoscreen vorrangig zur Alarmansicht. Sie enthält Stichwort, Adresse, Alarmzeit, die Ausrückordnung mit Fahrzeugstatus, eine Karte und – falls vorhanden – das verknüpfte Objekt mit Gefahren, BMA-, FSD- und FBF-Informationen. Rückmeldungen werden als Zu- und Absagen einschließlich Namen angezeigt. Ein Wetter-Badge nutzt die hinterlegten Koordinaten des Gerätehauses.

## Sonderansicht für Großschadenslagen

Ist die GSL-Sonderansicht aktiviert und eine Großschadenslage aktiv, hat sie Vorrang vor der normalen Alarmansicht. Der Monitor zeigt dann Kennzahlen, Einsatzstellen nach Bearbeitungsstand, Ressourcen, Abschnitte auf der Karte und einen Ticker für übergreifende Meldungen.

## Siehe auch

- [Einsatzinfo](Anwender-Einsatzinfo)
- [Objektverwaltung](Administration-Objektverwaltung)
- [Großschadenslage](Anwender-Grosschadenslage)
