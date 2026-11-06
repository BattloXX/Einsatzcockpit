# Atemschutz-Überwachungsformular drucken

Das Board bietet neben ASSP die Aktionen **Formular (PDF)** und **Leeres Formular**.
Beide verwenden den vorhandenen `ecpgPrint`-Dialog: lokaler Druck oder Stationsdruck
über das Gateway. Im Archiv führt **Überwachungsformular (PDF)** zur PDF-Vorschau.

| Dokumenttyp | Bezug | Inhalt |
|---|---|---|
| `asuew_formular` | `incident_id` | Aktuelle Zyklen, drei Trupps je Formularseite |
| `asuew_formular_leer` | `incident_id` | Ein leeres Formular mit den Kontrollintervallen der Organisation |

Beide Typen sind in `DOCUMENT_TYPE_LABELS` registriert und werden über
`print_artifact_service.render_job_pdf` gerendert. Sie benötigen keine `artifact_ref`.
Wie `troop_protokoll` werden sie manuell gedruckt; sie haben keinen automatischen
Auslöser und stehen deshalb nicht in `RULE_DOCUMENT_LABELS` oder `TRIGGER_DOCUMENT_TYPES`.
Einsatzzugehörigkeit und effektive Modulfreigabe werden vor dem Rendering geprüft.

Direkte Vorschau (Anmeldung und aktives Atemschutzmodul erforderlich):

- `/einsatz/{incident_id}/atemschutz/formular.pdf`
- `/einsatz/{incident_id}/atemschutz/formular-leer.pdf`

Das ausgefüllte Formular berücksichtigt ausschließlich den aktuellen Zyklus. Frühere
Deployments stehen weiterhin im einzelnen Atemschutz-Protokoll zur Verfügung.
Lagemeldungen, Funkverlust und Notfall werden aus der Audit-Historie seit dem letzten
erneuten Einsatz ergänzt; Druckmeldungen und Kontrollen kommen aus dem aktuellen Zyklus.
Zeitangaben verwenden die Zeitzone der Organisation. Bei vielen Meldungen können
zusätzliche Fortsetzungsseiten entstehen; Meldungen werden nicht abgeschnitten.

Das Blankoformular enthält keine Einsatz- oder Personendaten. Die digitale
Überwachung ergänzt Überwachungstafel und Papierformular; das ausgedruckte leere
Formular dient als Rückfallebene bei Ausfall der digitalen Überwachung.
