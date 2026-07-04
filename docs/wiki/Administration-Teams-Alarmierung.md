# Teams-Alarmierung einrichten

← [Zurück zur Startseite](Home)

Bei jeder Einsatzanlage (über das Alarmierungssystem, das Leitstellensystem/LIS oder manuell)
kann Einsatzcockpit die komplette Alarmierung zusätzlich als Karte in einen Microsoft-Teams-Kanal
posten — inklusive Kartenbild, Google-Maps-Link und einem No-Login-Link zur Alarmübersicht.
Echtalarme und Übungen können an unterschiedliche Kanäle gehen.

**Zweistufiges Modell:**

| Modus | Voraussetzung | Zusage/Absage direkt in Teams |
|---|---|---|
| **Einfacher Modus** (Webhook) | Ein normaler Teams-„Incoming Webhook"-Connector — in 2 Minuten eingerichtet, kein Azure nötig | Nein |
| **Erweiterter Modus** (Bot) | Einmalige Azure-Bot-Registrierung durch einen M365-Admin | Ja |

Der einfache Modus läuft unabhängig vom erweiterten Modus — ist die Bot-Erweiterung (noch)
nicht eingerichtet oder deaktiviert, wird für das jeweilige Ziel (Echtalarm/Übung) automatisch
der Webhook verwendet, sofern eine URL hinterlegt ist. Kein Hard-Fail, kein gegenseitiges
Blockieren.

> **Hinweis zum aktuellen Stand:** Der **einfache Webhook-Modus ist voll funktionsfähig**.
> Die **Bot-Erweiterung (Zusage/Absage direkt in Teams) ist in Vorbereitung** — die
> Admin-Oberfläche zur Konfiguration existiert bereits, der serverseitige Bot-Endpunkt
> (Empfang der Kanalbindung und der Zusage/Absage-Klicks) folgt in einer der nächsten
> Versionen. Bitte die Azure-Einrichtung unten daher erst vornehmen, wenn das in den
> Release-Notes bestätigt wurde — bis dahin läuft die Teams-Alarmierung über den
> einfachen Modus weiter.

---

## Master-Schalter und Inhalts-Bausteine

Unter **Admin → Teams-Alarmierung** (`/admin/teams-alarmierung`):

| Einstellung | Bedeutung |
|---|---|
| Teams-Alarmierung aktiviert | Master-Schalter — aus bedeutet: für diese Organisation passiert weder Webhook- noch Bot-Versand |
| Übungseinsätze auch senden | Standardmäßig **aus** (analog zur SMS-Einsatzinfo-Konvention) — Übungen lösen nur dann eine Teams-Karte aus, wenn hier aktiviert |
| Kartenbild einbetten | Serverseitig aus OpenStreetMap gerendertes Kartenbild um die Einsatzadresse |
| Google-Maps-Link | Direktlink `https://maps.google.com/?q=<lat>,<lng>` als Button in der Karte |
| Alarmübersicht-Link (QR) | Link zu einer No-Login-Seite mit den Alarm-Kerndaten (Stichwort, Adresse, Meldung, Karte — **keine** Mannschafts-/Personendaten) |

Jeder Baustein ist einzeln abschaltbar, z. B. wenn ein Kartenbild aus Datenschutz- oder
Bandbreitengründen nicht gewünscht ist.

---

## Einfacher Modus (Webhook) einrichten

