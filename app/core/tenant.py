"""Tenant-Infrastruktur: TenantScoped-Mixin + do_orm_execute-Listener.

Defense-in-Depth-Schicht 3: SQLAlchemy-Session-Event injiziert automatisch
WHERE org_id = :current_org auf allen als TenantScoped markierten Modellen
sowie auf Sondermodellen (Incident, VehicleMaster, User, AuditLog).

Fail-Closed-Design (PR 1 Sichtbarkeit-Konzept):
- Fehlt der Org-Kontext im Session-Info-Dict UND die Query berührt ein
  tenant-pflichtiges Modell → TenantContextMissing wird geworfen (HTTP 500).
- Eine vergessene Dependency fällt damit sofort im Test auf, statt still
  Daten zu leaken.
- Bypass: execution_option(include_all_tenants=True) – nur für system_admin-Code.

Kontextwerte:
  _MISSING (Sentinel)  → nie gesetzt → Fail-Closed
  None                 → system_admin-Modus (kein Filter, alle Orgs sichtbar)
  int                  → org_id-Filter für diesen Wert
"""
from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, event, or_
from sqlalchemy.orm import Mapped, Session, declared_attr, mapped_column, with_loader_criteria

# Sentinel: unterscheidet "nie gesetzt" von "explizit None (system_admin)"
_MISSING = object()


class TenantContextMissing(Exception):
    """Raised when a tenant-scoped model is queried without a tenant context.

    Indicates a missing CurrentOrgId dependency or missing set_tenant_context()
    call in system-level code. Fix: add Depends(current_org) to the endpoint,
    or call set_tenant_context(db, None) in background/CLI code.
    """


class TenantScoped:
    """Mixin: markiert Modelle für automatisches Org-Filtering per Session.

    Subklassen erhalten eine org_id-Spalte via declared_attr.
    Während der Transition (vor Backfill) ist org_id nullable.
    """

    @declared_attr
    def org_id(cls) -> Mapped[int | None]:
        # BigInteger (BIGINT) muss mit fire_dept.id (BIGINT) uebereinstimmen —
        # MySQL erzwingt exakte Typgleichheit bei FK-Constraints (errno: 150).
        return mapped_column(
            BigInteger, ForeignKey("fire_dept.id", ondelete="SET NULL"),
            nullable=True, index=True,
        )


# Tabellen, die der Listener aktiv scoped. Queries auf diese Tabellen ohne
# gesetzten Kontext lösen TenantContextMissing aus (Fail-Closed).
_TENANT_TABLE_NAMES: frozenset[str] = frozenset({
    # TenantScoped-Modelle (org_id via Mixin)
    "member",
    "alarm_type",
    "task_suggestion",
    "message_suggestion",
    "lage_hint",
    "default_message",
    "ai_prompt_versions",
    # UAS-Modul (TenantScoped via Mixin)
    "uas_device",
    "uas_pilot",
    "uas_flugbewegung",
    "uas_wartung",
    "uas_einsatz",
    "uas_einsatz_rolle",
    "uas_flug",
    "uas_checkliste",
    "uas_ereignis",
    "uas_kartenobjekt",
    "uas_medien",
    # Sondermodelle mit abweichendem Spaltenname
    "incident",        # primary_org_id
    "vehicle_master",  # dept_id
    # Sondermodelle mit nullable org_id
    "user",
    "audit_log",
    # Direkt org-gebunden
    "api_key",
    "sms_gateway_token",
    # Geräteverleih-Modul (TenantScoped via Mixin)
    "verleih_artikel",
    "verleih_stueckliste",
    "verleih_ausleihe",
    # Lokale Wetterstation (TenantScoped via Mixin) — Org-Isolation Fail-Closed
    "weather_station",
    # Wetterwarnungen (TenantScoped via Mixin) — Org-Isolation Fail-Closed
    "weather_alert_rule",
    "weather_alert_state",
    "weather_alert_log",
    # Fahrtenbuch-Modul (TenantScoped via Mixin)
    "fahrtzweck",
    "zielort",
    # Atemschutzgeräteprüfung-Modul (TenantScoped via Mixin)
    "atemschutz_geraet",
    # SMS-Erweiterungen (TenantScoped via Mixin)
    "sms_group",
    "sms_einsatzinfo_recipient",
    "sms_log",
    "sms_forward_rule",
    "sms_inbox",
    # Teilnehmerlisten-Modul (TenantScoped via Mixin)
    "termin",
    "funktion",
    "teilnahme",
    # Objektverwaltung (TenantScoped via Mixin)
    "objekt",
    "objekt_kategorie",
    "objekt_auswahl",
    "objekt_symbol",
    "objekt_zusatzadresse",
    "objekt_bma",
    "objekt_change",
    "gefahren_katalog",
    "objekt_gefahr",
    "merkmal_katalog",
    "objekt_merkmal",
    "objekt_kontakt",
    "objekt_wohnanlage",
    "objekt_dokument",
    "objekt_dokument_seite",
    "objekt_karten_objekt",
    "objekt_einsatz",
    "alarm_infoscreen_token",
    "infoscreen_url",
    "objekt_seite_ki_vorschlag",
    # Wasserstellen-/Löschwasser-Stammdaten (TenantScoped via Mixin)
    "wasserstelle",
    # Print & Alarm Gateway (ECPG) – TenantScoped via Mixin
    "gateway",
    "printer",
    "print_job",
    "alarm_ingest",
    "print_rule",
    # GSL/Großschadenslage (SEC-1 Defense-in-Depth-Backstop) — bisher nur durch
    # ~95 manuelle _check_org_access()-Aufrufe in ui_major_incident.py geschützt;
    # ein vergessener Aufruf wäre ein Cross-Tenant-IDOR ohne DB-seitigen Schutz.
    "major_incident",
    "incident_site",
    # Lageführung-Modul (TenantScoped via Mixin)
    "lagefuehrung_feature",
    "lagefuehrung_event",
})


