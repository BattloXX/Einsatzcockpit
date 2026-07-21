from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base

BOS_VALUES = ["Feuerwehr", "Rotes Kreuz", "Polizei", "Bauhof", "Privat"]


class FireDept(Base):
    """Organisation / Feuerwehr. Dient gleichzeitig als vollständige multi-org Entität."""
    __tablename__ = "fire_dept"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#d42225")
    bos: Mapped[str] = mapped_column(String(20), nullable=False, default="Feuerwehr")
    withdraw_press_factor: Mapped[float] = mapped_column(default=0.5)
    withdraw_press_reserve: Mapped[int] = mapped_column(Integer, default=10)

    # Multi-org fields
    is_home_org: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Org-Lifecycle: NULL = aktiv, gesetzt = Soft-Delete (30-Tage-Frist bis Purge)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # IANA timezone (e.g. "Europe/Vienna"). NULL faellt auf settings.DEFAULT_TIMEZONE zurueck.
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Fallback-Position für den Karten-Picker (wird genutzt, wenn Geocoding fehlschlägt)
    fallback_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    fallback_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Kurz-Kürzel (max. 3 Zeichen, z.B. "WOL") für Fahrzeug-Darstellung: "RLF WOL"
    short_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    # Storage-Quota in Bytes (NULL = unbegrenzt)
    storage_quota_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    vehicles: Mapped[list[VehicleMaster]] = relationship(back_populates="dept")
    members: Mapped[list[Member]] = relationship(back_populates="org", foreign_keys="Member.org_id")
    settings: Mapped[OrgSettings | None] = relationship(back_populates="org", uselist=False)

    @property
    def display_name(self) -> str:
        return self.name


class VehicleMaster(Base):
    __tablename__ = "vehicle_master"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dept_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    type: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_first_train: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    bos_override: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_adhoc: Mapped[bool] = mapped_column(Boolean, default=False)
    is_external: Mapped[bool] = mapped_column(Boolean, default=False)
    adhoc_org_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    adhoc_org_short: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # Fahrtenbuch: Stammdaten / Plausibilität
    kennzeichen: Mapped[str | None] = mapped_column(String(20), nullable=True)
    km_aktuell: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    betriebsstunden_aktuell: Mapped[Decimal] = mapped_column(Numeric(10, 1), default=0, nullable=False)
    seilwinde_bh_aktuell: Mapped[Decimal] = mapped_column(Numeric(10, 1), default=0, nullable=False)

    # Generische Erfassungs-Flags
    erfasst_km: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    erfasst_betriebsstunden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    zweiter_maschinist_pflicht: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    seilwinde_abfrage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Optionale Einsatzleiter-Abfrage im Fahrtenbuch (Feld erscheint, Eingabe bleibt optional)
    einsatzleiter_abfrage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Plausibilitäts-Schwellen
    warn_schwelle_km: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    warn_schwelle_bh: Mapped[Decimal] = mapped_column(Numeric(6, 1), default=10, nullable=False)

    # Token / QR für vorausgefülltes Formular
    qr_token: Mapped[str | None] = mapped_column(String(40), unique=True, index=True, nullable=True)

    # Schaden-Empfänger Override (eine oder mehrere Adressen, komma-separiert)
    schaden_mail_override: Mapped[str | None] = mapped_column(String(500), nullable=True)
    schaden_teams_webhook_override: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # LIS/IPR: stabile ReferenceId des Fahrzeugs im Leitstellensystem, manuell gepflegt
    # (siehe LIS_IPR_Schnittstellen_Dokumentation.md Abschnitt 7.3)
    lis_reference_id: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    # True, wenn dieser Platzhalter automatisch von lis_sync.py::_get_or_create_external_vehicle()
    # für eine fremde Organisation angelegt wurde (siehe OrgLisConfig.sync_external_units) —
    # grenzt ihn von manuell in der Admin-UI gepflegten externen Fahrzeugen ab (dort read-only,
    # da er beim nächsten Poll ohnehin wiederverwendet/neu angelegt würde).
    lis_auto_created: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Lageführung: taktisches Zeichen (id aus app/static/tz/tz-manifest.json), für die
    # automatische Fahrzeug-Darstellung auf der Lagekarte. NULL = einfacher Statuskreis.
    taktisches_zeichen: Mapped[str | None] = mapped_column(String(64), nullable=True)

    dept: Mapped[FireDept] = relationship(back_populates="vehicles")

    @property
    def effective_bos(self) -> str:
        return self.bos_override or (self.dept.bos if self.dept else "Feuerwehr")

    @property
    def org_display_name(self) -> str:
        """Zeigt den Namen der (ggf. externen) Organisation."""
        if (self.is_external or self.is_adhoc) and self.adhoc_org_name:
            return self.adhoc_org_name
        return self.dept.name if self.dept else ""

    @property
    def display_label(self) -> str:
        """Fahrzeug-Code + Organisations-Kürzel, z.B. 'RLF WOL'. Ohne Kürzel nur 'RLF'."""
        if self.is_adhoc or self.is_external:
            kuerzel = self.adhoc_org_short or None
            return f"{self.code} - {kuerzel}" if kuerzel else self.code
        kuerzel = self.dept.short_code if self.dept and self.dept.short_code else None
        return f"{self.code} - {kuerzel}" if kuerzel else self.code


