"""Fahrtenbuch-Mitglieder und historische Fahrten bereinigen.

Revision ID: 0231
Revises: 0230
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision = "0231"
down_revision = "0230"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

REFERENZEN = (
    ("api_message_recipient", "member_id"),
    ("member_qualification", "member_id"),
    ("atemschutz_pruefung", "traeger_member_id"),
    ("member_tag_assignment", "member_id"),
    ("fahrt", "maschinist_member_id"),
    ("pressure_log", "member_id"),
    ("fahrt", "maschinist2_member_id"),
    ("site_resource_assignment", "member_id"),
    ("fahrt", "seilwinde_bediener_member_id"),
    ("sms_einsatzinfo_recipient", "member_id"),
    ("fahrt", "ausbildner_member_id"),
    ("sms_forward_rule_member", "member_id"),
    ("fahrt", "gruppenkommandant_member_id"),
    ("sms_group_member", "member_id"),
    ("fahrt", "einsatzleiter_member_id"),
    ("sms_log_recipient", "member_id"),
    ("gsl_staff_assignment", "member_id"),
    ("staff_assignment", "member_id"),
    ("incident", "incident_leader_member_id"),
    ("teilnahme", "mitglied_id"),
    ("incident_column", "section_leader_member_id"),
    ("troop_member", "member_id"),
    ("incident_vehicle", "commander_member_id"),
    ("uas_pilot", "person_id"),
    ("incident_vehicle", "fahrer_member_id"),
    ("incident_vehicle", "fahrer2_member_id"),
    ("lage_einheit_leader", "member_id"),
)

ROLLEN = (
    ("maschinist_member_id", "maschinist_name"),
    ("maschinist2_member_id", "maschinist2_name"),
    ("seilwinde_bediener_member_id", "seilwinde_bediener_name"),
    ("ausbildner_member_id", "ausbildner_name"),
    ("gruppenkommandant_member_id", "gruppenkommandant_name"),
    ("einsatzleiter_member_id", "einsatzleiter_name"),
)


def _normalisiere(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", (name or "").strip().casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(sorted(re.findall(r"[a-z0-9]+", text)))


def _tabellen(bind) -> dict[str, sa.Table]:
    inspector = sa.inspect(bind)
    namen = set(inspector.get_table_names())
    metadata = sa.MetaData()
    gebraucht = {"member", "fahrt", "vehicle_master"} | {t for t, _ in REFERENZEN}
    return {name: sa.Table(name, metadata, autoload_with=bind) for name in gebraucht if name in namen}


def _eindeutige_spaltengruppen(bind, table: sa.Table, member_col: str) -> list[tuple[str, ...]]:
    inspector = sa.inspect(bind)
    gruppen = [tuple(c.name for c in table.primary_key.columns)]
    gruppen.extend(tuple(u["column_names"]) for u in inspector.get_unique_constraints(table.name))
    return [g for g in gruppen if member_col in g and len(g) > 1]


def _entferne_kollisionen(bind, table: sa.Table, member_col: str, alt: int, neu: int) -> None:
    for gruppe in _eindeutige_spaltengruppen(bind, table, member_col):
        rest = [name for name in gruppe if name != member_col]
        loser_rows = bind.execute(sa.select(table).where(table.c[member_col] == alt)).mappings().all()
        for row in loser_rows:
            bedingungen = [table.c[member_col] == neu]
            bedingungen.extend(table.c[name] == row[name] for name in rest)
            if bind.execute(sa.select(sa.literal(1)).select_from(table).where(*bedingungen)).first():
                pk = [table.c[col.name] == row[col.name] for col in table.primary_key.columns]
                bind.execute(table.delete().where(*pk))


def _haenge_referenzen_um(bind, tabellen: dict[str, sa.Table], alt: int, neu: int) -> None:
    for table_name, column_name in REFERENZEN:
        table = tabellen.get(table_name)
        if table is None or column_name not in table.c:
            continue
        _entferne_kollisionen(bind, table, column_name, alt, neu)
        bind.execute(table.update().where(table.c[column_name] == alt).values({column_name: neu}))

    uebrig = {}
    for table_name, column_name in REFERENZEN:
        table = tabellen.get(table_name)
        if table is None or column_name not in table.c:
            continue
        anzahl = bind.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c[column_name] == alt)
        ).scalar_one()
        if anzahl:
            uebrig[f"{table_name}.{column_name}"] = anzahl
    if uebrig:
        raise RuntimeError(f"Member-Referenzen konnten nicht vollstaendig umgehaengt werden: {uebrig}")


def _merge_members(bind, tabellen: dict[str, sa.Table]) -> None:
    member = tabellen["member"]
    rows = bind.execute(sa.select(member)).mappings().all()
    gruppen: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        key = ((row["lastname"] or "").strip().casefold(), (row["firstname"] or "").strip().casefold())
        gruppen[key].append(row)
    for gruppe in gruppen.values():
        alte = [r for r in gruppe if r["org_id"] is None and r.get("sybos_id") is None]
        neue = [r for r in gruppe if r["org_id"] is not None and r.get("sybos_id") is not None]
        if len(alte) != 1 or len(neue) != 1:
            continue
        loser, winner = alte[0], neue[0]
        werte = {}
        for feld in ("phone", "email"):
            if feld in member.c and not winner.get(feld) and loser.get(feld):
                werte[feld] = loser[feld]
        if werte:
            bind.execute(member.update().where(member.c.id == winner["id"]).values(**werte))
        _haenge_referenzen_um(bind, tabellen, loser["id"], winner["id"])
        bind.execute(member.delete().where(member.c.id == loser["id"]))


def _korrigiere_falsche_member_ids(bind, tabellen: dict[str, sa.Table]) -> None:
    """Repariert Fahrten, deren Mitglieds-ID nicht zum erfassten Namen passt.

    Entsteht, wenn im Erfassungsformular die versteckte member_id eines zuvor
    gewaehlten Vorschlags stehen blieb, waehrend der Name ueberschrieben wurde.
    Muss vor _normalisiere_fahrten laufen: dort wuerde der Freitextname durch
    den Namen des falsch verknuepften Mitglieds ersetzt und der Fehler waere
    nicht mehr erkennbar.
    """
    fahrt = tabellen.get("fahrt")
    member = tabellen["member"]
    if fahrt is None:
        return
    members = bind.execute(sa.select(member)).mappings().all()
    by_id = {row["id"]: row for row in members}
    aktive_by_org: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in members:
        if row.get("active") and row.get("org_id") is not None:
            schluessel = _normalisiere(f"{row['lastname']} {row['firstname']}")
            aktive_by_org[row["org_id"]][schluessel].append(row)

    for row in bind.execute(sa.select(fahrt)).mappings().all():
        werte = {}
        for id_col, name_col in ROLLEN:
            if id_col not in fahrt.c or name_col not in fahrt.c:
                continue
            verknuepft = by_id.get(row[id_col])
            if not verknuepft or not row[name_col]:
                continue
            erfasst = _normalisiere(row[name_col])
            if erfasst == _normalisiere(f"{verknuepft['lastname']} {verknuepft['firstname']}"):
                continue
            kandidaten = aktive_by_org[row["org_id"]].get(erfasst, [])
            if len(kandidaten) == 1:
                werte[id_col] = kandidaten[0]["id"]
                logger.info(
                    "Fahrt %s: %s zeigte auf %s, erfasst war %r -> korrigiert",
                    row["id"], id_col, row[id_col], row[name_col],
                )
            else:
                logger.warning(
                    "Fahrt %s: %s (%s) passt nicht zum Namen %r, keine eindeutige Korrektur",
                    row["id"], id_col, row[id_col], row[name_col],
                )
        if werte:
            bind.execute(fahrt.update().where(fahrt.c.id == row["id"]).values(**werte))


def _normalisiere_fahrten(bind, tabellen: dict[str, sa.Table]) -> None:
    fahrt = tabellen.get("fahrt")
    member = tabellen["member"]
    if fahrt is None:
        return
    members = bind.execute(sa.select(member)).mappings().all()
    by_id = {row["id"]: row for row in members}
    aktive_by_org: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in members:
        if row.get("active") and row.get("org_id") is not None:
            name = f"{row['lastname']} {row['firstname']}"
            aktive_by_org[row["org_id"]][_normalisiere(name)].append(row)

    explizit = {
        _normalisiere("horwart Thomas"): _normalisiere("Horwath Thomas"),
        _normalisiere("Berger oli"): _normalisiere("Berger Oliver"),
        _normalisiere("Christian schertler"): _normalisiere("Schertler Christian"),
        _normalisiere("Hanspeter Abler"): _normalisiere("Abler Hans-Peter"),
    }
    rows = bind.execute(sa.select(fahrt)).mappings().all()
    for row in rows:
        werte = {}
        for id_col, name_col in ROLLEN:
            if id_col not in fahrt.c or name_col not in fahrt.c:
                continue
            member_row = by_id.get(row[id_col])
            if member_row:
                werte[name_col] = f"{member_row['lastname']} {member_row['firstname']}"
            elif row[id_col] is None and row[name_col]:
                key = _normalisiere(row[name_col])
                key = explizit.get(key, key)
                kandidaten = aktive_by_org[row["org_id"]].get(key, [])
                if len(kandidaten) == 1:
                    kandidat = kandidaten[0]
                    werte[id_col] = kandidat["id"]
                    werte[name_col] = f"{kandidat['lastname']} {kandidat['firstname']}"
                elif id_col == "maschinist_member_id":
                    logger.info("Fahrt %s: Freitextname nicht eindeutig zuordenbar: %s", row["id"], row[name_col])
        if werte:
            bind.execute(fahrt.update().where(fahrt.c.id == row["id"]).values(**werte))


def _storniere_doppelfahrten(bind, tabellen: dict[str, sa.Table]) -> None:
    fahrt = tabellen.get("fahrt")
    if fahrt is None:
        return
    # Die bekannten echten Doppelerfassungen werden anhand ihrer Fahrtdaten gesucht.
    # Nur vollstaendig identische aktive Datensaetze desselben Fahrzeugs/Zeitpunkts
    # werden beruehrt; die spaeter angelegte Zeile bleibt als Storno nachvollziehbar.
    vergleich = [
        name
        for name in (
            "org_id",
            "zeitpunkt",
            "fahrzeug_id",
            "maschinist_member_id",
            "maschinist_name",
            "km_stand_neu",
            "betriebsstunden_neu",
            "seilwinde_bh_neu",
            "zweck_id",
            "fahrttyp",
            "incident_id",
            "erfasst_via",
            "token_label",
        )
        if name in fahrt.c
    ]
    rows = bind.execute(sa.select(fahrt).where(fahrt.c.status == "aktiv")).mappings().all()
    gruppen: dict[tuple, list] = defaultdict(list)
    for row in rows:
        gruppen[tuple(row[name] for name in vergleich)].append(row)
    fahrzeug_ids = set()
    for gruppe in gruppen.values():
        if len(gruppe) != 2:
            continue
        spaeter = sorted(gruppe, key=lambda r: (r.get("created_at") or datetime.min, r["id"]))[-1]
        # Ueber den Primaerschluessel der bereits gelesenen Zeile aktualisieren:
        # ein Vergleich auf zeitpunkt/created_at haengt an der Datetime-Serialisierung
        # des Backends und trifft dann keine Zeile.
        bind.execute(
            fahrt.update()
            .where(fahrt.c.id == spaeter["id"])
            .values(status="storniert", storno_grund="Doppelerfassung (Datenbereinigung)")
        )
        fahrzeug_ids.add(spaeter["fahrzeug_id"])

    vehicle = tabellen.get("vehicle_master")
    if vehicle is None:
        return
    for vehicle_id in fahrzeug_ids:
        werte = {}
        for fahrt_col, vehicle_col in (
            ("km_stand_neu", "km_aktuell"),
            ("betriebsstunden_neu", "betriebsstunden_aktuell"),
            ("seilwinde_bh_neu", "seilwinde_bh_aktuell"),
        ):
            if fahrt_col in fahrt.c and vehicle_col in vehicle.c:
                maximum = bind.execute(
                    sa.select(sa.func.max(fahrt.c[fahrt_col])).where(
                        fahrt.c.fahrzeug_id == vehicle_id, fahrt.c.status == "aktiv"
                    )
                ).scalar()
                if maximum is not None:
                    werte[vehicle_col] = maximum
        if werte:
            bind.execute(vehicle.update().where(vehicle.c.id == vehicle_id).values(**werte))


def upgrade() -> None:
    bind = op.get_bind()
    tabellen = _tabellen(bind)
    if "member" not in tabellen:
        return
    _merge_members(bind, tabellen)
    _korrigiere_falsche_member_ids(bind, tabellen)
    _normalisiere_fahrten(bind, tabellen)
    _storniere_doppelfahrten(bind, tabellen)


def downgrade() -> None:
    # Bewusst irreversibel: zusammengefuehrte und normalisierte Bestandsdaten
    # koennen ohne vorherige Sicherung nicht verlaesslich rekonstruiert werden.
    pass
