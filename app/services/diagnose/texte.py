"""Konkrete Ursachen und naechste Schritte je technischem Befundkern."""

TEXTE = {
    "migration": (
        "DB-Schema und ausgelieferter Code haben unterschiedliche Alembic-Staende.",
        ("Vor dem Neustart alembic upgrade head mit dem ausgelieferten Image ausfuehren; Migrationslog pruefen."),
    ),
    "pool": (
        "Gleichzeitige Requests belegen mindestens 90 Prozent des DB-Pools.",
        (
            "Worker-/Requestlast und offene Transaktionen pruefen; blockierende Abfragen "
            "vor einer Poolvergroesserung beheben."
        ),
    ),
    "langlaeufer": (
        "DB-Abfragen laufen laenger als 30 Sekunden.",
        (
            "Die angegebenen Prozess-IDs in MariaDB SHOW PROCESSLIST untersuchen und "
            "Abfrageplan sowie fehlende Indizes pruefen."
        ),
    ),
    "locks": (
        "InnoDB-Transaktionen warten auf Zeilensperren.",
        (
            "Blockierende Transaktion ueber information_schema.innodb_trx ermitteln und "
            "deren Laufzeit sowie Transaktionsumfang pruefen."
        ),
    ),
    "sha_unbekannt": (
        "Dem Image fehlt ein gueltiger GIT_SHA-Buildparameter.",
        "CI-Image pullen oder lokal --build-arg GIT_SHA=<Commit-SHA> setzen und neu starten.",
    ),
    "hinter_main": (
        "Das laufende Image enthaelt noch nicht alle main-Commits.",
        ("Erfolgreichen CI-/Docker-Build fuer main pruefen, Image pullen und Container neu erstellen."),
    ),
    "github.token": (
        "GitHub lehnt das hinterlegte Lesetoken ab oder es ist abgelaufen.",
        (
            "Unter Systemeinstellungen > GitHub-Zugang Ablaufdatum und Repository-Freigabe "
            "pruefen; neues fine-grained Lesetoken fuer Contents/Actions hinterlegen."
        ),
    ),
    "check_fehlgeschlagen": (
        ("Der isolierte Diagnoseprozess hat sein Budget ueberschritten oder eine Exception ausgeloest."),
        (
            "Exception-Typ und Dauer in der Evidenz pruefen; den genannten Check einzeln "
            "erneut starten und mit admin_code_analyse korrelieren."
        ),
    ),
    "email_dublette": (
        "Zwei Kontakte derselben Organisation verwenden dieselbe normalisierte E-Mail-Adresse.",
        (
            "Die Kontaktpaare oeffnen und feststellen, ob eine gemeinsame Adresse "
            "beabsichtigt ist; nur bestaetigte Dubletten zusammenfuehren."
        ),
    ),
    "seitenreferenz": (
        "Planquellen verweisen auf ungueltige oder nicht importierte Dokumentseiten.",
        (
            "Quellseiten der genannten Katplan-Elemente mit der Seitenzahl und den "
            "importierten Seiten abgleichen und Referenzen im Editor korrigieren."
        ),
    ),
    "pflichtdaten": (
        "Produktive Objekte haben einen leeren Pflichtnamen.",
        ("Die betroffenen Objekte in der Objektverwaltung filtern und einen fachlich korrekten Namen ergaenzen."),
    ),
    "mehrfach_claim": (
        "Mehrere SMS-Dispatch-Versuche desselben Jobs sind gleichzeitig offen.",
        (
            "Attempts und Lease des genannten SMS-Jobs vergleichen; Gateway-Praesenz und "
            "konkurrierende Worker pruefen, vor manuellem Neuversand Empfang bestaetigen."
        ),
    ),
    "sms_abgelaufen": (
        "Die Versandfrist offener SMS-Jobs ist abgelaufen.",
        (
            "SmsDispatchJob.expires_at und Gateway-Praesenz der betroffenen Jobs pruefen; "
            "bei weiterem Bedarf einen neuen Versand bewusst ausloesen."
        ),
    ),
    "sms_ack": (
        "Das SMS-Gateway hat bis zum Lease-Ende keine Empfangsquittierung geliefert.",
        (
            "Gateway-Verbindung und SmsDispatchAttempt zum betroffenen Job pruefen; Empfang"
            " vor erneutem Versand am Gateway abgleichen."
        ),
    ),
    "druck_ack": (
        "Der Druckauftrag blieb nach dem Versand ohne Abschlussquittierung.",
        (
            "PrintJob-Phase und Gateway-Logs zum Job pruefen und den physischen Ausdruck "
            "vor einem erneuten Druck kontrollieren."
        ),
    ),
    "transport_abgelaufen": (
        "Eine Gateway-Transportnachricht blieb bis zum Ablauf in queued.",
        (
            "Gateway-Praesenz und Transportverbindung pruefen; abgelaufenen Auftrag "
            "fachlich bewerten, bevor er erneut erstellt wird."
        ),
    ),
    "wt_dienst": (
        "Der konfigurierte W&T-Alarmdienst meldet down oder teilweise.",
        (
            "W&T-Verbindung, seriellen Port und Dienstmonitor der betroffenen Organisation "
            "pruefen und einen Testalarm ausloesen."
        ),
    ),
    "outbox_alt": (
        ("Suchindex-Auftraege warten ueber fuenf Minuten oder wurden mindestens fuenfmal versucht."),
        (
            "Suchindex-Worker und Fehlversuche der genannten search_outbox-IDs pruefen; "
            "danach Indexierung erneut anstossen."
        ),
    ),
    "index_alt": (
        "Die Quelle wurde nach ihrer letzten Indexierung geaendert.",
        ("search_outbox zur Quelle und Indexierungsworker pruefen; betroffene Dokumente neu indexieren."),
    ),
    "task_status": (
        "Auftraege enthalten einen Status ausserhalb der erlaubten Werte.",
        (
            "Status der genannten Auftraege mit TASK_STATUS_VALUES abgleichen und ueber die"
            " Auftragsverwaltung korrigieren."
        ),
    ),
    "offen_in_geschlossen": (
        "Abgeschlossene Einsaetze enthalten noch offene Auftraege.",
        (
            "Die genannten Auftraege im abgeschlossenen Einsatz pruefen und fachlich "
            "abschliessen oder den Einsatz wieder oeffnen."
        ),
    ),
    "task_flags": (
        "Auftragsstatus und is_done/is_cancelled widersprechen einander.",
        (
            "Die genannten Auftraege ueber den regulaeren Statuswechsel korrigieren und den"
            " ausloesenden Schreibpfad pruefen."
        ),
    ),
    "dispatch_status": (
        "Eine Disposition verwendet einen unbekannten Einheitenstatus.",
        ("Die Dispositionen mit EINHEIT_STATUS_WERTE abgleichen und den Status im Einsatzboard korrigieren."),
    ),
    "dispatch_lage": (
        "Dispositionen verbinden eine Einheit mit einer Stelle aus einer anderen Lage.",
        (
            "Die genannten Dispositionen im Board pruefen und der richtigen Lage zuordnen; "
            "Tenant-Zuordnung kontrollieren."
        ),
    ),
    "pruefung_fehlt": (
        "Freigegebene Katplaene haben kein dokumentiertes Pruefdatum.",
        "Den Plan fachlich pruefen und die Pruefung in der Katplan-Verwaltung dokumentieren.",
    ),
    "quelle_fehlt": (
        "Eine Elementquelle fehlt oder liegt in einem anderen Plan beziehungsweise Tenant.",
        ("Quellverknuepfung der genannten Elemente im Katplan-Editor auf ein Dokument desselben Plans setzen."),
    ),
    "zieltyp": (
        "Katplan-Links enthalten einen nicht unterstuetzten Zieltyp.",
        (
            "Die genannten Links im Katplan-Editor auf einen erlaubten Zieltyp umstellen "
            "oder fachlich ungueltige Links entfernen."
        ),
    ),
    "relation_ziel": (
        "Das Zielelement einer Katplan-Relation fehlt im selben Plan.",
        ("Relationen im Katplan-Editor pruefen und auf ein vorhandenes Element desselben Plans setzen."),
    ),
    "seiten_fehlen": (
        "Als fertig markierte Dokumente besitzen keine importierten Seiten.",
        ("Importstatus und Quelldatei der Dokument-IDs pruefen und den Dokumentimport erneut starten."),
    ),
    "seite_ohne_index": (
        "Dokumentseiten fehlen PDF-Verweis oder durchsuchbarer Text.",
        ("PDF-Erzeugung und OCR der genannten Seiten pruefen; betroffene Dokumente erneut verarbeiten."),
    ),
}

