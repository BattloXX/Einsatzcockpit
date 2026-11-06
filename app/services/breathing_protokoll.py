"""Vollständiges AS-Protokoll aus strukturierten Daten und ergänzenden Audits."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, selectinload

from app.models.breathing import BreathingAssp, BreathingDeployment, BreathingTroop, BreathingWarnung
from app.models.incident import Incident, IncidentChange
from app.models.user import User
from app.services.breathing_service import get_asuew_settings, get_troop_cycles

LABELS = {
    "druck": "Druck", "kontrolle": "Kontrolle", "warnung": "Warnung / Eskalation",
    "status": "Status", "meldung": "Lagemeldung", "audit": "Änderung",
}
WARNUNGEN = {
    "one_third_due": "Kontrolle 1/3 fällig", "two_third_due": "Kontrolle 2/3 fällig",
    "withdraw": "Rückzugsdruck", "max_exceeded": "Max-Zeit überschritten",
    "funk_verloren": "Funkverlust", "notfall": "Notfall", "assp": "Atemschutzsammelplatz",
    "kein_sicherheitstrupp": "Kein Sicherheitstrupp", "kein_heartbeat": "Kein Heartbeat",
}


def utc(zeit: datetime) -> datetime:
    return zeit.astimezone(UTC).replace(tzinfo=None) if zeit.tzinfo else zeit


def gruppiere_nach_zyklus(ereignisse: Iterable[dict]) -> dict[int, list[dict]]:
    gruppen: dict[int, list[dict]] = {}
    for ereignis in ereignisse:
        gruppen.setdefault(ereignis["zyklus"], []).append(ereignis)
    return dict(sorted(gruppen.items()))


def troop_protokoll_ereignisse(
    troop: BreathingTroop, *, audits: Iterable[IncidentChange] = (),
    warnungen: Iterable[BreathingWarnung] = (), benutzer: Mapping[int, str] | None = None,
) -> list[dict]:
    """Kein DB-Zugriff: Aufrufer lädt alle Relationen und Akteure vorab.

    Audits ergänzen strukturierte Einträge um Akteur/Nachtrag. Nur tatsächlich
    abgedeckte Ereignisse werden unterdrückt; frühere Änderungen bleiben erhalten.
    """
    audits = list(audits)
    warnungen = list(warnungen)
    namen = benutzer or {}
    cycles = get_troop_cycles(troop)
    events: list[dict] = []
    member_cycles = {m.id: nr for nr, c in enumerate(cycles, 1) for m in c.active_members}
    members = {m.id: m for c in cycles for m in c.active_members}
    redeploys = sorted(a.id for a in audits if a.action == "troop.redeployed"
                       and a.entity_type == "breathing_troop" and a.entity_id == troop.id and a.id)
    matched: set[int] = set()

    def add(zeit, art, text, nr, user_id=None, nachgetragen=False, action=None, entity_id=None):
        if zeit is not None:
            events.append(dict(zeit=utc(zeit), art=art, text=text, akteur=namen.get(user_id, ""),
                               nachgetragen=bool(nachgetragen), zyklus=nr,
                               _action=action, _entity_id=entity_id))

    def cycle_for(zeit):
        # Vorbereitende Eingaben zwischen Rückkehr und erneutem Eintritt gehören
        # zum folgenden Zyklus, sobald dessen Snapshot angelegt wurde.
        for nr, c in enumerate(cycles[:-1], 1):
            end = c.back_at or c.withdraw_at or c.entry_at
            if end and utc(zeit) <= utc(end):
                return nr
        return len(cycles)

    for nr, c in enumerate(cycles, 1):
        for attr, label, action in [("entry_at", "Eintritt", "troop.started"),
                                    ("withdraw_at", "Rückzug", "troop.status_changed"),
                                    ("back_at", "Zurück", "troop.status_changed")]:
            add(getattr(c, attr), "status", label, nr, action=action)
        for m in c.active_members:
            if m.start_press is not None:
                add(c.entry_at, "druck", f"Anfangsdruck: {m.display_name}, {m.start_press:g} bar", nr)
            if m.withdraw_press is not None and m.objective_press_at is None:
                add(c.withdraw_at or c.entry_at, "druck",
                    f"Rückzugsdruck: {m.display_name}, {m.withdraw_press:g} bar", nr)
            add(m.objective_press_at, "druck", f"Einsatzziel: {m.display_name}, {m.objective_press} bar; "
                f"Rückzugsdruck {m.withdraw_press} bar", nr, action="troop.objective_reached", entity_id=m.id)
            if m.back_press is not None:
                add(c.back_at or c.withdraw_at, "druck", f"Enddruck: {m.display_name}, {m.back_press:g} bar",
                    nr, action="troop.back_pressure_reported", entity_id=m.id)
        # Über die Mitglieds-IDs filtern: keine Lazy-Loads von log.troop_member.
        ids = {m.id for m in c.active_members}
        for log in troop.pressure_logs:
            if log.troop_member_id in ids:
                m = members[log.troop_member_id]
                add(log.ts, "druck", f"{m.display_name}: {log.pressure_bar:g} bar" +
                    (f" - {log.note}" if log.note else ""), nr, log.recorded_by_user_id, log.nachgetragen,
                    "troop.pressure_logged", m.id)
        deployment_id = c.id if isinstance(c, BreathingDeployment) else None
        for k in troop.kontrollen:
            if k.deployment_id == deployment_id:
                stufe = {"one_third": "1/3", "two_third": "2/3"}.get(k.stufe, k.stufe)
                druck = f", niedrigster Druck {k.niedrigster_druck:g} bar" if k.niedrigster_druck is not None else ""
                add(k.erledigt_at, "kontrolle", f"Kontrolle {stufe}: " +
                    {"funk": "Funk", "kein_kontakt": "kein Funkkontakt"}.get(k.art, k.art) + druck +
                    (f" - {k.note}" if k.note else ""), nr, k.user_id, k.nachgetragen, "troop.kontrolle")
        for attr, label, action in [("funk_verloren_at", "Funkverlust gesetzt", "troop.funk_verloren"),
                                    ("notfall_at", "Notfall gesetzt", "troop.notfall"),
                                    ("escalated_at", "Eskalation", "troop.escalated")]:
            add(getattr(c, attr), "warnung", label, nr,
                user_id=getattr(c, "notfall_von_user_id", None) if attr == "notfall_at" else None, action=action)
        add(c.readiness_override_at, "status", f"Readiness-Override: {c.readiness_override_reason or ''}",
            nr, c.readiness_override_by_user_id, action="troop.readiness_overridden")
        if c.entry_at:
            for attr, label in [("task_text", "Auftrag"), ("location_text", "Standort")]:
                if getattr(c, attr):
                    add(c.entry_at, "meldung", f"{label}: {getattr(c, attr)}", nr, action=f"snapshot.{attr}")
    if troop.last_meldung_at and troop.last_meldung_text:
        add(troop.last_meldung_at, "meldung", troop.last_meldung_text, len(cycles), action="troop.meldung")

    for w in warnungen:
        if w.troop_id != troop.id:
            continue
        nr = next((i for i, c in enumerate(cycles, 1)
                   if c.entry_at and w.zyklus_start and utc(c.entry_at) == utc(w.zyklus_start)),
                  cycle_for(w.ausgeloest_at))
        label = WARNUNGEN.get(w.kind, w.kind)
        add(w.ausgeloest_at, "warnung", f"Warnung ausgelöst: {label}", nr)
        add(w.erledigt_at, "warnung", f"Warnung erledigt: {label}", nr)
        add(w.eskaliert_at, "warnung", f"Eskalation Stufe {w.eskalation_stufe}: {label}", nr)
        for z in w.zustellungen:
            add(z.gesendet_at or z.created_at, "warnung",
                f"{label}: {z.kanal.upper()} an {z.empfaenger_label or namen.get(z.user_id or 0, 'Unbekannt')} "
                f"(Stufe {z.stufe}), Status: {z.status}" + (f", Fehler: {z.fehler}" if z.fehler else ""),
                nr, z.user_id)

    # Die jüngste Enddruckmeldung gehört zum gespeicherten Enddruck. Ältere
    # Korrekturen bleiben als eigene Audit-Ereignisse sichtbar.
    for row in sorted(audits, key=lambda r: (utc(r.ts), r.id or 0), reverse=True):
        if not row.action.startswith("troop."):
            continue
        if not ((row.entity_type == "breathing_troop" and row.entity_id == troop.id)
                or (row.entity_type == "troop_member" and row.entity_id in members)):
            continue
        try:
            after = json.loads(row.after_json or "{}")
            if not isinstance(after, dict):
                after = {}
            zeit = utc(datetime.fromisoformat(after["erfasst_at"])) if after.get("erfasst_at") else utc(row.ts)
        except (ValueError, TypeError):
            after, zeit = {}, utc(row.ts)
        nr = member_cycles[row.entity_id] if row.entity_type == "troop_member" else cycle_for(zeit)
        if row.entity_type == "breathing_troop" and len(redeploys) == len(cycles) - 1 and row.id:
            # Audit-Reihenfolge bewahrt auch Aufhebungen nach der Rückkehr,
            # aber vor dem Wiedereinsatz im richtigen Zyklus.
            nr = 1 + sum(boundary <= row.id for boundary in redeploys)
        candidates = [e for e in events if id(e) not in matched and e["_action"] == row.action and e["zyklus"] == nr
                      and (e["_entity_id"] is None or e["_entity_id"] == row.entity_id)]
        # Status-/Ziel-Audits werden oft Millisekunden nach dem Modell gespeichert.
        candidates.sort(key=lambda e: abs((e["zeit"] - zeit).total_seconds()))
        match = next((e for e in candidates if abs((e["zeit"] - zeit).total_seconds()) < 2), None)
        if row.action == "troop.back_pressure_reported":
            match = next((e for e in candidates if f"{after.get('pressure_bar'):g} bar" in e["text"]), None) \
                if isinstance(after.get("pressure_bar"), (int, float)) else None
        if match:
            matched.add(id(match))
            match.update(akteur=namen.get(row.user_id or 0, match["akteur"]),
                         nachgetragen=match["nachgetragen"] or bool(after.get("nachgetragen")))
            if row.action == "troop.back_pressure_reported":
                match["zeit"] = zeit
            continue
        if row.action == "troop.warnung_zugestellt" and any(
            w.troop_id == troop.id and (not after.get("kind") or after["kind"] == w.kind)
            and any(z.gesendet_at and abs((utc(z.gesendet_at) - zeit).total_seconds()) < 2
                    and (not after.get("kanal") or z.kanal == after["kanal"])
                    and (after.get("stufe") is None or z.stufe == after["stufe"]) for z in w.zustellungen)
            for w in warnungen
        ):
            continue
        action = row.action
        art = "audit"
        text = action.removeprefix("troop.")
        if action in ("troop.funk_verloren", "troop.notfall", "troop.notfall_cleared"):
            art = "warnung"
            text = ("Funkverlust" if action == "troop.funk_verloren" else "Notfall") + (
                " aufgehoben" if after.get("active") is False else " gesetzt")
        elif action in ("troop.meldung", "troop.standort", "troop.auftrag"):
            art = "meldung"
            text = {"troop.meldung": "Lagemeldung", "troop.standort": "Standort", "troop.auftrag": "Auftrag"}[action]
            text += ": " + str(after.get("text") or after.get("location_text") or after.get("task_text") or "–")
        elif action.startswith("troop.warn_acked."):
            art = "warnung"
            kind = action.removeprefix("troop.warn_acked.")
            text = "Warnung quittiert: " + WARNUNGEN.get(kind, {
                "one_third": "1/3", "two_third": "2/3", "max_time": "Max-Zeit",
            }.get(kind, kind))
        else:
            art = {"troop.pressure_logged": "druck", "troop.objective_reached": "druck",
                   "troop.back_pressure_reported": "druck", "troop.kontrolle": "kontrolle",
                   "troop.warnung_zugestellt": "warnung", "troop.escalated": "warnung"}.get(action, "audit")
            text = {"troop.created": "Trupp angelegt", "troop.redeployed": "Wiedereinsatz vorbereitet",
                    "troop.status_changed": "Status geändert", "troop.details": "Truppdaten geändert",
                    "troop.started": "Eintritt", "troop.readiness_overridden": "Readiness-Override",
                    "troop.objective_reached": "Einsatzziel", "troop.back_pressure_reported": "Enddruck",
                    "troop.pressure_logged": "Druckmeldung", "troop.kontrolle": "Kontrolle",
                    "troop.warnung_zugestellt": "Warnung zugestellt", "troop.escalated": "Eskalation"}.get(
                        action, f"Änderung ({text})")
            feldnamen = {
                "pressure_bar": "Druck (bar)", "withdraw_press": "Rückzugsdruck (bar)",
                "status": "Status", "stufe": "Stufe", "art": "Art", "reason": "Grund", "issues": "Hinweise",
                "angriffsweg": "Angriffsweg", "funkkanal": "Funkkanal", "ueberwacher_name": "Überwacher",
                "ueberwacher_user_id": "Überwacher-ID", "members": "Personen", "lfd_nr": "Zyklus",
                "kind": "Warnung", "kanal": "Kanal", "note": "Notiz", "name": "Name",
                "is_sicherheitstrupp": "Sicherheitstrupp", "unit_name": "Einheit",
                "planned_duration_min": "Geplante Einsatzzeit (min)", "bottle_preset": "Flasche",
            }
            details = [f"{feldnamen.get(key, key)}: {value}" for key, value in after.items()
                       if key not in ("erfasst_at", "nachgetragen")]
            if row.entity_type == "troop_member":
                details.insert(0, members[row.entity_id].display_name)
            if details:
                text += " - " + "; ".join(details)
        add(zeit, art, text, nr, row.user_id, after.get("nachgetragen"))

    events.sort(key=lambda e: e["zeit"])
    # Dauer nur aus belegtem Beginn/Ende; offene Situationen nicht als beendet ausgeben.
    starts: dict[tuple[int, str], datetime] = {}
    for e in events:
        for label in ("Funkverlust", "Notfall"):
            key = (e["zyklus"], label)
            if e["text"] == f"{label} gesetzt":
                starts.setdefault(key, e["zeit"])
            elif e["text"] == f"{label} aufgehoben" and key in starts:
                dauer = int((e["zeit"] - starts.pop(key)).total_seconds())
                e["text"] += f" (Dauer {dauer // 60} min {dauer % 60} s)"
        e.pop("_action")
        e.pop("_entity_id")
    for (nr, label), start in starts.items():
        c = cycles[nr - 1]
        ende = utc(c.back_at) if c.back_at else datetime.now(UTC).replace(tzinfo=None)
        if ende >= start:
            dauer = int((ende - start).total_seconds())
            e = next(e for e in events if e["zyklus"] == nr and e["zeit"] == start
                     and e["text"] == f"{label} gesetzt")
            e["text"] += f" (Dauer {'bis Rückkehr' if c.back_at else 'bisher'} {dauer // 60} min {dauer % 60} s)"
    return events


def load_protokoll_context(db: Session, incident: Incident) -> list[BreathingTroop]:
    """Einmal pro Einsatz laden, unabhängig von der Zahl der Trupps/Zyklen."""
    troops = db.query(BreathingTroop).options(
        selectinload(BreathingTroop.members), selectinload(BreathingTroop.pressure_logs),
        selectinload(BreathingTroop.kontrollen),
        selectinload(BreathingTroop.deployments).selectinload(BreathingDeployment.members),
        selectinload(BreathingTroop.deployments).selectinload(BreathingDeployment.pressure_logs),
    ).filter(BreathingTroop.incident_id == incident.id).order_by(BreathingTroop.id).all()
    audits = db.query(IncidentChange).filter(
        IncidentChange.incident_id == incident.id, IncidentChange.action.like("troop.%"),
    ).order_by(IncidentChange.ts, IncidentChange.id).all()
    warnungen = db.query(BreathingWarnung).options(selectinload(BreathingWarnung.zustellungen)).filter(
        BreathingWarnung.incident_id == incident.id,
    ).all()
    ids = {a.user_id for a in audits if a.user_id}
    for t in troops:
        if t.notfall_von_user_id:
            ids.add(t.notfall_von_user_id)
        ids.update(k.user_id for k in t.kontrollen if k.user_id)
        ids.update(p.recorded_by_user_id for p in t.pressure_logs if p.recorded_by_user_id)
        ids.update(c.readiness_override_by_user_id for c in get_troop_cycles(t) if c.readiness_override_by_user_id)
    ids.update(z.user_id for w in warnungen for z in w.zustellungen if z.user_id)
    namen = {u.id: u.full_name or u.username for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    erholung = get_asuew_settings(db, incident.primary_org_id).erholzeit_min if incident.primary_org_id else 30
    now = datetime.now(UTC).replace(tzinfo=None)
    alle_zyklen = [c for t in troops for c in get_troop_cycles(t)]
    for t in troops:
        ereignisse = troop_protokoll_ereignisse(t, audits=audits, warnungen=warnungen, benutzer=namen)
        t.protokoll_ereignisse = ereignisse  # type: ignore[attr-defined]
        gruppen = gruppiere_nach_zyklus(ereignisse)
        cycles = get_troop_cycles(t)
        for nr, c in enumerate(cycles, 1):
            deployment_id = c.id if isinstance(c, BreathingDeployment) else None
            controls = [k for k in t.kontrollen if k.deployment_id == deployment_id]
            fristen = []
            for stufe, label, faktor in [("one_third", "1/3", 20), ("two_third", "2/3", 40)]:
                due = utc(c.entry_at) + timedelta(seconds=c.planned_duration_min * faktor) \
                    if c.entry_at and c.planned_duration_min else None
                ende = utc(c.back_at) if c.back_at else now
                fristen.append(dict(label=label, versaeumt=bool(due and due < ende and not any(
                    k.stufe == stufe and k.art == "funk" for k in controls))))
            previous_members = {m.member_id for old in alle_zyklen if old is not c and old.back_at and c.entry_at
                                and utc(old.back_at) <= utc(c.entry_at) < utc(old.back_at) + timedelta(minutes=erholung)
                                for m in old.active_members if m.member_id}
            c.protokoll = dict(  # type: ignore[union-attr]
                nr=nr, ereignisse=gruppen.get(nr, []), fristen=fristen,
                nachgetragen=sum(e["nachgetragen"] for e in gruppen.get(nr, [])),
                erholzeit_verletzt=any(m.member_id in previous_members for m in c.active_members if m.member_id),
                erholzeit_min=erholung,
            )
    incident.protokoll_assp = db.query(BreathingAssp).filter(BreathingAssp.incident_id == incident.id).first()  # type: ignore[attr-defined]
    return troops
