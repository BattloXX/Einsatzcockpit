# GSL-Einstellungen

← [Zurück zur Startseite](Home)

> URL: `/admin/gsl-einstellungen` · zugänglich für: `org_admin`, `admin`

Die Einstellungen gelten für die eigene Organisation. Änderungen werden mit **Einstellungen speichern** übernommen.

## Module

Die Schalter aktivieren das jeweilige GSL-Modul für die Organisation, sofern es systemweit verfügbar ist. Standard: aktiviert.

| Bezeichnung | Wirkung |
|---|---|
| Stab (SKKM-Besetzungstafel) | Stab-Modul ein- oder ausschalten. |
| Funkjournal | Funkjournal ein- oder ausschalten. |
| Bürgermeldungen | Bürgermeldungen ein- oder ausschalten. |
| Abschnitte / Sektoren | Abschnitte und Sektoren ein- oder ausschalten. |
| Lagekarte | Lagekarte ein- oder ausschalten. |
| Zeitreise (Audit-Replay) | Audit-Replay ein- oder ausschalten. |
| Ressourcenmanagement | Ressourcenmanagement ein- oder ausschalten. |
| Übergreifende Meldungen | Übergreifende Meldungen ein- oder ausschalten. |
| Geräteverleih | Geräteverleih ein- oder ausschalten. |

## Lagemeldungs-Regelkreis (SKKM)

| Bezeichnung | Wirkung | Wertebereich / Standard |
|---|---|---|
| Standard-Intervall (Minuten) | Zeit bis zur fälligen Lagemeldung. | 5–480; leer deaktiviert den Timer. Modellstandard 60. |
| Sofort-Intervall (Minuten) | Eigenes Intervall für Priorität „Sofort“. | 5–120; leer verwendet das Standard-Intervall. Modellstandard 30. |
| Automatischen Funkjournal-Auftrag bei Überfälligkeit erstellen | Erstellt bei überfälliger Meldung einen Funkjournal-Auftrag. | Schalter; Standard aktiviert. |

## Sonderalarm bei Ausrufung

| Bezeichnung | Wirkung | Wertebereich / Standard |
|---|---|---|
| Sonderalarm senden (SMS + Teams, sofern jeweils aktiviert) | Sendet beim Ausrufen einer neuen GSL einmalig SMS und Teams, wenn der jeweilige Versand konfiguriert ist. | Schalter; Standard aktiviert. |
| Text | Nachricht des Sonderalarms. | Freitext; Platzhalter `{lage}`. Leer verwendet keinen eigenen Text. |

## Geräteverleih

| Bezeichnung | Wirkung | Wertebereich / Standard |
|---|---|---|
| Auto-Erinnerung nach X Stunden | Zeitpunkt für die automatische Erinnerung an offene Ausleihen. | 1–168; leer deaktiviert. |
| SMS-Vorlage: Ausleihe | Text der Ausleih-SMS. | Freitext; `{artikel}`, `{org}`, `{name}`. |
| SMS-Vorlage: Erinnerung | Text der Erinnerungs-SMS. | Freitext; `{artikel}`, `{org}`, `{name}`. |

## Gruppenkommandanten-Zugang

| Bezeichnung | Wirkung | Wertebereich / Standard |
|---|---|---|
| Gruppenkommandanten-Zugang aktivieren | Erlaubt persönliche GK-Zugänge zum Einheitenmodus. | Schalter; Standard aus. Beim Ausschalten werden persönliche Zugänge widerrufen. |
| SMS bei Gruppenkommandanten-Zuweisung automatisch versenden | Sendet den persönlichen Link bei Zuweisung automatisch. | Schalter; nur wirksam mit aktivem Gesamtschalter; Standard aus. |
| SMS an Gruppenkommandanten auch bei Übungen senden | Erlaubt GK-SMS in Übungen. | Schalter; unabhängig vom Einsatzinfo-SMS-Schalter; Standard aus. |
| Nachrichtenvorlage | Text der Zugangs-SMS. | Freitext; `{lage}`, `{einheit}`, `{gruppenkommandant}`, `{link}`; leer nutzt die Standardnachricht. |
| Auftrags-SMS automatisch senden | Sendet bei Auftragsereignissen eine SMS. | Schalter; bestätigte GK-Mobilnummer erforderlich; Standard aus. |
| Vorlage Auftrags-SMS | Text der Auftrags-SMS. | Freitext; `{ereignis}`, `{gsl}`, `{einheit}`, `{einsatzstelle}`, `{auftrag}`, `{link}`; leer nutzt die Standardnachricht. |
| Gültigkeit (Stunden) | Gültigkeitsdauer eines persönlichen Zugangs. | 1–168; Standard 48. |
| Sitzungsdauer (Stunden) | Höchstdauer einer Sitzung. | 1–72; Standard 12; nicht länger als die Gültigkeit. |
| Max. Sitzungen | Gleichzeitig zulässige Sitzungen. | 1–5; Standard 2. |
| SMS-Bestätigungscode beim Öffnen | Fordert beim Öffnen des persönlichen Links einen SMS-Code an. | Schalter; Standard aus; benötigt SMS-Anbieter. |
| Gruppenkommandant darf Personal/Ausstattung pflegen | Gibt dem persönlichen GK-Zugang diese Bearbeitung frei. | Schalter; Standard aus. |

### QR-Zugang (Ausdruck)

| Bezeichnung | Wirkung | Wertebereich / Standard |
|---|---|---|
| QR-Zugang aktivieren | Erlaubt QR-Zugänge für Einheiten. | Schalter; Standard aus. Beim Ausschalten werden QR-Zugänge widerrufen. |
| Gültigkeit (Stunden) | Gültigkeit eines QR-Zugangs. | 1–168; Standard 72. |
| PIN beim QR-Zugang verlangen | Fordert beim Öffnen des QR-Zugangs eine PIN an. | Schalter; Standard aus. Die PIN steht nie auf dem Ausdruck. |

### Notbremse

**Alle Zugänge dieser Organisation widerrufen** widerruft sofort alle Gruppenkommandanten-Zugänge der Organisation.

## Allgemein

| Bezeichnung | Wirkung | Wertebereich / Standard |
|---|---|---|
| Einsätze automatisch als Einsatzstellen in aktiver GSL übernehmen (mi_auto_adopt) | Übernimmt Einsätze als Einsatzstellen in die aktive GSL. | Schalter; Standard aktiviert. |

Die Seite bietet für beide SMS-Vorlagen jeweils **Standard wiederherstellen** und Vorschau; bei aktivem SMS-Bestätigungscode ohne SMS-Anbieter zeigt sie nach dem Speichern eine Warnung.