WORKER = {
    "ausfall": (
        "Ein Worker ist kuerzlich ohne aktiven Ersatz auf demselben Host abgelaufen.",
        "Worker-Dienst, Heartbeats und Neustart auf diesem Host pruefen.",
    ),
    "recht_fehlt": (
        "Der DB-Benutzer darf diese Systemtabelle nicht lesen.",
        "GRANT PROCESS ON *.* TO '<app-user>'@'<host>' optional",
    ),
    "abgelaufen": (
        "Worker-Heartbeats sind abgelaufen.",
        (
            "Worker-Neustarts und Heartbeat-Schreibzugriff pruefen; aktuelle aktive Worker "
            "mit admin_worker_pruefen vergleichen."
        ),
    ),
    "heartbeat_alt": (
        "Aktive Worker haben seit ueber 20 Sekunden keinen Heartbeat geschrieben.",
        (
            "Workerlast und DB-Latenz pruefen; bei anhaltendem Stillstand den betroffenen "
            "Worker kontrolliert neu starten."
        ),
    ),
    "mehrere_leader_pro_host": (
        "Mehrere Worker beanspruchen gleichzeitig die Fuehrung eines Hosts.",
        "Leader-Lease und Worker-Identitaeten pruefen; doppelte Instanzen kontrolliert stoppen.",
    ),
    "leader_fehlt": (
        "Kein aktiver Worker besitzt eine Leader-Lease.",
        ("Worker-Dienst und Leader-Wahl pruefen; sicherstellen, dass mindestens ein Worker Hintergrundjobs ausfuehrt."),
    ),
    "loop_fehler": (
        "Ein Worker-Loop ist fehlgeschlagen oder hat seine Ausfuehrungsfrist ueberschritten.",
        (
            "admin_worker_pruefen und Worker-Loop-Logs vergleichen; fehlgeschlagenen Dienst"
            " nach Ursachenbehebung neu starten."
        ),
    ),
}
REDIS = {
    "connected": "Redis-Verbindung ist unterbrochen",
    "publisher_ok": "Redis-Publisher kann nicht senden",
    "subscriber_connected": "Redis-Subscriber ist getrennt",
    "reconnects": "Redis-Verbindung musste erneut aufgebaut werden",
    "publish_failures": "Redis-Publish-Aufrufe sind fehlgeschlagen",
    "subscriber_failures": "Redis-Subscriber meldet Fehler",
}


