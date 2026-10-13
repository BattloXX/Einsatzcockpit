"""Ereignisgesteuerte Phasenautomatik für GSL-Einsatzstellen."""
from __future__ import annotations

from app.models.major_incident import EINHEIT_STATUS_BEENDET, EinheitSiteDispatch, SitePhase
from app.services.major_incident_service import setze_site_phase
from app.services.resource_service import _journal


def _userdaten(user):
    if isinstance(user, tuple):
        return user
    return getattr(user, "id", None), getattr(user, "display_name", None) or getattr(user, "name", None)


def _zielphase(dispatches: list[EinheitSiteDispatch], aktuelle_phase: SitePhase | None = None) -> SitePhase | None:
    abarbeitungen = [d for d in dispatches if d.auftragsart == "abarbeitung"]
    aktive_a = [d for d in abarbeitungen if d.withdrawn_at is None and d.einheit_status not in EINHEIT_STATUS_BEENDET]
    if any(d.einheit_status in {"vor_ort", "in_arbeit"} for d in aktive_a):
        return SitePhase.in_arbeit
    if aktive_a:
        # Läuft die Bearbeitung bereits, bleibt die Stelle in Arbeit, bis alle Abarbeitungen fertig sind –
        # auch wenn die begonnene Einheit schon fertig ist und nur noch zugewiesene Aufträge offen sind.
        return SitePhase.in_arbeit if aktuelle_phase == SitePhase.in_arbeit else SitePhase.disponiert
    nicht_zurueckgezogene_a = [d for d in abarbeitungen if d.withdrawn_at is None]
    erkundungen = [d for d in dispatches if d.auftragsart == "erkundung"]
    aktive_e = [
        d for d in erkundungen
        if d.withdrawn_at is None and d.einheit_status not in EINHEIT_STATUS_BEENDET
    ]
    if nicht_zurueckgezogene_a:
        if all(d.einheit_status in EINHEIT_STATUS_BEENDET for d in nicht_zurueckgezogene_a):
            return SitePhase.fertig_gemeldet
    elif abarbeitungen and not aktive_e:
        return SitePhase.bewertet if erkundungen else None
    if aktive_e:
        return SitePhase.erkundung
    if erkundungen and all(
        d.withdrawn_at is not None or d.einheit_status in EINHEIT_STATUS_BEENDET for d in erkundungen
    ):
        return SitePhase.bewertet
    return None


def aktualisiere_site_phase(db, site, *, ausloeser: str, user=None, quelle: str) -> bool:
    """Berechnet die automatische Phase nach einer Dispatch-Mutation."""
    from app.services import lagemeldung_service

    if site.phase in {SitePhase.erledigt, SitePhase.abgebrochen}:
        lagemeldung_service.sync_timer(site, db)
        return False
    dispatches = db.query(EinheitSiteDispatch).filter(EinheitSiteDispatch.site_id == site.id).all()
    ziel = _zielphase(dispatches, site.phase)
    if ziel is None:
        lagemeldung_service.sync_timer(site, db)
        return False
    user_id, author_name = _userdaten(user)
    changed = setze_site_phase(
        db, site, ziel, user_id=user_id, author_name=author_name, ausloeser=ausloeser
    )
    # Auftragsbezogene Ereignisse (Erkundung zugewiesen, disponiert, begonnen, fertig) schreibt die
    # jeweilige Mutation selbst; hier nur die Ereignisse, die erst auf Ebene der Einsatzstelle entstehen.
    ereignis = {SitePhase.bewertet: "bewertet", SitePhase.fertig_gemeldet: "alle_fertig"}.get(ziel)
    if changed and ereignis:
        text = ("alle Erkundungen abgeschlossen – Bewertung durch die Leitstelle" if ereignis == "bewertet"
                else "alle Einheiten fertig gemeldet – Abschluss durch die Leitstelle")
        _journal(
            db, site.major_incident_id, f"Einsatzstelle {site.bezeichnung}: {text}",
            author_name=author_name, user_id=user_id, site_id=site.id,
            ereignis_typ=ereignis, quelle=quelle,
        )
    lagemeldung_service.sync_timer(site, db)
    return changed


def einsatz_abschliessen(db, site, *, user, quelle: str, aktive_zurueckziehen: bool) -> None:
    """Leitstelle schließt die Einsatzstelle endgültig ab (Phase erledigt = „Abgeschlossen“)."""
    from app.services.resource_service import withdraw_from_site

    aktive = [
        d for d in db.query(EinheitSiteDispatch).filter(EinheitSiteDispatch.site_id == site.id).all()
        if d.withdrawn_at is None and d.einheit_status not in EINHEIT_STATUS_BEENDET
    ]
    if aktive and not aktive_zurueckziehen:
        raise ValueError("Aktive Aufträge: " + ", ".join(d.einheit.label for d in aktive))
    user_id, author_name = _userdaten(user)
    for dispatch in aktive:
        withdraw_from_site(
            db, dispatch.einheit_id, site.major_incident_id, site.id, author_name=author_name,
            user_id=user_id, grund="Einsatz abgeschlossen", quelle=quelle, auftragsart=dispatch.auftragsart,
        )
    setze_site_phase(db, site, SitePhase.erledigt, user_id=user_id, author_name=author_name)
    _journal(db, site.major_incident_id, f"Einsatzstelle {site.bezeichnung} abgeschlossen", author_name=author_name,
             user_id=user_id, site_id=site.id, ereignis_typ="site_abgeschlossen", quelle=quelle)


def einsatz_wiedereroeffnen(db, site, *, user, quelle: str) -> None:
    """Bewusste Wiedereröffnung; die Phase folgt den vorhandenen Aufträgen, sonst „Bewertet“."""
    if site.phase not in {SitePhase.erledigt, SitePhase.abgebrochen}:
        raise ValueError("Einsatzstelle ist nicht abgeschlossen")
    user_id, author_name = _userdaten(user)
    dispatches = db.query(EinheitSiteDispatch).filter(EinheitSiteDispatch.site_id == site.id).all()
    ziel = _zielphase(dispatches) or SitePhase.bewertet
    setze_site_phase(db, site, ziel, user_id=user_id, author_name=author_name, ausloeser="wiedereröffnet")
    _journal(db, site.major_incident_id, f"Einsatzstelle {site.bezeichnung} wiedereröffnet", author_name=author_name,
             user_id=user_id, site_id=site.id, ereignis_typ="site_wiedereroeffnet", quelle=quelle)
