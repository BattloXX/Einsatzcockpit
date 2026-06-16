from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    adhoc_org_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    adhoc_org_short: Mapped[str | None] = mapped_column(String(3), nullable=True)

    dept: Mapped[FireDept] = relationship(back_populates="vehicles")

    @property
    def effective_bos(self) -> str:
        return self.bos_override or (self.dept.bos if self.dept else "Feuerwehr")

    @property
    def display_label(self) -> str:
        """Fahrzeug-Code + Organisations-Kürzel, z.B. 'RLF WOL'. Ohne Kürzel nur 'RLF'."""
        if self.is_adhoc:
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

    # GSL-Lagemeldungs-Regelkreis (SKKM): Intervall der Lagemeldungs-Pflicht je Einsatz.
    # NULL beim Default-Intervall ⇒ gesamte Logik deaktiviert (kein Timer/Auftrag/Chip).
    gsl_lagemeldung_interval_minutes:        Mapped[int | None] = mapped_column(Integer, nullable=True, default=60)
    # Eigenes (kürzeres) Intervall bei Priorität "Sofort"; NULL ⇒ Default-Intervall verwenden.
    gsl_lagemeldung_interval_sofort_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True, default=30)
    # Automatischen Funkjournal-Auftrag bei Überfälligkeit erstellen
    gsl_lagemeldung_auto_auftrag:            Mapped[bool]       = mapped_column(Boolean, default=True)

    org: Mapped[FireDept] = relationship(back_populates="settings")


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
    # type: alarm_type | task_suggestion | message_suggestion | lage_hint | default_message
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON object
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC),
                                                  onupdate=lambda: datetime.now(UTC))