def befund_texte(kern, titel):
    key = kern if isinstance(kern, str) else kern[-1] if isinstance(kern, list) and kern else ""
    if key in TEXTE:
        cause, step = TEXTE[key]
    elif key in WORKER:
        cause, step = WORKER[key]
    elif key in REDIS:
        cause, step = (
            REDIS[key] + ".",
            (
                "Redis-Erreichbarkeit und Worker-Verbindungsstatus pruefen; Zeitpunkte mit "
                "Redis-Neustarts und Backupjobs abgleichen."
            ),
        )
    elif key in ("ueberfaellig", "attempts", "presence"):
        table = str(kern[0])
        details = {
            "ueberfaellig": (
                "Die Versand-Lease ist abgelaufen oder der offene Auftrag wartet seit mehr als fuenf Minuten.",
                ("Lease und Jobstatus der genannten IDs mit der Gateway-Praesenz abgleichen; "
                 "Worker fuer die Versandqueue pruefen."),
            ),
            "attempts": (
                "Mindestens fuenf Versandversuche desselben offenen Auftrags sind registriert.",
                ("Attempt-Historie und letzten Gateway-Fehler pruefen; Empfang vor einem weiteren "
                 "Versand am Zielgeraet kontrollieren."),
            ),
            "presence": (
                "Gateway-Lease, Verbindungskennung oder Tokenabgleich ist ungueltig.",
                ("Gateway-Agent und Netzwerkverbindung pruefen; Registrierungsschluessel mit der "
                 "aktuellen Gateway-Konfiguration abgleichen."),
            ),
        }
        cause, step = details[key]
        cause = f"{table}: " + cause
    elif key == "datei":
        cause, step = (
            "Dokumentdatei fehlt oder liegt ausserhalb des erlaubten Medienverzeichnisses.",
            (
                "Medienvolume und Dateirechte der genannten Dokument-IDs pruefen; fehlende "
                "Dateien aus Backup wiederherstellen oder neu importieren."
            ),
        )
    elif key in ("bestand_fehlt", "bestand_veraltet", "pruefung", "importfehler"):
        cause, step = (
            f"OSM-Quelle {kern[0]}: Importzustand {key} verhindert einen aktuellen freigegebenen Bestand.",
            (
                "python -m app.cli osm-gebaeude-status ausfuehren; Quelle importieren "
                "beziehungsweise wartenden Lauf nach Pruefung aktivieren."
            ),
        )
    elif isinstance(kern, list) and kern and kern[0] == "link":
        cause, step = (
            f"Katplan-Link auf {key} hat kein gueltiges Ziel im selben Tenant.",
            ("Den genannten Katplan-Link im Editor auf ein vorhandenes Ziel derselben Organisation setzen."),
        )
    elif isinstance(kern, list) and len(kern) == 2:
        cause, step = (
            f"Referenz {kern[0]}.{key} hat kein Ziel im erlaubten Tenant.",
            f"Die genannten IDs in {kern[0]} auf {key} pruefen und die Beziehung ueber die Fachverwaltung korrigieren.",
        )
    else:
        cause, step = (
            f"Pruefung '{titel}' hat eine Abweichung festgestellt.",
            (f"Evidenz der Pruefung '{titel}' anhand der betroffenen IDs kontrollieren "
             "und den Check einzeln wiederholen."),
        )
    return "Hypothese: " + cause, step
