# Übungsmodus

← [Zurück zur Startseite](Home)

## Was ist der Übungsmodus?

Wenn ein Einsatz als **Übung** markiert ist (`is_exercise = true`), verhält er sich funktional identisch zu einem Echteinsatz — für eine realistische Übung — wird aber in mehreren Bereichen besonders behandelt.

## Übung starten

**Via Alarmierungssystem:** Wenn das Alarmierungssystem `"Uebung": true` im Payload übergibt, wird der Einsatz automatisch als Übung markiert.

**Manuell:** Neuer Einsatz → **Als Übung markieren** ☑ aktivieren.

Bestehende Einsätze können nachträglich (nur Admin) umgeschaltet werden.

## Erkennungsmerkmale einer Übung

### Diagonaler ÜBUNG-Banner

In der gesamten App erscheint ein gelb/schwarz gestreifter Querbalken im Header:

```
⚠ ÜBUNG – ÜBUNG – ÜBUNG ⚠
```

Damit ist jederzeit klar, dass es sich um eine Übung handelt.

### Push-Benachrichtigungen

Alle Push-Benachrichtigungen für Übungseinsätze tragen das Präfix **[ÜBUNG]**:

> [ÜBUNG] Neuer Einsatz: T3 – Technische Hilfe, Teststraße 1

So kann niemand durch eine Übungsbenachrichtigung in einen echten Alarm versetzt werden.

### PDF-Bericht

Jede Seite des PDF-Berichts zeigt den diagonalen **ÜBUNG**-Banner. Der Bericht kann als Übungsprotokoll dienen.

## Statistik-Ausschluss

Übungseinsätze werden aus der Standard-Statistik ausgeschlossen:
- Zählen nicht in der Einsatzzahl pro Monat
- Zählen nicht bei der Durchschnittsdauer
- Zählen nicht bei der AS-Einsatzzeit der Mitglieder

In der Statistik kann ein **Toggle** aktiviert werden, um Übungen separat anzuzeigen.

## Atemschutz in der Übung

Die Atemschutzüberwachung verhält sich in einer Übung **identisch** zu einem Echteinsatz:
- Rückzugsdrücke werden berechnet
- Warnungen erscheinen bei 75% und Rückzugsdruck
- Zeiten werden protokolliert

Jedoch: Die aufgezeichneten Atemschutzzeiten zählen **nicht** zur kumulativen Mitglieder-Einsatzzeit (für Tauglichkeits-Nachverfolgung).

## Archiv

Übungseinsätze landen im Archiv in einem **eigenen Reiter „Übungen"** und erscheinen nicht in der Standard-Archiv-Liste.
