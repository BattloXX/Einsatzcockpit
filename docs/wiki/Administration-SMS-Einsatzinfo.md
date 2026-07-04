# SMS-Einsatzinfo, manueller Versand & SMS-Empfang

← [Zurück zur Startseite](Home)

Voraussetzung für alle Funktionen auf dieser Seite: eine funktionierende [SMS-Gateway-Anbindung](Installation-SMS-Gateway) (Docker-Container oder Android-App). Ohne verbundenes Gateway wird kein SMS-Versand ausgelöst.

---

## SMS-Gruppen

Unter **Admin → SMS-Gruppen** (`/admin/gruppen`) werden Verteilergruppen aus aktiven Mitgliedern gebildet — Basis für Einsatzinfo-Verteiler, manuellen Versand und Weiterleitungsregeln. Mitglieder lassen sich einzeln zuordnen oder per Excel-Import bulk hinzufügen.

---

## Einsatzinfo-SMS (automatisch bei Alarm)

Unter **Admin → Einsatzinfo-SMS** (`/admin/einsatzinfo-sms`):

| Einstellung | Bedeutung |
|---|---|
| Aktiviert | Schaltet den automatischen Versand bei Alarm ein/aus |
| Bei Übungen senden | Standardmäßig **aus** — Übungseinsätze lösen keine Einsatzinfo-SMS aus, sofern nicht explizit aktiviert |
| Vorlage (Org-Standard) | Text mit Platzhaltern, gilt für alle Stichworte ohne eigene Vorlage |
| Basis-Verteiler | Gruppen + einzelne Mitglieder, die bei **jedem** Stichwort eine SMS erhalten |
| Verteiler je Stichwort | Zusätzliche Gruppen/Mitglieder sowie optional eine eigene Vorlage pro Alarmtyp (überschreibt die Org-Standard-Vorlage) |

**Verfügbare Platzhalter in der Vorlage:**

| Platzhalter | Ersetzung |
|---|---|
| `{stichwort}` | Alarmtyp-Code (z.B. B2, T1) |
| `{adresse}` | Straße + Ort zusammengesetzt |
| `{ort}` | Nur der Ort |
| `{meldung}` | Meldungstext |
| `{einsatzgrund}` | Einsatzgrund |
| `{datum}` | Datum der Alarmierung (TT.MM.JJJJ) |
| `{zeit}` | Uhrzeit der Alarmierung (HH:MM) |

Standard-Vorlage: `Einsatz {stichwort}: {adresse}. {meldung}`. Unbekannte Platzhalter werden stillschweigend durch einen leeren String ersetzt (kein Fehler bei Tippfehlern).

Der Versand läuft als Hintergrund-Task nach Einsatzanlage (egal ob über die API/Alarmierungssystem oder LIS) und protokolliert jeden Versand im SMS-Log (sichtbar unter **SMS senden**).

---

## Manueller SMS-Versand

Unter **Admin → SMS senden** (`/admin/sms-senden`): freier Text an eine oder mehrere **Gruppen**, einzelne **Mitglieder** oder eine **Ad-hoc-Nummer**. Zeigt an, ob das Gateway aktuell verbunden ist, sowie die letzten 30 Versand-Protokolle (Empfängerzahl, Erfolgsquote).

---

## SMS-Empfang & Weiterleitung

Unter **Admin → SMS-Empfang** (`/admin/sms-empfang`):

- **Aktivierung**: eingehende SMS werden nur verarbeitet, wenn „SMS-Empfang" für die Org eingeschaltet ist. Unabhängig davon wird **jede** eingehende SMS immer geloggt (letzte 50 Einträge sichtbar)
- **Weiterleitungsregeln**: pro Regel wird die Absendernummer gegen `match_number` geprüft — entweder **exakt** oder als **Präfix** (z.B. alle Nummern eines Mobilfunkbetreibers oder einer Vorwahl)
- Bei Treffer kann eine Regel weiterleiten an:
  - einen **Teams-Webhook** (Regel-eigener oder Org-Standard-Webhook)
  - **SMS-Gruppen** und/oder einzelne **Mitglieder**
  - **Ad-hoc-Nummern** (Freitext-Liste)
- **Absender voranstellen**: fügt die Absendernummer der SMS dem weitergeleiteten Text voran, damit der Ursprung der Nachricht nachvollziehbar bleibt
- Regeln lassen sich einzeln deaktivieren, ohne sie zu löschen

---

## SMS-Gateway: Docker-Container oder Android-App

Beide Wege verbinden sich über denselben Token-authentifizierten WebSocket (`/ws/sms-gateway`) mit Einsatzcockpit — Einsatzinfo-SMS, manueller Versand und SMS-Empfang funktionieren mit beiden identisch:

- **Docker-Container** (CoNiuGo-Modem im lokalen Netz) — siehe [SMS-Gateway einrichten](Installation-SMS-Gateway)
- **Native Android-App** (eigenes Repo `Einsatzcockpit-Android`) — nutzt die SIM-Karte eines Android-Geräts zum Senden/Empfangen, läuft als Foreground-Service im Hintergrund

Ist mehr als eine Gateway-Verbindung gleichzeitig aktiv, wählt der Versand automatisch die zuletzt verbundene/lebende Verbindung; getrennte Verbindungen werden bereinigt, ohne SMS doppelt zu versenden.

---

**Verwandt:** [SMS-Gateway einrichten](Installation-SMS-Gateway) · [Einstellungen](Administration-Einstellungen)