class Qualification(Base):
    __tablename__ = "qualification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    is_einsatzleiter: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_gruppenkommandant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Member(TenantScoped, Base):
    __tablename__ = "member"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id kommt via TenantScoped-Mixin
    lastname: Mapped[str] = mapped_column(String(100), nullable=False)
    firstname: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    ist_gruppenkommandant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # syBOS-Personen-ID (Mitglieder-Excel-Import, Spalte "syBOS-ID") — stabiler
    # Schlüssel, um DIBOS-EventHub-Personenrückmeldungen (personResponseList[].idSybos)
    # einem echten Mitglied zuzuordnen (siehe app/services/dibos/dibos_enrich.py).
    # Nur für sybos-angebundene Dienststellen befüllt, daher nullable.
    sybos_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    org: Mapped[FireDept | None] = relationship(back_populates="members", foreign_keys="Member.org_id")
    qualifications: Mapped[list[MemberQualification]] = relationship(
        back_populates="member", lazy="joined", passive_deletes=True,
    )

    @property
    def full_name(self) -> str:
        return f"{self.firstname} {self.lastname}"

    @property
    def is_agt(self) -> bool:
        return any(mq.qualification.code == "AGT" for mq in self.qualifications if mq.qualification)


class MemberQualification(Base):
    __tablename__ = "member_qualification"

    member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("member.id", ondelete="CASCADE"), primary_key=True)
    qualification_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("qualification.id", ondelete="CASCADE"), primary_key=True
    )
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    member: Mapped[Member] = relationship(back_populates="qualifications")
    qualification: Mapped[Qualification] = relationship(lazy="joined")


class AlarmType(TenantScoped, Base):
    __tablename__ = "alarm_type"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_alarm_type_org_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="T")
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    default_first_train_only: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_neighbors: Mapped[bool] = mapped_column(Boolean, default=False)
    triggers_major_incident: Mapped[bool] = mapped_column(Boolean, default=False)
    # Stichwort-spezifische SMS-Vorlage; überschreibt den Org-Standard wenn gesetzt
    einsatzinfo_sms_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Löst dieses Stichwort eine Teams-Alarmkarte aus? (siehe teams_alarm_service.post_incident_card())
    teams_alarm_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TaskSuggestion(TenantScoped, Base):
    __tablename__ = "task_suggestion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    text: Mapped[str] = mapped_column(String(500), nullable=False)

    alarm_assignments: Mapped[list[TaskSuggestionAlarm]] = relationship(
        back_populates="suggestion", cascade="all, delete-orphan"
    )


class TaskSuggestionAlarm(Base):
    __tablename__ = "task_suggestion_alarm"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_suggestion_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_suggestion.id", ondelete="CASCADE"), nullable=False
    )
    alarm_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alarm_type.id", ondelete="CASCADE"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    suggestion: Mapped[TaskSuggestion] = relationship(back_populates="alarm_assignments")
    alarm_type: Mapped[AlarmType] = relationship(lazy="joined")


class MessageSuggestion(TenantScoped, Base):
    __tablename__ = "message_suggestion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    text: Mapped[str] = mapped_column(String(500), nullable=False)

    alarm_assignments: Mapped[list[MessageSuggestionAlarm]] = relationship(
        back_populates="suggestion", cascade="all, delete-orphan"
    )


