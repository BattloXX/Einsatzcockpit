from pydantic_settings import BaseSettings, SettingsConfigDict

SECRET_KEY_PLACEHOLDER = "change-me-in-production"
BOOTSTRAP_PASSWORD_PLACEHOLDER = "admin"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "mysql+pymysql://einsatzleiter:pw@127.0.0.1:3306/einsatzleiter"
    SECRET_KEY: str = SECRET_KEY_PLACEHOLDER
    SESSION_MAX_AGE_SECONDS: int = 86400    # 24 Stunden (normaler Benutzer)
    SESSION_INACTIVITY_SECONDS: int = 28800  # 8 Stunden Inaktivitäts-Timeout
    # "Login merken": längeres, gleitendes Fenster. Solange der Nutzer mindestens
    # alle 7 Tage aktiv ist, bleibt er bis zur absoluten Obergrenze (30 Tage) eingeloggt.
    SESSION_REMEMBER_INACTIVITY_SECONDS: int = 604800   # 7 Tage Inaktivität (gleitend)
    SESSION_REMEMBER_MAX_AGE_SECONDS: int = 2592000     # 30 Tage absolute Obergrenze

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8092
    APP_BASE_URL: str = "http://localhost:8092"
    PUBLIC_BASE_URL: str = ""  # Für Mail-Links; leer = falls leer APP_BASE_URL verwenden
    APP_VERSION: str = "3.7.0"
    DEBUG: bool = False
    TEST_SYSTEM: bool = False

    # Slow-Query-Logging: Queries oberhalb dieser Schwelle (ms) landen als
    # WARNING im Log ("einsatzleiter.slow_query") — Messbasis für gezielte
    # Index-/N+1-Optimierungen. 0 = deaktiviert.
    SLOW_QUERY_LOG_MS: int = 300

    # Redis Pub/Sub-Bus für worker-übergreifende WebSocket-Zustellung. Leer = aus:
    # dann läuft alles In-Process (korrekt bei -w 1). Bei -w 2+ (siehe
    # deploy/einsatzleiter.service) MUSS dies gesetzt sein, sonst erreichen Broadcasts
    # und Gateway-Druckaufträge nur den Worker, der den jeweiligen Socket hält.
    REDIS_URL: str = ""

    # Brand-Identität (Einsatzcockpit)
    APP_NAME: str = "Einsatzcockpit"
    APP_TAGLINE: str = "Echtzeit-Führung im Einsatz"
    APP_DOMAIN: str = "einsatzcockpit.com"

    # Öffentlicher Bereich (Pre-Login-Website). Als Konstanten konfigurierbar,
    # damit die Marketing-Seiten (Header-Button "Zum Login", GitHub-Links,
    # Kontakt) ohne Markup-Änderung angepasst werden können.
    # PUBLIC_LOGIN_URL bewusst relativ ("/login"): oeffentliche Seite und App
    # laufen auf derselben Instanz -- so loggt jedes Deployment (Produktion wie
    # test.einsatzcockpit.com) in seine EIGENE Instanz ein, nicht in eine fest
    # verdrahtete Fremd-Domain.
    PUBLIC_LOGIN_URL: str = "/login"
    PUBLIC_GITHUB_URL: str = "https://github.com/BattloXX/einsatzcockpit"
    PUBLIC_CONTACT_EMAIL: str = "johannes@battlogg.org"

    # Cookie-Flags
    COOKIE_SECURE: bool = False  # In Produktion auf true (HTTPS)

    # Reverse-Proxy: X-Forwarded-For/-Proto nur akzeptieren, wenn die Anfrage
    # von einer der TRUSTED_PROXY_IPS kommt (Komma-getrennt; "*" = alle, nicht
    # empfohlen). Läuft die App OHNE vorgelagerten Proxy direkt am Netz,
    # TRUST_PROXY_HEADERS=false setzen — sonst wäre die Client-IP für
    # Rate-Limits und Audit-Logs per gefälschtem Header frei wählbar.
    TRUST_PROXY_HEADERS: bool = True
    TRUSTED_PROXY_IPS: str = "127.0.0.1,::1"

    # Die gesamte App per <iframe> auf externen (vertrauenswürdigen) Seiten einbettbar
    # machen (z. B. Wetter-/Dashboard-Kacheln auf eus.fwwo.at, Fahrtenbuch auf
    # feuerwehr.wolfurt.at). Leerzeichen-getrennte Liste erlaubter Eltern-Origins
    # (CSP frame-ancestors, Wildcard-Subdomain wie "https://*.fwwo.at" erlaubt).
    # Leer = Einbettung nur same-origin (strikter Fallback, DENY für alle übrigen Routen).
    TRUSTED_FRAME_ANCESTORS: str = "https://feuerwehr.wolfurt.at https://*.fwwo.at"

    VAPID_PRIVATE_KEY: str = ""
    VAPID_PUBLIC_KEY: str = ""
    VAPID_CLAIM_EMAIL: str = "admin@feuerwehr-wolfurt.at"

    # Firebase Cloud Messaging (native Android Push)
    FCM_ENABLED: bool = False
    FCM_PROJECT_ID: str = ""
    # Pfad zur Service-Account-JSON-Datei (außerhalb des Repos!)
    FCM_CREDENTIALS_PATH: str = ""

    BOOTSTRAP_ADMIN_USER: str = "admin"
    BOOTSTRAP_ADMIN_PASSWORD: str = ""  # Leer → wird beim ersten Start zufällig generiert

    PDF_LOGO_PATH: str = "app/static/img/Logo-rot.png"

    # IANA-Zeitzone fuer Anzeige von Datums-/Zeitwerten, wenn die Org keine eigene
    # Zeitzone konfiguriert hat. DB-Werte bleiben immer UTC.
    DEFAULT_TIMEZONE: str = "Europe/Vienna"

    # SMTP / Mail
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_STARTTLS: bool = True
    SMTP_TIMEOUT: int = 15

    PASSWORD_RESET_TTL_MIN: int = 30

    # Login-Lockout
    LOGIN_MAX_FAILED: int = 10
    LOGIN_LOCKOUT_MINUTES: int = 15

    # Update-Mechanismus: erwarteter SHA256 der nächsten Release-ZIP (optional;
    # wenn gesetzt, muss er auch im Upload-Form vom Admin angegeben werden)
    UPDATE_ZIP_REQUIRE_HASH: bool = True

    # Media-Upload (Auftrag-Anhaenge)
    # Storage liegt bewusst AUSSERHALB von app/static, damit Dateien nur ueber
    # die geschuetzte Route /medien/datei/{id} ausgeliefert werden (Org-Check).
    MEDIA_STORAGE_DIR: str = "app_storage/incident_media"
    MAX_UPLOAD_BYTES_IMAGE: int = 10 * 1024 * 1024   # 10 MB
    MAX_UPLOAD_BYTES_PDF:   int = 20 * 1024 * 1024   # 20 MB
    MAX_UPLOAD_BYTES_VIDEO: int = 50 * 1024 * 1024   # 50 MB
    MEDIA_IMAGE_MAX_WIDTH:  int = 1920
    MEDIA_IMAGE_MAX_HEIGHT: int = 1080
    MEDIA_THUMB_SIZE: int = 240
    MEDIA_VIDEO_MAX_HEIGHT: int = 720
    FFMPEG_BIN: str = "ffmpeg"   # ggf. absoluter Pfad ueber ENV

    # Objektverwaltung: Dokumenten-Pipeline (PDF-Zerlegung + Rasterung)
    OBJEKT_MEDIA_DIR: str = "app_storage/objekt_media"
    OBJEKT_PDF_MAX_BYTES: int = 100 * 1024 * 1024  # 100 MB je Datei
    OBJEKT_PDF_MAX_SEITEN: int = 300               # Seiten je Datei
    OBJEKT_SEITE_RENDER_DPI: int = 150             # Hi-Res-Rasterung (pdf2image/Poppler)
    OBJEKT_SYMBOL_MAX_BYTES: int = 512 * 1024      # 512 KB je hochgeladenem Symbolbild (SVG/PNG)
    # Volltext-Indexierung der Dokumentseiten (Suche nach Raum/Melderlinie/…)
    OBJEKT_OCR_ENABLED: bool = True                # OCR-Fallback fuer Scan-PDFs (Tesseract)
    OBJEKT_OCR_MIN_CHARS: int = 20                 # unter dieser Textlaenge → OCR versuchen
    OBJEKT_OCR_LANG: str = "deu+eng"               # Tesseract-Sprachpakete
    OBJEKT_VOLLTEXT_MAX_CHARS: int = 100_000        # Kappung je Seite

    # Nachschlagewerke (Gefahrgut-Suche, Rettungsdatenblaetter, Karten-Overlays).
    # Sync-Downloads landen hier (persistent, ausserhalb des read-only Repo-app/data);
    # Fallback-Seed bleibt app/data/bam_gefahrgut.csv.
    NACHSCHLAGEWERK_DATA_DIR: str = "app_storage/nachschlagewerk"
    # Taeglicher Sync des Gefahrgut-Datensatzes (BAM/ADR, dl-de). False = aus.
    NACHSCHLAGEWERK_SYNC_ENABLED: bool = True
    # Quelle des vollstaendigen Gefahrgut-Datensatzes (;-getrennte CSV, dl-de).
    # Leer = kein Download, es bleibt beim gebuendelten Seed (offline nutzbar).
    NACHSCHLAGEWERK_GEFAHRGUT_URL: str = ""
    # On-demand-Quelle fuer Rettungsdatenblaetter (PDF), URL-Template mit Platzhaltern
    # {hersteller} und {modell} (bereits urlsafe eingesetzt). Leer = kein Abruf,
    # nur Deep-Links auf externe Freigabe-Quellen (Euro Rescue). Rechtlich: keine
    # Massen-Spiegelung, nur Einzelabruf bei Bedarf.
    NACHSCHLAGEWERK_RETTUNGSKARTEN_URL_TEMPLATE: str = ""
    NACHSCHLAGEWERK_RETTUNGSKARTEN_MAX_BYTES: int = 25 * 1024 * 1024  # 25 MB je PDF
    # Katalog verfuegbarer Rettungskarten (Euro NCAP / CTIF "Euro Rescue"): offene,
    # fuer Einsatzkraefte frei bereitgestellte JSON-Katalog-API mit >2000 Modellen
    # inkl. direkter (deutscher) Rettungsblatt-PDFs. Taeglich gesynct (Metadaten +
    # PDF-Link); die PDFs werden erst beim Oeffnen on-demand offline gecacht.
    # Leer = kein Katalog-Sync (es bleibt bei Einzelabruf/Deep-Links).
    NACHSCHLAGEWERK_RETTUNGSKARTEN_KATALOG_URL: str = "https://api.rescue.euroncap.com/euro-rescue/variants"

    # KI-Integration (Anthropic Claude)
    ANTHROPIC_API_KEY: str = ""
    AI_ENABLED: bool = False
    AI_MODEL_DEFAULT: str = "claude-sonnet-4-6"
    AI_MODEL_FAST: str = "claude-haiku-4-5-20251001"
    AI_MAX_TOKENS: int = 1500
    AI_TIMEOUT: int = 20

    # Rate-Limits
    LOGIN_RATELIMIT: str = "10/minute"          # POST /login – IP-basiert
    API_ALARM_RATELIMIT: str = "60/minute"      # POST /api/v1/einsatz – Key-basiert
    UPLOAD_RATELIMIT: str = "20/minute"         # Medien-Uploads – IP-basiert

    # Lagekarte.info GeoJSON-Endpoint
    LAGEKARTE_CORS_ORIGINS: str = "https://www.lagekarte.info,https://lagekarte.info"
    LAGEKARTE_GEOJSON_RATELIMIT: str = "60/minute"

    # Nominatim Geocoding (OSM – kein API-Key nötig, User-Agent Pflicht!)
    NOMINATIM_BASE_URL: str = "https://nominatim.openstreetmap.org"
    NOMINATIM_USER_AGENT: str = "Einsatzcockpit/2.x (contact: office@einsatzcockpit.com)"
    NOMINATIM_TIMEOUT_SECONDS: float = 5.0

    # Photon Adress-Autocomplete (OSM/komoot – kein API-Key, über Backend geproxyt)
    PHOTON_BASE_URL: str = "https://photon.komoot.io"
    PHOTON_TIMEOUT_SECONDS: float = 4.0
    PHOTON_CACHE_TTL_SECONDS: int = 300
    PHOTON_SUGGEST_LIMIT: int = 8
    DEFAULT_INCIDENT_CITY: str = "Wolfurt"   # Fallback wenn Home-Org kein city hat

    # Hydranten / Löschwasser (OpenStreetMap / OSMHydrant, via Overpass server-seitig geproxyt)
    HYDRANT_ENABLED: bool = True
    HYDRANT_OVERPASS_URL: str = "https://overpass-api.de/api/interpreter"
    HYDRANT_RADIUS_M: int = 300               # Suchradius um den Einsatzort (Standard)
    HYDRANT_RADIUS_EINSATZINFO_M: int = 2000  # Erweiterter Radius für die Einsatzinfo-Karte
    HYDRANT_TIMEOUT_SECONDS: float = 8.0
    HYDRANT_CACHE_TTL_SECONDS: int = 3600     # In-Memory-Cache je gerundeter Koordinate
    HYDRANT_MAX: int = 40                     # max. zurückgegebene Entnahmestellen (Standard)
    HYDRANT_MAX_EINSATZINFO: int = 120        # max. Entnahmestellen im 2-km-Radius (Liste lädt nach)
    HYDRANT_USER_AGENT: str = "Einsatzcockpit/1.0 (+https://einsatzcockpit.com)"
    # Eigene Wasserstellen-Stammdaten haben Vorrang; OSM-Hydranten näher als dieser
    # Wert an einer eigenen Wasserstelle werden ausgeblendet (kein Doppelbild).
    WASSERSTELLE_OSM_DEDUPE_M: int = 25

    # ── Höhen-Service (Förderstrecken-Planer, PR 3) ──────────────────────────
    # Primär: Open-Meteo Elevation API (frei, batch-fähig). Höhenservice Österreich
    # (geoland.at) als optionale, präzisere Primärquelle konfigurierbar; leer = nur Open-Meteo.
    HOEHEN_AT_URL: str = ""                    # z. B. geoland.at-Höhenabfrage; leer = deaktiviert
    HOEHEN_OPENMETEO_URL: str = "https://api.open-meteo.com/v1/elevation"
    HOEHEN_TIMEOUT_SECONDS: float = 8.0
    HOEHEN_BATCH_MAX: int = 100               # max. Punkte je HTTP-Abfrage
    HOEHEN_CACHE_TTL_SECONDS: int = 86400      # In-Memory-Cache (Höhen ändern sich nicht)
    HOEHEN_USER_AGENT: str = "Einsatzcockpit/1.0 (+https://einsatzcockpit.com)"
    # Straßen-Routing für den Förderstrecken-Planer (Start→Ende der Förderleitung entlang
    # der Straße). OSRM-Instanz (öffentlicher Demo-Server als Default; für Produktion eigene
    # Instanz konfigurieren). Leer = Routing deaktiviert (nur manuelles Zeichnen).
    ROUTING_OSRM_URL: str = "https://router.project-osrm.org"
    ROUTING_PROFILE: str = "driving"           # OSRM-Profil (driving/walking/cycling)
    ROUTING_TIMEOUT_SECONDS: float = 8.0
    ROUTING_USER_AGENT: str = "Einsatzcockpit/1.0 (+https://einsatzcockpit.com)"
    # Vereinfachung der Routen-Geometrie (Douglas-Peucker) in Metern: weniger Stützpunkte
    # → die Förderleitung lässt sich mit wenigen Griffen verschieben (0 = keine Vereinfachung).
    ROUTING_SIMPLIFY_TOLERANCE_M: float = 25.0
    # Gefahren der Nachbarobjekte im Umkreis des Einsatzobjekts (Einsatzinfo-Karte)
    NACHBAR_GEFAHR_RADIUS_M: int = 400

    # Wetter (GeoSphere Austria / ZAMG — CC BY 4.0, Standardquelle / Fallback ohne API-Key)
    WEATHER_ENABLED: bool = True
    GEOSPHERE_BASE_URL: str = "https://dataset.api.hub.geosphere.at/v1"
    # Amtliche Warnungen: produktive ZAMG-Warn-API (openapi.hub.geosphere.at/warnapi liefert 404)
    GEOSPHERE_WARN_URL: str = "https://warnungen.zamg.at/wsapp/api"
    WEATHER_NOWCAST_RESOURCE: str = "nowcast-v1-15min-1km"
    WEATHER_NWP_RESOURCE: str = "nwp-v1-1h-2500m"
    WEATHER_STATION_RESOURCE: str = "tawes-v1-10min"
    WEATHER_CACHE_TTL_NOWCAST: int = 300     # 5 min
    WEATHER_CACHE_TTL_NWP: int = 1800        # 30 min
    WEATHER_CACHE_TTL_WARN: int = 300        # 5 min
    WEATHER_HTTP_TIMEOUT: int = 8
    WEATHER_RADIUS_KM: int = 15
    WEATHER_FALLBACK_OPENMETEO: bool = True
    # Windy.com Vollkarte als zusätzlicher Radar-Tab per <iframe> (extern → Datenschutz).
    WEATHER_WINDY_ENABLED: bool = True

    # Kachelmann Wetter (kostenpflichtige Plus-API) — Primärquelle wenn API-Key gesetzt.
    # Key wird i.d.R. in den Systemeinstellungen (kachelmann_api_key) gepflegt; ENV = Fallback.
    KACHELMANN_BASE_URL: str = "https://api.kachelmannwetter.com/v02"
    KACHELMANN_API_KEY: str = ""

    # Lokale Wetterstationen (Davis/Meteobridge etc.) — je Org per Push-Ingest.
    # Zeitreihen-Historie liegt in einer SEPARATEN DB (eigener Pool) damit die
    # operative DB nicht aufgebläht wird und Einsatz-Funktionen Vorrang behalten.
    # Leer ⇒ Zeitreihen-Persistenz/Ingest deaktiviert (nur Ist-Stand in der Haupt-DB
    # wäre dann ebenfalls nicht möglich, daher für das Feature setzen).
    WEATHER_DATABASE_URL: str = ""
    # Ingest-Endpoint (Meteobridge-Push) global aktiv/deaktiv.
    WEATHER_STATION_INGEST_ENABLED: bool = True
    # Aufbewahrungsdauer der Zeitreihe in Tagen; ältere Messwerte werden täglich gelöscht.
    WEATHER_READING_RETENTION_DAYS: int = 365
    # Aufbewahrungsdauer der GPS-Positionshistorie (vehicle_position) in Tagen.
    # Positionen abgeschlossener Lagen + Archiv werden behalten; nur sehr alte Daten werden entfernt.
    VEHICLE_POSITION_RETENTION_DAYS: int = 90
    # Mindestabstand zwischen zwei akzeptierten Pushes je Station (Flood-Schutz).
    WEATHER_INGEST_MIN_INTERVAL_S: int = 60

    # Wetterwarnungen – automatischer Versand per Mail / Teams
    WEATHER_ALERTS_ENABLED: bool = True      # globaler Kill-Switch
    WEATHER_ALERT_INTERVAL_S: int = 300      # Loop-Intervall (5 min)
    BODENSEE_TEMP_FETCH_ENABLED: bool = False  # optionaler externer Adapter (nicht aktiv)
    BODENSEE_TEMP_SOURCE_URL: str = ""       # URL für externen Temperatur-Adapter

    # Pegelmessstationen – kontinuierliches Polling unabhängig von Seitenaufrufen
    # (ohne diesen Loop entstehen Lücken im 24-h-Verlauf, wenn niemand die Wetterseite öffnet)
    ABFLUSS_POLL_ENABLED: bool = True
    ABFLUSS_POLL_INTERVAL_S: int = 600       # Loop-Intervall (10 min, = abfluss_service._FETCH_TTL_S)

    # Datenbank-Backup + Restore-Probe (app/services/backup_service.py, app.cli backup)
    # Zielverzeichnis der Dumps (persistent, ausserhalb des Repos; im DR-Runbook
    # zusaetzlich an einen zweiten Ort spiegeln — 3-2-1-Regel).
    BACKUP_DIR: str = "app_storage/backups"
    # Aufbewahrung: so viele der neuesten Dumps je Datenbank/Medien behalten.
    BACKUP_KEEP_DAILY: int = 14
    # Medien (app_storage) mitsichern (Einsatzfotos, Objektdokumente, Nachschlagewerke).
    BACKUP_INCLUDE_MEDIA: bool = True
    # Binaries (ggf. absoluter Pfad ueber ENV). MariaDB 10.11 liefert mariadb/mariadb-dump.
    BACKUP_DUMP_BIN: str = "mariadb-dump"
    BACKUP_CLIENT_BIN: str = "mariadb"
    # Wegwerf-DB fuer die Restore-Probe. Wird bei jedem Lauf neu angelegt und danach
    # verworfen; darf NIEMALS gleich der Produktions-DB sein (harte Pruefung im Code).
    BACKUP_RESTORE_TEST_DB: str = "einsatzleiter_restore_test"

    # Off-Site-Upload der Backups (app/services/remote_backup_service.py).
    # Laeuft nach jedem `app.cli backup` automatisch, wenn aktiviert; separat per
    # `app.cli backup-upload` testbar. Erfuellt die 3-2-1-Regel (zweiter Ort).
    BACKUP_REMOTE_ENABLED: bool = False
    # Protokoll: sftp | scp | rsync | ftp | ftps | rclone
    #  - sftp/scp/rsync: SSH-basiert, Key-Auth empfohlen (BACKUP_REMOTE_KEY), sicher.
    #  - ftps: FTP ueber TLS (verschluesselt). ftp: UNVERSCHLUESSELT (nur im LAN nutzen!).
    #  - rclone: Catch-all fuer S3/WebDAV/Backblaze/Google Drive u.v.m. (rclone-Remote).
    BACKUP_REMOTE_PROTOCOL: str = "sftp"
    BACKUP_REMOTE_HOST: str = ""
    BACKUP_REMOTE_PORT: int = 0              # 0 = Protokoll-Standard (22/21)
    BACKUP_REMOTE_USER: str = ""
    BACKUP_REMOTE_PASSWORD: str = ""         # nur FTP/FTPS (SSH nutzt Key)
    BACKUP_REMOTE_KEY: str = ""              # Pfad zum privaten SSH-Key (sftp/scp/rsync)
    BACKUP_REMOTE_PATH: str = ""             # Zielverzeichnis auf der Gegenstelle
    # SSH-Hostkey-Pruefung: accept-new (TOFU, Standard) | yes (strikt, known_hosts noetig) | no
    BACKUP_REMOTE_SSH_STRICT: str = "accept-new"
    # rclone: Remote-Name inkl. Doppelpunkt, z. B. "offsite:" oder "s3:bucket".
    BACKUP_REMOTE_RCLONE_REMOTE: str = ""
    # Binaries (ggf. absoluter Pfad).
    BACKUP_REMOTE_SCP_BIN: str = "scp"
    BACKUP_REMOTE_SFTP_BIN: str = "sftp"
    BACKUP_REMOTE_RSYNC_BIN: str = "rsync"
    BACKUP_REMOTE_RCLONE_BIN: str = "rclone"
    # Nur die neuesten Dumps je Typ hochladen (True) statt aller im BACKUP_DIR (False).
    BACKUP_REMOTE_ONLY_LATEST: bool = True

    # Self-Service-Backup je Organisation (app/services/org_export_service.py +
    # org_backup_loop.py). Globaler Kill-Switch; je Org zusaetzlich in org_backup_config.
    ORG_BACKUP_ENABLED: bool = True
    # Groessenlimit je Org-Export-Archiv (Schutz vor Ueberlast; 0 = unbegrenzt).
    ORG_BACKUP_MAX_BYTES: int = 2 * 1024 * 1024 * 1024   # 2 GB
    # Loop-Intervall (Sekunden), in dem faellige Org-Backups geprueft werden.
    ORG_BACKUP_LOOP_INTERVAL_S: int = 900

    # Fernet-Verschlüsselung (Client Secrets, KI-API-Keys)
    # Eigener Key für Datenverschlüsselung; unabhängig von SECRET_KEY rotierbar.
    # Generieren: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Leer → Fallback auf SHA256("fernet-v1:" + SECRET_KEY) [abwärtskompatibel].
    FERNET_KEY: str = ""

    # LIS/IPR-Anbindung (Intergraph Leitstelleninformationssystem)
    LIS_ENABLED: bool = True         # globaler Kill-Switch
    LIS_POLL_INTERVAL_S: int = 30    # Loop-Intervall

    # DIBOS EventHub / Elvis-Anbindung (Landeswarnzentrale Vorarlberg) - reines
    # Tracing/Diagnose-Feature, siehe app/services/dibos/
    DIBOS_TRACE_ENABLED: bool = True   # globaler Kill-Switch für den Auto-Erkennungs-Loop
    DIBOS_POLL_INTERVAL_S: int = 20    # Loop-Intervall (leichter Poll auf GetCurrentEvents)

    # SSO / Microsoft Entra ID
    SSO_ENABLED: bool = True
    MS_LOGIN_BASE_URL: str = "https://login.microsoftonline.com"
    SSO_HTTP_TIMEOUT: int = 10
    SSO_FLOW_MAX_AGE: int = 600   # 10 min für state/nonce/PKCE-Cookie
    SSO_JWKS_CACHE_TTL: int = 3600
    SSO_SCOPES: str = "openid profile email User.Read"

    # Mail-Versand je Org: Office 365 / Microsoft Graph (App-only, Client-Credentials),
    # mit SMTP (org-eigen oder global) als automatischem Fallback (mail_service.deliver()).
    O365_MAIL_ENABLED: bool = True    # globaler Kill-Switch, analog LIS_ENABLED/SSO_ENABLED
    O365_MAIL_HTTP_TIMEOUT: int = 15
    O365_MAIL_TOKEN_MARGIN_S: int = 60  # Sicherheitsmarge vor Token-Ablauf im Cache

    @property
    def effective_public_base_url(self) -> str:
        return self.PUBLIC_BASE_URL or self.APP_BASE_URL

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.LAGEKARTE_CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()