1. Im Ziel-Teams-Kanal (z. B. „Alarmierung") → **Connectors** (oder in neueren Teams-Versionen:
   **Workflows**) → **Incoming Webhook** hinzufügen, einen Namen vergeben (z. B.
   „Einsatzcockpit-Alarmierung") → **Erstellen**.
2. Die angezeigte Webhook-URL kopieren.
3. In Einsatzcockpit unter **Admin → Teams-Alarmierung** unter „Einfacher Modus (Webhook)" bei
   **Webhook-URL — Echtalarm** einfügen. Für ein separates Übungs-Ziel denselben Ablauf für
   einen zweiten Kanal wiederholen und die URL bei **Webhook-URL — Übung** eintragen (optional
   — bleibt das Feld leer, gehen Übungen an denselben Kanal wie Echtalarme, sofern
   „Übungseinsätze auch senden" aktiviert ist).
4. **Speichern**, danach über **Testkarte senden** prüfen, ob die Karte im gewählten Kanal
   ankommt.

Damit ist der einfache Modus einsatzbereit — jede neu angelegte Alarmierung (API, LIS,
manuell) postet ab sofort automatisch eine Karte.

---

## Erweiterter Modus (Bot, Zusage/Absage) — Einrichtung für später

Diese Schritte sind für einen **M365-/Azure-Administrator** gedacht und liegen außerhalb von
Einsatzcockpit selbst. Sie sind **erst relevant, sobald die Bot-Anbindung freigeschaltet ist**
(siehe Hinweis oben).

1. **Azure-Bot-Ressource anlegen**: Azure Portal → *Create a resource* → **Azure Bot**.
   Bei „Type of App" **Single Tenant** wählen (einfacher zu betreiben als Multi-Tenant), bei
   „Creation type" **„Create new Microsoft App ID"**. Damit entsteht eine neue
   App-Registrierung.
2. **App-ID und Tenant-ID notieren** (beide werden später in Einsatzcockpit eingetragen).
3. App-Registrierung → **Certificates & secrets** → **New client secret** anlegen, den Wert
   sofort kopieren (wird nur einmal angezeigt — das ist das **Client-Secret**).
4. Azure Bot → **Configuration** → **Messaging endpoint** eintragen:
   `https://<eure-cockpit-domain>/api/v1/teams/messages`
5. Azure Bot → **Channels** → **Microsoft Teams** hinzufügen.
6. **Teams-App-Paket bauen**: ein ZIP aus `manifest.json` + einem 192×192-Farbicon
   (`color.png`) + einem 32×32-Umrissicon (`outline.png`). Wichtige Manifest-Felder:
   - `id`: eine neue, eigene GUID für das Teams-App-Paket
   - `bots[0].botId`: die Microsoft-App-ID aus Schritt 1
   - `bots[0].scopes`: `["team"]`
7. Im **Teams Admin Center** → **Manage apps** → **Upload** das ZIP als Custom-App
   hochladen (setzt voraus, dass Custom-App-Uploads im Tenant erlaubt sind).
8. Die App **dem Ziel-Team/-Kanal hinzufügen** — einmal für den Echtalarm-Kanal, bei Bedarf
   ein zweites Mal für einen separaten Übungs-Kanal. Dadurch lernt Einsatzcockpit automatisch,
   in welchen Kanal es später posten kann (die Kanalbindung erscheint danach unter
   **Admin → Teams-Alarmierung** im Abschnitt „Erweiterter Modus").
9. In Einsatzcockpit unter **Admin → Teams-Alarmierung** → „Erweiterter Modus: Bot mit
   Zusage/Absage" aktivieren und App-ID, Tenant-ID sowie Client-Secret aus Schritt 2/3
   eintragen, speichern.
10. Sobald eine Kanalbindung erfasst wurde, im entsprechenden Dropdown das Ziel
    (Echtalarm/Übung) zuordnen.

Danach werden neue Alarmierungen für ein Ziel mit vorhandener Kanalbindung automatisch über
den Bot gesendet (mit Zusage-/Absage-Buttons); Ziele ohne Kanalbindung laufen unverändert über
den einfachen Webhook-Modus weiter.

### Wie eine Zusage/Absage in Einsatzcockpit ankommt

Klickt jemand in Teams auf „Zusagen" oder „Absagen", wird die Antwort automatisch dem
bestehenden Mannschaftsregister zugeordnet — per Abgleich der Teams-/Entra-ID-E-Mail-Adresse
gegen die im Mannschaftsregister hinterlegte E-Mail-Adresse. **Es findet kein Massenimport der
Teams-/Entra-ID-Mitgliederliste statt** — nur wer tatsächlich antwortet, wird zugeordnet
(und nur, wenn seine E-Mail-Adresse bereits im Mannschaftsregister gepflegt ist).

---

## Am Einsatz-Board sichtbar

Auf dem Einsatz-Board (Desktop) erscheint neben den übrigen Kopfzeilen-Aktionen ein
Zähler-Widget mit der Anzahl Zusagen (grün) und Absagen (rot). Ein Klick darauf zeigt die
Namensliste. Auf Mobilgeräten wird dieser Bereich der Kopfzeile grundsätzlich nicht angezeigt
(gleiche Logik wie die übrigen Kopfzeilen-Aktionen).

---

## Troubleshooting

| Symptom | Mögliche Ursache |
|---|---|
| Testkarte kommt nicht an | Webhook-URL falsch/abgelaufen — in Teams einen neuen Connector anlegen und URL erneuern |
| Karte kommt an, aber ohne Kartenbild | „Kartenbild einbetten" deaktiviert, oder der Einsatz hat noch keine Koordinaten (Geocoding läuft asynchron im Hintergrund) |
| Übungen lösen keine Karte aus | „Übungseinsätze auch senden" ist aus (Standardeinstellung) |
| Zusage/Absage wird nicht erkannt (erweiterter Modus) | E-Mail-Adresse der antwortenden Person ist nicht im Mannschaftsregister hinterlegt |
| Keine Kanalbindung erfasst (erweiterter Modus) | Bot wurde dem Ziel-Kanal noch nicht hinzugefügt, oder die Bot-Anbindung ist serverseitig noch nicht freigeschaltet (siehe Hinweis oben) |

---

**Verwandt:** [Einsatz starten](Anwender-Einsatz-starten) · [SMS-Einsatzinfo, manueller Versand & SMS-Empfang](Administration-SMS-Einsatzinfo)