class MessageSuggestionAlarm(Base):
    __tablename__ = "message_suggestion_alarm"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_suggestion_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("message_suggestion.id", ondelete="CASCADE"), nullable=False
    )
    alarm_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alarm_type.id", ondelete="CASCADE"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    suggestion: Mapped[MessageSuggestion] = relationship(back_populates="alarm_assignments")
    alarm_type: Mapped[AlarmType] = relationship(lazy="joined")


class LageHint(TenantScoped, Base):
    __tablename__ = "lage_hint"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    alarm_assignments: Mapped[list[LageHintAlarm]] = relationship(
        back_populates="hint", cascade="all, delete-orphan"
    )


class LageHintAlarm(Base):
    __tablename__ = "lage_hint_alarm"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lage_hint_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lage_hint.id", ondelete="CASCADE"), nullable=False
    )
    alarm_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alarm_type.id", ondelete="CASCADE"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    hint: Mapped[LageHint] = relationship(back_populates="alarm_assignments")
    alarm_type: Mapped[AlarmType] = relationship(lazy="joined")


class DefaultMessage(TenantScoped, Base):
    __tablename__ = "default_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    text: Mapped[str] = mapped_column(String(500), nullable=False)

    alarm_assignments: Mapped[list[DefaultMessageAlarm]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class DefaultMessageAlarm(Base):
    __tablename__ = "default_message_alarm"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    default_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("default_message.id", ondelete="CASCADE"), nullable=False
    )
    alarm_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alarm_type.id", ondelete="CASCADE"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    due_after_sec: Mapped[int] = mapped_column(Integer, default=300)

    message: Mapped[DefaultMessage] = relationship(back_populates="alarm_assignments")
    alarm_type: Mapped[AlarmType] = relationship(lazy="joined")


