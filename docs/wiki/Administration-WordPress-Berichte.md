# WordPress-Berichte einrichten

← [Zurück zur Startseite](Home.md)

Wird ein Einsatz abgeschlossen (manuell, über den 48h-Autoclose, den LIS-Sync, beim Anlegen
eines bereits geschlossenen DIBOS-Events oder beim Beenden einer Großschadenslage), kann Einsatzcockpit
automatisch einen **Beitragsentwurf** im WordPress-Blog der Organisation anlegen — vorbefüllt
mit Titel, Alarmzeit, Einsatzende, Einsatzort, Einsatzart, Dauer und den ausgerückten
Fahrzeugen. Alternativ kann derselbe Entwurf jederzeit manuell aus dem Archiv heraus
ausgelöst werden. **Es wird nie automatisch veröffentlicht** — der Beitrag bleibt Entwurf,
bis ihn jemand im WordPress-Adminbereich fertigstellt und selbst publiziert.

Voraussetzung ist ein WordPress mit dem Theme „Feuerwehr Wolfurt" (ab Version 2.10.3), das
den passenden Einsatzbericht-Endpoint mitbringt.

---

## Master-Schalter und Zugangsdaten

Unter **Admin → WordPress-Berichte** (`/admin/wordpress-berichte`):

| Einstellung | Bedeutung |
|---|---|
| WordPress-Berichte aktiviert | Master-Schalter — aus bedeutet: für diese Organisation wird nie automatisch oder manuell ein Entwurf erzeugt |
| Webhook-URL | Die Endpoint-URL des WordPress-Themes, z. B. `https://feuerwehr.wolfurt.at/wp-content/themes/feuerwehr-wolfurt-v2.0/fw-einsatzbericht-api.php` — muss mit `https://` beginnen |
| Webhook-Token | Dasselbe Secret, das in `wp-config.php` als `FW_EINSATZBERICHT_TOKEN` hinterlegt ist |

Nur `org_admin`/`system_admin` können diese Seite sehen und speichern.

---

## Einrichtung Schritt für Schritt

1. **WordPress-Seite** (Website-Betreiber/Admin): In `wp-config.php` eine lange, zufällige
   Zeichenfolge als `FW_EINSATZBERICHT_TOKEN` hinterlegen und die Endpoint-URL notieren.
   Details dazu stehen im README des Theme-Repos, Abschnitt „Einsatzcockpit-Anbindung".
2. **Einsatzcockpit**: Unter **Admin → WordPress-Berichte** aktivieren, dieselbe Webhook-URL
   und denselben Token eintragen, speichern.
3. **Alarmarten zuordnen** (optional, aber empfohlen): Unter **Admin → Stichwörter** hat
   jede Alarmart jetzt ein zusätzliches Feld **WordPress-Einsatzart**. Dort einen der fünf
   festen WordPress-Werte eintragen (`Brandeinsatz`, `Technischer Einsatz`,
   `Hochwasser Einsatz`, `Fehlalarm`, `Sturm`) — bleibt das Feld leer, erscheint im
   WordPress-Entwurf einfach keine Einsatzart (der Redakteur trägt sie dann selbst ein).
4. **Fahrzeuge zuordnen**: Damit ausgerückte Fahrzeuge im Entwurf korrekt erscheinen, muss
   jedes Fahrzeug auf **beiden** Seiten dieselbe **LIS-ReferenceId** tragen — hier unter
   **Admin → Fahrzeuge** je Fahrzeug pflegen (Feld existiert bereits, siehe
   [Stammdaten pflegen](Administration-Stammdaten-pflegen.md)), auf WordPress-Seite im
   entsprechenden Feld der Fahrzeug-Stammdaten. Fahrzeuge ohne übereinstimmende
   ReferenceId auf einer der beiden Seiten werden im Entwurf einfach ausgelassen.

---

## Automatischer Entwurf beim Abschließen

Sobald ein Einsatz geschlossen wird — per Klick auf **Einsatz abschließen**, über den
48h-Autoclose, den LIS-Sync, beim Anlegen eines bereits geschlossenen DIBOS-Events oder
beim Beenden einer Großschadenslage — versucht Einsatzcockpit im Hintergrund (best effort), den Entwurf
anzulegen. Ist die WordPress-Anbindung nicht konfiguriert oder gerade nicht erreichbar,
wird der Fehler nur geloggt — **der Einsatzabschluss selbst schlägt dadurch nie fehl.**

## Manuell auslösen

