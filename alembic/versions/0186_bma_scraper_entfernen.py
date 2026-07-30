"""BMA-Scraper entfernen und Kontakt-IDs stabilisieren.

Revision ID: 0186
Revises: 0185
"""
import re
import unicodedata
from collections import defaultdict

from alembic import op
from sqlalchemy import text

revision = "0186"
down_revision = "0185"
branch_labels = None
depends_on = None


def _column_exists(conn, table, column):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {"t": table, "c": column}).scalar())


def _slug(name):
    name = (name or "").translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}))
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:60] or "unbenannt"


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    saetze = conn.execute(text("SELECT org_id,objekt_id,extern_id,bma_nummer FROM bma_import_satz WHERE objekt_id IS NOT NULL")).mappings().all()
    je_objekt = defaultdict(list)
    for satz in saetze:
        je_objekt[(satz["org_id"], satz["objekt_id"])].append(satz)
    kontakte = conn.execute(text(
        "SELECT id,org_id,objekt_id,art,name,sort FROM objekt_kontakt WHERE extern_quelle='dibos_bma' ORDER BY org_id,objekt_id,sort,id"
    )).mappings().all()
    kollisionszaehler = defaultdict(int)
    for kontakt in kontakte:
        kandidaten = je_objekt.get((kontakt["org_id"], kontakt["objekt_id"]), [])
        pdf = [s for s in kandidaten if s["extern_id"].startswith("pdf:")]
        basis = pdf[0]["extern_id"] if len(pdf) == 1 else None
        if not pdf:
            scraper = [s for s in kandidaten if s["bma_nummer"]]
            if scraper:
                basis = "pdf:" + scraper[0]["bma_nummer"]
        if basis is None:
            conn.execute(text("UPDATE objekt_kontakt SET extern_quelle=NULL,extern_id=NULL WHERE id=:id"), {"id": kontakt["id"]})
            continue
        extern_id = f"{basis}:{kontakt['art']}:{_slug(kontakt['name'])}"
        kollisionszaehler[(kontakt["org_id"], kontakt["objekt_id"], extern_id)] += 1
        nr = kollisionszaehler[(kontakt["org_id"], kontakt["objekt_id"], extern_id)]
        if nr > 1:
            extern_id += f"#{nr}"
        conn.execute(text("UPDATE objekt_kontakt SET extern_id=:extern_id WHERE id=:id"),
                     {"extern_id": extern_id, "id": kontakt["id"]})
    conn.execute(text("DELETE FROM bma_import_satz WHERE extern_id NOT LIKE 'pdf:%'"))
    if not _column_exists(conn, "bma_import_satz", "ignoriert_hash"):
        conn.execute(text("ALTER TABLE bma_import_satz ADD COLUMN ignoriert_hash VARCHAR(64) NULL"))
    if _column_exists(conn, "bma_import_satz", "kontakte_uebernommen"):
        conn.execute(text("UPDATE bma_import_satz SET ignoriert_hash=bestaetigt_hash WHERE kontakte_uebernommen=FALSE AND bestaetigt_hash IS NOT NULL AND bestaetigt_hash=quell_hash"))
        conn.execute(text("ALTER TABLE bma_import_satz DROP COLUMN kontakte_uebernommen"))
    for column in ("extern_guid", "quell_change_date"):
        if _column_exists(conn, "bma_import_satz", column):
            conn.execute(text(f"ALTER TABLE bma_import_satz DROP COLUMN `{column}`"))
    for column in ("enabled", "base_url", "session_cookie_enc", "session_gesetzt_am", "keepalive_aktiv", "sync_stunde", "sync_minute", "letzter_lauf_am", "letzter_lauf_status", "letzter_lauf_meldung", "import_rechnungsadresse"):
        if _column_exists(conn, "org_bma_import_config", column):
            conn.execute(text(f"ALTER TABLE org_bma_import_config DROP COLUMN `{column}`"))
    conn.execute(text("DROP TABLE IF EXISTS bma_import_lauf"))


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    # Geloeschte Scraper-Daten und alte IDs sind nicht wiederherstellbar.
    if not _column_exists(conn, "bma_import_satz", "kontakte_uebernommen"):
        conn.execute(text("ALTER TABLE bma_import_satz ADD COLUMN kontakte_uebernommen BOOLEAN NOT NULL DEFAULT FALSE"))
    for column, typ in (("extern_guid", "VARCHAR(64) NULL"), ("quell_change_date", "DATETIME NULL")):
        if not _column_exists(conn, "bma_import_satz", column):
            conn.execute(text(f"ALTER TABLE bma_import_satz ADD COLUMN `{column}` {typ}"))
    if _column_exists(conn, "bma_import_satz", "ignoriert_hash"):
        conn.execute(text("ALTER TABLE bma_import_satz DROP COLUMN ignoriert_hash"))
    definitionen = {
        "enabled": "BOOLEAN NOT NULL DEFAULT FALSE", "base_url": "VARCHAR(300) NULL",
        "session_cookie_enc": "TEXT NULL", "session_gesetzt_am": "DATETIME NULL",
        "keepalive_aktiv": "BOOLEAN NOT NULL DEFAULT TRUE", "sync_stunde": "INT NOT NULL DEFAULT 3",
        "sync_minute": "INT NOT NULL DEFAULT 30", "letzter_lauf_am": "DATETIME NULL",
        "letzter_lauf_status": "VARCHAR(20) NULL", "letzter_lauf_meldung": "VARCHAR(300) NULL",
        "import_rechnungsadresse": "BOOLEAN NOT NULL DEFAULT FALSE",
    }
    for column, typ in definitionen.items():
        if not _column_exists(conn, "org_bma_import_config", column):
            conn.execute(text(f"ALTER TABLE org_bma_import_config ADD COLUMN `{column}` {typ}"))
    conn.execute(text("""CREATE TABLE IF NOT EXISTS bma_import_lauf (
        id BIGINT NOT NULL AUTO_INCREMENT, org_id BIGINT NOT NULL, gestartet_am DATETIME NOT NULL,
        beendet_am DATETIME NULL, ausloeser VARCHAR(20) NOT NULL DEFAULT 'loop', status VARCHAR(20) NOT NULL DEFAULT 'ok',
        gefunden INT NOT NULL DEFAULT 0, neu_angelegt INT NOT NULL DEFAULT 0, aktualisiert INT NOT NULL DEFAULT 0,
        vorschlaege INT NOT NULL DEFAULT 0, uebersprungen INT NOT NULL DEFAULT 0, fehler INT NOT NULL DEFAULT 0,
        meldung VARCHAR(500) NULL, PRIMARY KEY(id), CONSTRAINT fk_bma_import_lauf_org FOREIGN KEY(org_id) REFERENCES fire_dept(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""))