class OrgSettings(Base):
    """Organisations-spezifische Einstellungen (Logo, Farbe, KI-Modus, Quota etc.)."""
    __tablename__ = "org_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    primary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    footer_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mi_auto_adopt: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    # KI-Konfiguration je Org
    # 'central' = Plattform-Key aus Server-Env; 'byok' = org-eigener Anthropic-Key
    ai_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="central")
    ai_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-verschlüsselt
    ai_monthly_token_quota: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ai_tokens_used_month: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    ai_tokens_month_key: Mapped[str | None] = mapped_column(String(7), nullable=True)  # YYYY-MM

    # Auto-Schließen je Org (NULL = globale SystemSettings-Fallback nutzen)
    autoclose_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    autoclose_after_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    autoclose_grace_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_access_pin_hash: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # GSL: konfigurierbare Pflicht-Rollen (JSON-Liste von Role-Codes, NULL = Default EL…S6)
    gsl_required_staff_roles: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fahrzeugposition gilt als "veraltet" nach N Minuten ohne Update
    vehicle_stale_minutes: Mapped[int] = mapped_column(Integer, default=5)
    # GPS-Positionshistorie Aufbewahrungsdauer
    position_retention_days: Mapped[int] = mapped_column(Integer, default=30)
    # Wetter-Integration: NULL = globale Einstellung nutzen, True/False = org-spezifisch
    weather_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Wetter-Dashboard-Tokens: siehe eigene, beschriftete Tabelle WeatherDashboardToken
    # (mehrere Tokens je Org moeglich, z.B. Infoscreen + Website-Widget unabhaengig widerrufbar)
    # Darstellungszeitraum für historische Werte im Infoscreen (in Stunden), Default 24
    infoscreen_history_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)

    # GSL-Lagemeldungs-Regelkreis (SKKM): Intervall der Lagemeldungs-Pflicht je Einsatz.
    # NULL beim Default-Intervall ⇒ gesamte Logik deaktiviert (kein Timer/Auftrag/Chip).
    gsl_lagemeldung_interval_minutes:        Mapped[int | None] = mapped_column(Integer, nullable=True, default=60)
    # Eigenes (kürzeres) Intervall bei Priorität "Sofort"; NULL ⇒ Default-Intervall verwenden.
    gsl_lagemeldung_interval_sofort_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True, default=30)
    # Automatischen Funkjournal-Auftrag bei Überfälligkeit erstellen
    gsl_lagemeldung_auto_auftrag:            Mapped[bool]       = mapped_column(Boolean, default=True)

    # UAS-Modul: je Org aktivierbar, aber nur wenn System-Flag (SystemSettings key
    # "uas_module_enabled") ebenfalls "true" ist → effektiv = System AND Org.
    uas_module_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Objektverwaltung: je Org aktivierbar, effektiv = SystemSettings-Key
    # "objekt_module_enabled" == "true" AND dieser Wert (Muster UAS).
    objekt_module_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Förderstrecken-Planer: je Org aktivierbar, effektiv = SystemSettings-Key
    # "foerderstrecke_module_enabled" == "true" AND dieser Wert (Muster UAS).
    foerderstrecke_module_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)
    # Nachschlagewerke (Gefahrgut-Suche, Rettungsdatenblaetter, Karten-Overlays):
    # effektiv = SystemSettings-Key "nachschlagewerke_module_enabled" == "true"
    # AND dieser Wert (Muster UAS/Objekt/Gateway).
    nachschlagewerke_module_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)
    # Geo-Fallback des Alarm-Matchings: max. Distanz Einsatz↔Objekt in Metern
    objekt_geo_match_radius_m: Mapped[int] = mapped_column(Integer, nullable=False, default=75)
    # Alarm-Infoscreen (Wandmonitor): Ruhezustand + Dauer der Alarmansicht
    # idle_modus: uhr / wetter / einsatzliste
    alarm_infoscreen_idle_modus: Mapped[str] = mapped_column(String(20), nullable=False, default="uhr")
    alarm_infoscreen_alarm_dauer_min: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    # Idle-Modus "wetter": URL des bestehenden Wetter-Infoscreens (Token nur als
    # Hash gespeichert → URL kann nicht abgeleitet werden, daher manuell hinterlegen)
    alarm_infoscreen_wetter_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Großschadenslage-Sonderansicht am Infoscreen (bleibt, solange die GSL aktiv ist)
    alarm_infoscreen_gsl_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # KI-Dokumentklassifizierung (Objektverwaltung): Opt-in je Org, zusaetzlich zum AI-Setup
    objekt_ki_klassifikation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Hydranten-/Löschwasser-Layer (OSM/OSMHydrant) auf Einsatz- und Objekt-Lagekarten.
    # Effektiv = Settings.HYDRANT_ENABLED AND dieser Wert.
    hydrant_layer_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Print & Alarm Gateway (ECPG): je Org aktivierbar, effektiv = SystemSettings-Key
    # "gateway_module_enabled" == "true" AND dieser Wert (Muster UAS/Objekt).
    gateway_module_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Gateway gilt als offline nach N Minuten ohne Heartbeat → Admin-Benachrichtigung.
    gateway_offline_alert_min: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    # Verleihscheine bei Anlage automatisch am Stationsdrucker drucken (nur wirksam,
    # wenn Gateway-Modul aktiv + ein aktiver Drucker verbunden ist).
    verleih_autodruck: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # GSL-Feature-Flags je Org (effektiv = SystemSettings-Globalschalter AND dieser Wert).
    # Default True = Modul aktiv sofern global nicht deaktiviert.
    mi_feature_stab:           Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mi_feature_funkjournal:    Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mi_feature_meldungen:      Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mi_feature_sektoren:       Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mi_feature_karte:          Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mi_feature_zeitreise:      Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mi_feature_ressourcen:     Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mi_feature_uebergreifend:  Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mi_feature_geraeteverleih: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Geräteverleih-Konfiguration je Org
    gsl_verleih_erinnerung_stunden:  Mapped[int | None] = mapped_column(Integer, nullable=True)
    gsl_verleih_sms_ausleih_text:    Mapped[str | None] = mapped_column(Text, nullable=True)
    gsl_verleih_sms_erinnerung_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Großschadenslage-Alarm: einmaliger SMS+Teams-Sonderhinweis bei Ausrufung einer neuen
    # Lage — unabhängig von der stichwortbezogenen Einsatzinfo (siehe gsl_notify.py)
    gsl_alarm_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    gsl_alarm_text:    Mapped[str | None] = mapped_column(Text, nullable=True)

    # Pegel-/Abflusskonfiguration: JSON-Array [{hzbnr, name, beschreibung}]
    abfluss_stationen: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Kachelmann Plus-API: Key je Org (kostenpflichtiges Abo); NULL = ZAMG/GeoSphere
    kachelmann_api_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Fahrtenbuch-Konfiguration je Org
    fahrtenbuch_modul_aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fahrtenbuch_token: Mapped[str | None] = mapped_column(String(40), unique=True, index=True, nullable=True)
    schaden_mail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    schaden_teams_webhook_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fahrt_doppel_minuten: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

    # Wetterwarnung-Kanäle: Org-weite Empfänger (Regel-Override hat Vorrang)
    weather_alert_mail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    weather_alert_teams_webhook_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Bodensee-Seewassertemperatur manuell (für Lake-Effekt-Regel)
    bodensee_temp_override_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    bodensee_temp_override_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # SMS-Einsatzinfo: automatischer Versand bei Alarmeingang
    # Aktivierungsschalter
    einsatzinfo_sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Vorlage mit Platzhaltern {stichwort} {adresse} {ort} {meldung} {einsatzgrund} {datum} {zeit}
    # NULL → systemweiter Default wird genutzt
    einsatzinfo_sms_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True = auch bei Übungen senden (mit [ÜBUNG]-Präfix)
    einsatzinfo_sms_send_exercise: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # SMS-Empfang: Org-Schalter (App fordert RECEIVE_SMS nur an, wenn aktiv)
    sms_receive_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Org-Default-Webhook fuer Weiterleitungsregeln ohne eigenen teams_webhook_url
    sms_receive_teams_webhook_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Atemschutzgeräteprüfung-Konfiguration je Org
    atemschutz_pruefung_modul_aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    atemschutz_pruef_token: Mapped[str | None] = mapped_column(String(40), unique=True, index=True, nullable=True)
    atemschutz_wart_mail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    atemschutz_wart_teams_webhook_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Lageführung (einsatzbezogene Lagekarte): je Org aktivierbar, effektiv = SystemSettings-Key
    # "lagefuehrung_modul_aktiv" == "true" AND dieser Wert (Muster UAS/Objekt).
    lagefuehrung_modul_aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    org: Mapped[FireDept] = relationship(back_populates="settings")

    @property
    def abfluss_stationen_list(self) -> list[dict]:
        import json as _json
        if not self.abfluss_stationen:
            return []
        try:
            return _json.loads(self.abfluss_stationen)
        except (ValueError, TypeError):
            return []


