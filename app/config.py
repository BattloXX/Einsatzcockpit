from pydantic_settings import BaseSettings, SettingsConfigDict

SECRET_KEY_PLACEHOLDER = "change-me-in-production"
BOOTSTRAP_PASSWORD_PLACEHOLDER = "admin"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "mysql+pymysql://einsatzleiter:pw@127.0.0.1:3306/einsatzleiter"
    SECRET_KEY: str = SECRET_KEY_PLACEHOLDER
    SESSION_MAX_AGE_SECONDS: int = 86400    # 24 Stunden (normaler Benutzer)
    SESSION_INACTIVITY_SECONDS: int = 28800  # 8 Stunden Inaktivitäts-Timeout

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8092
    APP_BASE_URL: str = "http://localhost:8092"
    PUBLIC_BASE_URL: str = ""  # Für Mail-Links; leer = falls leer APP_BASE_URL verwenden
    APP_VERSION: str = "2.2.0"
    DEBUG: bool = False

    # Cookie-Flags
    COOKIE_SECURE: bool = False  # In Produktion auf true (HTTPS)

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
    NOMINATIM_USER_AGENT: str = "Einsatzleiter-Hilfswerkzeug/2.x (contact: office@feuerwehr-wolfurt.at)"
    NOMINATIM_TIMEOUT_SECONDS: float = 5.0

    # Photon Adress-Autocomplete (OSM/komoot – kein API-Key, über Backend geproxyt)
    PHOTON_BASE_URL: str = "https://photon.komoot.io"
    PHOTON_TIMEOUT_SECONDS: float = 4.0
    PHOTON_CACHE_TTL_SECONDS: int = 300
    PHOTON_SUGGEST_LIMIT: int = 8
    DEFAULT_INCIDENT_CITY: str = "Wolfurt"   # Fallback wenn Home-Org kein city hat

    # Wetter (GeoSphere Austria Data Hub — CC BY 4.0, kein API-Key)
    WEATHER_ENABLED: bool = True
    GEOSPHERE_BASE_URL: str = "https://dataset.api.hub.geosphere.at/v1"
    GEOSPHERE_WARN_URL: str = "https://openapi.hub.geosphere.at/warnapi/v1"
    WEATHER_NOWCAST_RESOURCE: str = "nowcast-v1-15min-1km"
    WEATHER_NWP_RESOURCE: str = "nwp-v1-1h-2500m"
    WEATHER_STATION_RESOURCE: str = "tawes-v1-10min"
    WEATHER_CACHE_TTL_NOWCAST: int = 300     # 5 min
    WEATHER_CACHE_TTL_NWP: int = 1800        # 30 min
    WEATHER_CACHE_TTL_WARN: int = 300        # 5 min
    WEATHER_HTTP_TIMEOUT: int = 8
    WEATHER_RADIUS_KM: int = 15
    WEATHER_FALLBACK_OPENMETEO: bool = True

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
    return errors
