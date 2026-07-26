# Objektverwaltung: Arbeitskopie-Workflow (Entwurf -> Freigabe -> Ueberarbeitung)

## Ziel

Ein freigegebenes Objekt bleibt waehrend einer Ueberarbeitung inhaltlich unveraendert
produktiv (Alarm-Matching, Objektblatt, Einsatzansicht, Android-Sync). Bearbeitet wird
ausschliesslich eine separate Arbeitskopie; erst ihre Freigabe ersetzt den produktiven
Stand. Ergaenzung zu docs/plans/objektverwaltung-plan.md (dort ist der einfache
Einzelfeld-Status beschrieben, der hier um Versionierung erweitert wird).

Stufe 1 (dieser Plan) versioniert Stammdaten + Kinddaten (BMA, Zusatzadressen, Gefahren,
Merkmale, Kontakte, Wohnanlage, Lagekarten-Symbole). **Dokumente sind ausgenommen** und
haengen weiterhin immer an der produktiven Objekt-Zeile.

## Ansatz: Arbeitskopie als eigene Objekt-Zeile, Merge bei Freigabe

Die produktive Zeile behaelt ihre `id` (referenziert von ObjektEinsatz, ObjektDokument,
ObjektChange, Medienpfaden, Sync-URLs). "Ueberarbeiten" legt eine tiefe Kopie an
(`Objekt.entwurf_von_id` zeigt auf die Basis, Status der Kopie = `entwurf`); die Basis
wechselt auf `in_ueberarbeitung`, ihre Daten bleiben unangetastet. "Freigeben" der Kopie
ist ein Merge: Felder + Kinddaten werden auf die Basis zurueckgeschrieben (Basis-`id`
bleibt stabil), die Kopie wird geloescht. "Verwerfen" loescht die Kopie ohne Merge.

Details, Datenmodell, Service-Funktionen (`erstelle_arbeitskopie`,
`uebernimm_arbeitskopie`, `verwirf_arbeitskopie`, `nur_produktiv`,
`hole_arbeitskopie` in `app/services/objekt_service.py`), Routen
(`POST /{id}/ueberarbeiten|uebernehmen|verwerfen` in `app/routers/ui_objekt.py`) und die
Migration (`alembic/versions/0180_objekt_arbeitskopie.py`) sind im Code dokumentiert.

## Statusmatrix-Aenderung

`in_ueberarbeitung -> freigegeben` ist ueber `POST /{id}/status` nicht mehr erlaubt (siehe
`OBJEKT_STATUS_UEBERGAENGE` in `app/models/objekt.py`) - der reguläre Weg ist die Uebernahme
der Arbeitskopie, nicht ein blinder Statuswechsel der Basis.

## Leak-Vermeidung

Arbeitskopien duerfen nie in Listen, Matching, Sync-Manifest oder Auswahlfeldern
auftauchen. Der Helper `nur_produktiv()` (`objekt_service.py`) haengt den Filter
`entwurf_von_id IS NULL` an; er ist an jeder betroffenen Abfragestelle gesetzt (Objektliste,
Alarm-Matching, Sync-Manifest, Revisions-Erinnerungen, Einsatz-Verknuepfungssuchen,
Planupload-Zielobjektsuche, Kategorie-Verwendungspruefung).

## Berechtigung

Nur `objekt_verwalter` (bzw. admin/org_admin/system_admin ueber `require_role`) darf
Arbeitskopien anlegen, uebernehmen oder verwerfen.

## Migration des Altbestands

Keine automatische Statusmigration - der heutige `entwurf`-Bestand bleibt `entwurf`. Die
Freigabe des Ist-Stands ist eine bewusste, manuelle Nutzeraktion (Einzel- oder
Sammelfreigabe in der Objektliste).