class OrgStorageUsage(Base):
    """Laufende Speicher-Verbrauchszeile pro Organisation."""
    __tablename__ = "org_storage_usage"

    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), primary_key=True
    )
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class SystemSettings(Base):
    """Systemweite Einstellungen als Key-Value-Store."""
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)


class AIPromptVersion(TenantScoped, Base):
    """Versionsverlauf der bearbeitbaren KI-Prompt-Teile (max. 10 je Prompt-Typ)."""
    __tablename__ = "ai_prompt_versions"
    __table_args__ = (UniqueConstraint("org_id", "prompt_key", "version", name="uq_ai_prompt_org_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_key: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    variable_part: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_by_username: Mapped[str | None] = mapped_column(String(100), nullable=True)


class AlarmDispatchVehicle(Base):
    """Ausrückordnung: welche Fahrzeuge bei welchem Alarmtyp ausrücken (inkl. Reihenfolge)."""
    __tablename__ = "alarm_dispatch_vehicle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alarm_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alarm_type.id", ondelete="CASCADE"), nullable=False
    )
    vehicle_master_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vehicle_master.id", ondelete="CASCADE"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    alarm_type: Mapped[AlarmType] = relationship(lazy="joined")
    vehicle: Mapped[VehicleMaster] = relationship()


class SeedTemplate(Base):
    """System-Vorlagen je Profil – system_admin pflegt; beim Org-Anlegen werden sie kopiert."""
    __tablename__ = "seed_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    profile_label: Mapped[str] = mapped_column(String(100), nullable=False)
    # Werte: alarm_type | task_suggestion | message_suggestion | lage_hint | default_message
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON object
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC),
                                                  onupdate=lambda: datetime.now(UTC))
