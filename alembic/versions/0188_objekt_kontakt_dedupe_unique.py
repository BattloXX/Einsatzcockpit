"""BMA-Kontaktdubletten bereinigen und externe Identitaet absichern.

Revision ID: 0188
Revises: 0187
"""
import re
import unicodedata
from collections import defaultdict

from alembic import op
from sqlalchemy import text

revision = "0188"
down_revision = "0187"
branch_labels = None
depends_on = None


def _slug(name):
    """Stabile Kopie von bma_pdf_parser.namens_slug fuer historische Migrationen."""
    name = (name or "").translate(str.maketrans({
        "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
    }))
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:60] or "unbenannt"


def _pdf_praefix(extern_id):
    if not extern_id or not extern_id.startswith("pdf:"):
        return None
    teile = extern_id.split(":", 2)
    return ":".join(teile[:2]) if len(teile) >= 2 else None


def _index_exists(conn, name):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='objekt_kontakt' AND INDEX_NAME=:name"
    ), {"name": name}).scalar())


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return

    kontakte = conn.execute(text(
        "SELECT id,org_id,objekt_id,art,name,erreichbarkeit,extern_quelle,extern_id "
        "FROM objekt_kontakt ORDER BY id"
    )).mappings().all()

    # Union-Find bildet erst exakte Schluessel-Dubletten und danach die bewusst
    # adoptierbaren Handpflege/Import-Gruppen auf jeweils die kleinste id ab.
    parent = {k["id"]: k["id"] for k in kontakte}

    def find(kontakt_id):
        while parent[kontakt_id] != kontakt_id:
            parent[kontakt_id] = parent[parent[kontakt_id]]
            kontakt_id = parent[kontakt_id]
        return kontakt_id

    def vereinige(ids):
        wurzel = min(find(i) for i in ids)
        for kontakt_id in ids:
            parent[find(kontakt_id)] = wurzel

    exakt = defaultdict(list)
    for kontakt in kontakte:
        if kontakt["extern_id"] is not None:
            exakt[(kontakt["org_id"], kontakt["objekt_id"], kontakt["extern_quelle"],
                   kontakt["extern_id"])].append(kontakt["id"])
    for ids in exakt.values():
        if len(ids) > 1:
            vereinige(ids)

    personen = defaultdict(list)
    for kontakt in kontakte:
        personen[(kontakt["org_id"], kontakt["objekt_id"], kontakt["art"] or "sonstig",
                  _slug(kontakt["name"]))].append(kontakt)
    adoptierte_gruppen = set()
    for schluessel, gruppe in personen.items():
        manuell = [k for k in gruppe if k["extern_quelle"] is None]
        importiert = [k for k in gruppe if k["extern_quelle"] == "dibos_bma" and k["extern_id"]]
        praefixe = {_pdf_praefix(k["extern_id"]) for k in importiert}
        praefixe.discard(None)
        # Mehrere Datenblaetter verwalten absichtlich disjunkte Kontaktmengen.
        if manuell and importiert and len(praefixe) <= 1:
            vereinige([k["id"] for k in gruppe])
            adoptierte_gruppen.add(schluessel)

    gruppen = defaultdict(list)
    for kontakt in kontakte:
        gruppen[find(kontakt["id"])].append(kontakt)

    updates = []
    verlierer = []
    for gruppe in gruppen.values():
        ueberlebender = min(gruppe, key=lambda k: k["id"])
        verlierer.extend((k["id"], ueberlebender["id"]) for k in gruppe if k["id"] != ueberlebender["id"])
        erreichbarkeit = next((k["erreichbarkeit"] for k in gruppe if k["erreichbarkeit"]), None)
        extern_quelle, extern_id = ueberlebender["extern_quelle"], ueberlebender["extern_id"]
        schluessel = (ueberlebender["org_id"], ueberlebender["objekt_id"],
                      ueberlebender["art"] or "sonstig", _slug(ueberlebender["name"]))
        if schluessel in adoptierte_gruppen:
            importzeile = next(k for k in gruppe if k["extern_quelle"] == "dibos_bma" and k["extern_id"])
            extern_quelle, extern_id = importzeile["extern_quelle"], importzeile["extern_id"]
        updates.append({"id": ueberlebender["id"], "erreichbarkeit": erreichbarkeit,
                        "extern_quelle": extern_quelle, "extern_id": extern_id})

    # ON DELETE SET NULL darf die Hausverwaltungs-Zuordnung nicht still verlieren.
    for kontakt_id, ueberlebender_id in verlierer:
        conn.execute(text(
            "UPDATE objekt_wohnanlage SET hausverwaltung_kontakt_id=:neu "
            "WHERE hausverwaltung_kontakt_id=:alt"
        ), {"neu": ueberlebender_id, "alt": kontakt_id})
    for kontakt_id, _ in verlierer:
        conn.execute(text("DELETE FROM objekt_kontakt WHERE id=:id"), {"id": kontakt_id})
    for update in updates:
        conn.execute(text(
            "UPDATE objekt_kontakt SET erreichbarkeit=:erreichbarkeit, "
            "extern_quelle=:extern_quelle, extern_id=:extern_id WHERE id=:id"
        ), update)

    rest = conn.execute(text(
        "SELECT org_id,objekt_id,extern_quelle,extern_id,COUNT(*) AS anzahl "
        "FROM objekt_kontakt WHERE extern_id IS NOT NULL "
        "GROUP BY org_id,objekt_id,extern_quelle,extern_id HAVING COUNT(*) > 1"
    )).mappings().all()
    if rest:
        raise RuntimeError(f"0188: Verbleibende ObjektKontakt-Dubletten vor UNIQUE: {list(rest)}")
    if not _index_exists(conn, "uq_objekt_kontakt_extern"):
        conn.execute(text(
            "ALTER TABLE objekt_kontakt ADD UNIQUE KEY uq_objekt_kontakt_extern "
            "(org_id,objekt_id,extern_quelle,extern_id)"
        ))


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    # Geloeschte Dubletten sind nicht wiederherstellbar.
    if _index_exists(conn, "uq_objekt_kontakt_extern"):
        conn.execute(text("ALTER TABLE objekt_kontakt DROP INDEX uq_objekt_kontakt_extern"))
