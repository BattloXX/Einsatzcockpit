# LIS/IPR-Anbindung einrichten

← [Zurück zur Startseite](Home)

Einsatzcockpit kann sich direkt an das **Leitstellensystem (LIS/IPR)** der Landeswarnzentrale anbinden. Ein Hintergrund-Dienst gleicht dabei laufend die aktiven Einsätze der Organisation ab — ohne dass die Leitstelle eigens eine Anfrage an die Einsatzcockpit-API schicken muss.

**Zweck:** Automatischer Einsatz-/Übungseinsatzabgleich, Fahrzeugstatus/-position, Meldungen, Zu-/Absagen der Mannschaft und Dokumente — direkt aus dem Leitstellensystem, ohne manuelles Übertragen.

---

## Voraussetzungen

| Anforderung | Details |
|---|---|
| Zugangsdaten der Landeswarnzentrale | Basis-URL, Site-Kennung, Organisation-ID (GUID), Benutzername/Passwort für die SOAP/WCF-Schnittstelle |
| Rolle `org_admin` oder `admin` | Zum Einrichten der Verbindung unter `/admin/lis` |
| Alembic-Migration 0114 | Legt `org_lis_config` und `lis_synced_object` an (`alembic upgrade head`) |
| `FERNET_KEY` gesetzt | Das LIS-Passwort wird verschlüsselt gespeichert (wie SSO-Client-Secrets) |

---

## Schritt 1 — Verbindung konfigurieren

Unter **Admin → LIS / Leitstellenanbindung** (`/admin/lis`) für die eigene Organisation:

| Feld | Bedeutung |
|---|---|
| Aktiviert | Schaltet die Anbindung für diese Org ein/aus |
| Basis-URL | z.B. `https://lis.lwz-vorarlberg.at/ipr` |
| Organisation-ID (LIS-GUID) | Eindeutige Kennung der eigenen Feuerwehr im LIS |
| Site | Standardwert `LIS`, nur ändern wenn von der Leitstelle vorgegeben |
| Benutzername / Passwort | Zugangsdaten der SOAP-Schnittstelle; Passwort wird Fernet-verschlüsselt gespeichert |
| Poll-Intervall (Sek.) | **Nur für das Diagnose-Aufzeichnungstool** (Schritt 3) — der reguläre Hintergrund-Abgleich läuft global über `LIS_POLL_INTERVAL_S` (siehe unten), nicht pro Org |

Mit **Verbindung testen** prüft Einsatzcockpit Login + Abfrage der aktiven Einsätze, ohne etwas zu speichern — die Anzahl gefundener aktiver Einsätze wird zurückgemeldet.

System-Admins sehen zusätzlich eine Org-Auswahl, um die Anbindung für jede Organisation zu konfigurieren.

---

## Schritt 2 — Globalen Hintergrund-Dienst konfigurieren (.env)

```dotenv
LIS_ENABLED=true          # Globaler Kill-Switch für den Hintergrund-Poll-Loop
LIS_POLL_INTERVAL_S=30    # Poll-Intervall in Sekunden, gilt für alle Orgs
```

Der Loop läuft serverweit; einzelne Organisationen werden über den Schalter **Aktiviert** in Schritt 1 ein-/ausgeschlossen. Ein Fehler bei einer Org (z.B. falsches Passwort) blockiert nie den Zyklus für andere Organisationen.

---

## Was der Abgleich automatisch übernimmt

- **Einsätze und Übungseinsätze anlegen/verknüpfen** — existiert bereits ein über die API angelegter Einsatz mit passendem Stichwort/Adresse/Zeitpunkt, wird verknüpft statt dupliziert. Übungseinsätze werden anhand des LIS-Einsatztyps (`Schulung`, `Übung`, `Training`, `Probe`) erkannt und auch in Einsatzcockpit als Übungseinsatz markiert (gelb/schwarzer Banner, keine Statistik-Zählung)
- **Leitstellen-Nummer als führende Kennung** — wird überall angezeigt, wo sonst die interne Einsatz-ID stünde (Alarm-Kopfzeile, Archiv, Verlauf, PDF-Export)
- **Anrufer/Melder** — Name und Telefonnummer, sofern vom Alarmierungssystem/LIS mitgeliefert; nur Anzeige, keine weitere Verarbeitung
- **Fahrzeugstatus (S1–S6) und -position** — sofern das Fahrzeug über `lis_reference_id` in den Stammdaten zugeordnet ist (siehe [Stammdaten pflegen](Administration-Stammdaten-pflegen)); Positionen landen in derselben Historie wie App-GPS-Daten
- **Meldungen sowie Zu-/Absagen der Mannschaft** aus den LIS-Aufträgen des Einsatzes
- **Dokumente/Bilder**, die der Leitstelle zum Einsatz angehängt wurden
- **Automatisches Schließen** — verschwindet die Operation aus der aktiven Liste des LIS (weil sie dort abgeschlossen wurde), schließt Einsatzcockpit den verknüpften Einsatz automatisch mit (inkl. Widerruf von QR-/Lagekarte-Tokens, wie beim manuellen Abschließen)

---

## Diagnose-Aufzeichnung (nur System-Admin)

Für die Fehlersuche bei der Erstanbindung gibt es unter `/admin/lis` einen Button **Rohdaten aufzeichnen**: Er zeichnet den kompletten SOAP-Datenverkehr mit der Leitstelle für eine wählbare Dauer auf (Standard 120 Min., abbrechbar). Beim Beenden — egal ob Zeit abgelaufen oder **Abbrechen** geklickt — werden alle aufgezeichneten Rohdaten automatisch zu einer einzigen ZIP-Datei gebündelt.

- Aufzeichnungen werden **7 Tage** aufbewahrt und danach automatisch gelöscht
- Es gibt bewusst **keine Download-Route über HTTP** — die ZIP-Dateien liegen nur lokal auf dem Server (Datenschutz: Aufzeichnungen können personenbezogene Daten aus echten Einsätzen enthalten)
- Empfehlung: Aufzeichnung starten, während in LIS gezielt ein Testeinsatz mit Meldung/Auftrag/Fahrzeugstatus bearbeitet wird — das liefert die aussagekräftigsten Daten für die Fehlersuche

---

## Fehlerbehebung

| Symptom | Mögliche Ursache |
|---|---|
| „Verbindung testen" schlägt fehl | Basis-URL/Zugangsdaten prüfen; Firewall zwischen App-Server und LIS-Endpunkt |
| Verbindung ok, aber kein Einsatz wird angelegt | Serverlog prüfen (nicht nur die Diagnose-Aufzeichnung) — z.B. fehlende Python-Abhängigkeiten können den Hintergrund-Loop stillschweigend abbrechen, ohne dass die Diagnose-Aufzeichnung das zeigen kann |
| Einsatz wird angelegt, aber falsches Stichwort | LIS liefert das Alarmtyp-Kürzel z.T. mit Präfix (z.B. `t_t3` statt `t3`) — wird automatisch erkannt; bei neuen/unbekannten Formaten Serverlog prüfen |
| Fahrzeugstatus wird nicht übernommen | Fahrzeug hat keine `lis_reference_id` in den Stammdaten hinterlegt |

---

**Verwandt:** [Einsatz starten](Anwender-Einsatz-starten) · [Stammdaten pflegen](Administration-Stammdaten-pflegen)
