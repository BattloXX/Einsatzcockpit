# BSP-Overlay: Plan auf der Karte

← [Zurück zur Startseite](Home)

Das BSP-Overlay legt einen Ausschnitt aus dem Lage- oder Brandschutzplan eines Objekts **lagegenau auf die Karte**. So siehst du im Einsatz auf einen Blick, wo Gebäude, Zugänge und Anlagen des Plans in der Umgebung liegen. Es erscheinen nur Pläne, die ein Objektverwalter geprüft und freigegeben hat (→ [Administration](Administration-BSP-Overlay)).

Voraussetzung: Der Einsatz ist einem Objekt eindeutig zugeordnet (bestätigt) und das Objekt hat ein freigegebenes Overlay. Sonst bleibt die Karte unverändert, es gibt keine Fehlermeldung.

---

## Wo wird das Overlay angezeigt?

| Ansicht | Bedienung |
|---------|-----------|
| **Lageführung** (Lagekarte des Einsatzes) | Ebene „Brandschutzplan" in den Layern, Auswahl des Plans, Deckkraft, Schalter „für alle" / „nur für mich" |
| **Einsatzinfo**, **GSL-Karte**, **Einheitenansicht** | Ebene „Brandschutzplan" mit Schalter und Deckkraft, nur für dich |
| **Objekt-Einsatzansicht** und **Objektkarte** | Ebene „Brandschutzplan" mit Schalter und Deckkraft, nur für dich |

Das Overlay liegt **unter** den Symbolen der Karte: Marker, Einheiten und Zeichnungen bleiben sichtbar und bedienbar. Zoomen, Verschieben und Tippen funktionieren wie gewohnt.

Zugänge per QR-Code oder Token (ohne Anmeldung) zeigen das Overlay nicht.

---

## Ein- und ausschalten

1. In den Layern der Karte **Brandschutzplan** an- oder abwählen.
2. Mit dem Regler **Deckkraft** die Karte darunter besser oder schlechter sichtbar machen.
3. Gibt es mehrere Pläne (z. B. mehrere Geschosse oder Hallen), wählst du den passenden unter **Plan / Stand** aus. Name, Geschoss/Bereich und Stand der Freigabe stehen dabei.

### „Für alle" oder „nur für mich"

In der **Lageführung** gibt es zwei getrennte Wege:

- **Für alle im Einsatz:** ändert die Anzeige für alle, die den Einsatz gerade sehen. Das darf nur, wer in der Lageführung bearbeiten darf.
- **Nur für mich:** gilt nur auf deinem Gerät und ändert nichts für andere. Ein lokal gesetzter Wert hat bei dir Vorrang und ist als solcher gekennzeichnet. **Vorgabe verwenden** setzt ihn zurück.

Ohne Bearbeitungsrecht steht nur „nur für mich" zur Verfügung.

---

## Drucken

Die Anzeige am Bildschirm und der Ausdruck sind **getrennte Entscheidungen**: Wenn du das Overlay am Bildschirm ausblendest, ändert das nichts an einer Druckregel.

Im Druckdialog der Lageführungskarte wählst du unter **Brandschutzplan**:

| Modus | Ergebnis |
|-------|----------|
| **Nur Karte** (Standard) | Karte ohne Plan |
| **Karte mit Plan** | Karte mit eingeblendetem Plan, Deckkraft einstellbar |
| **Nur Plan-Ausschnitt** | eigenes Blatt mit dem unverzerrten Ausschnitt aus dem Plan |
| **Karte und Plan getrennt** | zwei Seiten: Karte, dann Plan-Ausschnitt |

Der Dialog zeigt ausdrücklich, ob das Overlay gedruckt wird. Auf dem Ausdruck stehen Plan, Geschoss/Bereich, Freigabenummer mit Datum und die Quelle. Ist ein gewählter Plan nicht mehr verfügbar (veraltet oder zurückgezogen), erscheint ein **Hinweis auf dem Blatt** statt eines stillen Weglassens.

Ein automatischer Ausdruck des Plan-Ausschnitts bei Alarm ist über eine Druckregel möglich (→ [Administration](Administration-BSP-Overlay#automatischer-ausdruck-bei-alarm)).

---

## Wenn das Overlay fehlt

- Der Einsatz hat **kein bestätigtes Objekt** (nur ein Vorschlag genügt nicht).
- Es gibt **kein freigegebenes Overlay** für das Objekt.
- Der Plan oder seine Georeferenzierung wurde **geändert** und noch nicht neu freigegeben. Veraltete Pläne werden aus Sicherheitsgründen nie angezeigt.
- Das Bild ist nicht ladbar (keine Verbindung): Die Karte bleibt normal, ein dezenter Hinweis erscheint.

Siehe auch: [Objekte](Anwender-Objekte), [Lageführung](Anwender-Lagefuehrung).