def validate_startup_secrets() -> list[str]:
    """Gibt eine Liste fataler Konfigurationsfehler zurück.
    Aufgerufen aus app.main beim Start; in Nicht-Debug-Umgebung wird hart abgebrochen.
    """
    errors: list[str] = []
    if not settings.SECRET_KEY or settings.SECRET_KEY == SECRET_KEY_PLACEHOLDER:
        errors.append("SECRET_KEY ist nicht gesetzt oder enthält Default-Platzhalter")
    if len(settings.SECRET_KEY) < 32:
        errors.append("SECRET_KEY ist kürzer als 32 Zeichen")
    if not settings.COOKIE_SECURE:
        errors.append(
            "COOKIE_SECURE ist False – in Produktion (HTTPS) müssen Session-/CSRF-Cookies "
            "Secure sein. Setze COOKIE_SECURE=true in der .env (erfordert HTTPS via nginx)."
        )
    if not settings.FERNET_KEY:
        errors.append(
            "FERNET_KEY ist nicht gesetzt – der Datenverschlüsselungs-Key wird sonst aus "
            "SECRET_KEY abgeleitet (SHA256), wodurch eine SECRET_KEY-Rotation gespeicherte "
            "SSO-Client-Secrets/KI-API-Keys unentschlüsselbar macht. Generieren: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return errors