Im **Archiv** eines bereits abgeschlossenen Einsatzes erscheint (ab Rolle **Bearbeiter**,
sofern die WordPress-Anbindung für die Organisation aktiviert ist) der Button
**„Website-Entwurf erstellen"**. Ein Klick zeigt sofort das Ergebnis an: entweder einen Link
**„↗ Website-Entwurf öffnen"** zum neuen Entwurf im WordPress-Adminbereich, oder eine
Fehlermeldung.

## Keine Duplikate, keine überschriebenen Änderungen

Sobald für einen Einsatz einmal erfolgreich ein Entwurf angelegt wurde, merkt sich
Einsatzcockpit dessen WordPress-Beitrags-ID. Jeder weitere Versuch — automatisch beim
erneuten Schließen oder per Klick auf den Button — erkennt das und tut **nichts** außer den
bestehenden Link anzuzeigen. Es wird weder ein zweiter Beitrag angelegt, noch werden Titel
oder Text eines bereits von einem Redakteur bearbeiteten Entwurfs überschrieben.

## Großschadenslage: nur ein Bericht für die ganze Lage

Ist ein Einsatz einer **Großschadenslage** als Einsatzstelle zugeordnet, entsteht **ein
einziger** Website-Entwurf für die gesamte Lage — nicht einer je Einsatzstelle. Die zuerst
abgeschlossene (oder zuerst per Button ausgelöste) Einsatzstelle legt den Entwurf an; alle
weiteren Einsatzstellen derselben Lage übernehmen automatisch denselben Link, ohne einen
eigenen Beitrag oder einen weiteren Webhook-Aufruf auszulösen. Der Beitragstitel ist in
diesem Fall der Name der Großschadenslage, nicht der Einsatzgrund der einzelnen
Einsatzstelle. Dieses Verhalten ist fest eingebaut (kein Schalter) — für Website-Entwürfe
gibt es keinen sinnvollen Anwendungsfall für mehrere Beiträge zu ein und derselben Lage.

---

## Was übertragen wird

| Einsatzcockpit | WordPress | Anmerkung |
|---|---|---|
| Einsatzgrund | Beitragstitel | Fällt zurück auf die Bezeichnung der Alarmart, dann auf die Leitstellennummer, wenn kein Einsatzgrund erfasst wurde |
| Alarmzeit / Einsatzende | Alarmzeit / Einsatzende | In der Zeitzone der Organisation |
| Alarmzeit + Einsatzende | Dauer (Minuten) | Wird direkt berechnet und vorausgefüllt |
| Adresse (Ort + Straße) | Einsatzort | Innerorts (Ort = Sitz der Organisation): nur die Straße ohne Hausnummer. Außerorts: Ort + Straße |
| Alarmart → „WordPress-Einsatzart" | Einsatzart | Siehe Einrichtung, Punkt 3 |
| Ausgerückte Fahrzeuge (LIS-ReferenceId) | Fahrzeug-Auswahl | Siehe Einrichtung, Punkt 4 |

Mannschaftsstärke und beteiligte Organisationen werden nicht automatisch befüllt — dafür
gibt es in Einsatzcockpit keine passende Datenquelle; der Redakteur trägt sie bei Bedarf im
WordPress-Editor selbst ein.

---

## Troubleshooting

| Symptom | Mögliche Ursache |
|---|---|
| Button erscheint nicht im Archiv | WordPress-Berichte für diese Organisation nicht aktiviert, Einsatz noch nicht abgeschlossen, oder eigene Rolle liegt unter „Bearbeiter" |
| Fehlermeldung „Website-Anbindung ist für diese Organisation nicht konfiguriert." | Unter Admin → WordPress-Berichte ist „aktiviert" aus, oder Webhook-URL/Token fehlen |
| Fehlermeldung „Website-Entwurf konnte nicht erstellt werden." | WordPress-Seite nicht erreichbar, Token stimmt nicht mit `FW_EINSATZBERICHT_TOKEN` überein, oder Webhook-URL falsch/veraltet |
| Entwurf erscheint, aber ohne Fahrzeuge | LIS-ReferenceId ist auf einer der beiden Seiten nicht (oder nicht identisch) gepflegt |
| Entwurf erscheint ohne Einsatzart | Für die verwendete Alarmart ist unter Admin → Stichwörter kein „WordPress-Einsatzart"-Wert hinterlegt |
| Ein zweiter Klick auf den Button ändert nichts mehr | Erwartetes Verhalten — der Entwurf existiert bereits, es wird nichts erneut angelegt oder überschrieben |

---

**Verwandt:** [Archiv und PDF-Export](Anwender-Archiv-und-PDF-Export.md) · [Stammdaten pflegen](Administration-Stammdaten-pflegen.md) · [Teams-Alarmierung](Administration-Teams-Alarmierung.md)
