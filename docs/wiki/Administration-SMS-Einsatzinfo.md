# SMS-Einsatzinfo, manueller Versand & SMS-Empfang

← [Zurück zur Startseite](Home)

SMS kann entweder über eine verbundene [SMS-Gateway-Android-App](Installation-SMS-Gateway) oder über den konfigurierten EUS-Versandweg gesendet werden. EUS funktioniert unabhängig davon, ob ein Gateway verbunden ist.

Die Seiten **SMS senden**, **SMS-Versandweg**, **SMS-Empfang** und **SMS-Einsatzinfo** sind unter einem gemeinsamen Menüpunkt **SMS** als vier Tabs erreichbar. Ihre bisherigen URLs `/admin/sms-senden`, `/admin/sms-provider`, `/admin/sms-empfang` und `/admin/einsatzinfo-sms` bleiben gültig.

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
| `{link}` | Öffentlicher Link zur Einsatzinformation ohne Anmeldung |
| `{leitstellennummer}` | Leitstellen-Einsatznummer, falls vorhanden (nicht Teil der Standard-Vorlage) |

Standard-Vorlage: `Einsatz {stichwort}: {adresse}. {meldung} {link}`. Unbekannte Platzhalter werden stillschweigend durch einen leeren String ersetzt (kein Fehler bei Tippfehlern).

Der Versand läuft als Hintergrund-Task nach Einsatzanlage (egal ob über die API/Alarmierungssystem oder LIS) und protokolliert jeden Versand im SMS-Log (sichtbar unter **SMS senden**).

Für Großschadenslagen gibt es zusätzlich einen eigenen GSL-Sonderalarm. Er wird unabhängig von der stichwortbezogenen Einsatzinfo über das Feature-Flag `gsl_alarm_enabled` aktiviert und verwendet den Basis-Verteiler.

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

## SMS-Gateway: native Android-App

Der SMS-Versand/-Empfang läuft über die native Einsatzcockpit-Android-App (eigenes Repo `Einsatzcockpit-Android`), die sich über einen Token-authentifizierten WebSocket (`/ws/sms-gateway`) mit Einsatzcockpit verbindet und die SIM-Karte des Android-Geräts zum Senden/Empfangen nutzt (Foreground-Service, dauerhaft im Hintergrund) — siehe [SMS-Gateway einrichten](Installation-SMS-Gateway).

Mehrere Gateways können gleichzeitig registriert und als Fallbacks verwendet werden. Unter **Admin → Geräte-Login → SMS-Gateways** wird ihre Priorität mit den Auf-/Ab-Pfeilen festgelegt; der SMS-Versand versucht die verbundenen Gateways in dieser Reihenfolge.

---

**Verwandt:** [SMS-Gateway einrichten](Installation-SMS-Gateway) · [Einstellungen](Administration-Einstellungen)
