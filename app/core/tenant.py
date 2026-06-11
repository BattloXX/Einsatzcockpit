"""Tenant-Infrastruktur: TenantScoped-Mixin + do_orm_execute-Listener.

Defense-in-Depth-Schicht 3: SQLAlchemy-Session-Event injiziert automatisch
WHERE org_id = :current_org auf allen als TenantScoped markierten Modellen.
Bypass nur über execution_option(include_all_tenants=True) – ausschließlich
für system_admin-Code vorgesehen.

Übergangsstrategie: org_id ist in der Mixin-Basis nullable=True, damit bestehende
Datensätze ohne org_id nicht brechen. Nach dem Backfill-Migration in PR 3 werden
die NOT-NULL-Constraints gesetzt.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, event
from sqlalchemy.orm import Mapped, Session, declared_attr, mapped_column, with_loader_criteria


class TenantScoped:
    """Mixin: markiert Modelle für automatisches Org-Filtering per Session.

    Subklassen erhalten eine org_id-Spalte via declared_attr.
    Während der Transition (vor Backfill) ist org_id nullable.
    """

    @declared_attr
    def org_id(cls) -> Mapped[int | None]:
        return mapped_column(
            Integer, ForeignKey("fire_dept.id", ondelete="SET NULL"),
            nullable=True, index=True,
        )


def set_tenant_context(db: Session, org_id: int | None) -> None:
    """Setzt den aktuellen Tenant im Session-Info-Dict."""
    db.info["current_org_id"] = org_id


def _add_tenant_filter(execute_state) -> None:
    if not execute_state.is_select:
        return
    if execute_state.execution_options.get("include_all_tenants"):
        return
    org_id = execute_state.session.info.get("current_org_id")
    if org_id is None:
        return
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.org_id == org_id,
            include_aliases=True,
        )
    )


def register_tenant_listener() -> None:
    """Registriert den Tenant-Filter-Listener global auf der Session-Basisklasse.

    Muss einmalig beim App-Start aufgerufen werden. Der Listener greift für
    alle Session-Instanzen, die current_org_id im info-Dict haben.
    """
    from sqlalchemy.orm import Session as _Session
    event.listen(_Session, "do_orm_execute", _add_tenant_filter)