def _touches_tenant_models(stmt) -> bool:
    """Return True if the statement references any tenant-scoped table."""
    from sqlalchemy import Table
    from sqlalchemy.sql.visitors import iterate as _sa_iterate
    try:
        for clause in _sa_iterate(stmt):
            if isinstance(clause, Table) and clause.name in _TENANT_TABLE_NAMES:
                return True
    except Exception:
        pass
    return False


def set_tenant_context(db: Session, org_id: int | None) -> None:
    """Setzt den aktuellen Tenant im Session-Info-Dict."""
    db.info["current_org_id"] = org_id


def _add_tenant_filter(execute_state) -> None:
    if not execute_state.is_select:
        return
    if execute_state.execution_options.get("include_all_tenants"):
        return

    org_id = execute_state.session.info.get("current_org_id", _MISSING)

    if org_id is _MISSING:
        # Fail-Closed: Kontext wurde nie gesetzt → Exception statt Datenleak
        if _touches_tenant_models(execute_state.statement):
            raise TenantContextMissing(
                "Tenant-pflichtiges Modell ohne gesetzten Org-Kontext abgefragt. "
                "Fehlende CurrentOrgId-Dependency oder fehlendes "
                "set_tenant_context(db, None) in System-Code. "
                "Zum Bypass: execution_options(include_all_tenants=True)"
            )
        return

    if org_id is None:
        # system_admin-Modus: kein Filter (alle Orgs sichtbar)
        return

    # Regulärer Benutzer: Filter auf aktive Org
    from sqlalchemy import select as sa_select

    from app.models.incident import Incident, IncidentOrg
    from app.models.major_incident import IncidentSite, MajorIncident
    from app.models.master import VehicleMaster
    from app.models.user import AuditLog, User

    _org_id = org_id  # lokale Kopie für Closure

    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda c: c.org_id == _org_id,
            include_aliases=True,
        ),
        with_loader_criteria(
            Incident,
            lambda c: or_(
                c.primary_org_id == _org_id,
                c.id.in_(
                    sa_select(IncidentOrg.incident_id).where(
                        IncidentOrg.org_id == _org_id
                    )
                ),
            ),
            include_aliases=True,
        ),
        with_loader_criteria(
            VehicleMaster,
            lambda c: c.dept_id == _org_id,
            include_aliases=True,
        ),
        with_loader_criteria(
            User,
            lambda c: c.org_id == _org_id,
            include_aliases=True,
        ),
        with_loader_criteria(
            AuditLog,
            lambda c: c.org_id == _org_id,
            include_aliases=True,
        ),
        # GSL/Großschadenslage (SEC-1): kein Kollaborations-Modell wie bei Incident/
        # IncidentOrg — org_id ist ein einfacher, nicht-nullbarer FK, daher genügt
        # ein direkter Vergleich (kein or_(...)-Subquery nötig).
        with_loader_criteria(
            MajorIncident,
            lambda c: c.org_id == _org_id,
            include_aliases=True,
        ),
        with_loader_criteria(
            IncidentSite,
            lambda c: c.org_id == _org_id,
            include_aliases=True,
        ),
    )


def register_tenant_listener() -> None:
    """Registriert den Tenant-Filter-Listener global auf der Session-Basisklasse.

    Muss einmalig beim App-Start aufgerufen werden. Der Listener greift für
    alle Session-Instanzen, die current_org_id im info-Dict haben.
    """
    from sqlalchemy.orm import Session as _Session
    event.listen(_Session, "do_orm_execute", _add_tenant_filter)
