"""HTTP-Regressionen fuer die MCP-Kontaktverwaltung."""

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.kontakt import Kontakt
from app.models.objekt import ObjektKontakt
from app.models.user import AuditLog
from tests.test_mcp_objekte import _rufe, _seed, _token


def _anlegen(client, token: str, name: str = "Neue Person") -> dict:
    return _rufe(
        client,
        token,
        "kontakt_anlegen",
        felder={"typ": "person", "anzeigename": name, "email": f"{name.lower().replace(' ', '.')}@example.test"},
        telefone=[],
        kategorien=["MCP-Test"],
    )


def test_kontakt_anlegen_lesen_aktualisieren_und_dubletten(client):
    seed = _seed("kontakt-crud", {"admin": "kontakt_verwalter"})
    token = _token(client, seed, "admin")
    assert "__fehler__" in _rufe(
        client,
        token,
        "kontakt_anlegen",
        felder={"typ": "person", "anzeigename": "Max Muster", "email": "max@example.test"},
    )
    erstellt = _anlegen(client, token, "Crud Person")
    gelesen = _rufe(client, token, "kontakt_lesen", kontakt_id=erstellt["id"])
    assert gelesen["telefone"] == []
    assert gelesen["kategorien"] == ["MCP-Test"]
    aktualisiert = _rufe(
        client,
        token,
        "kontakt_aktualisieren",
        kontakt_id=erstellt["id"],
        version=gelesen["version"],
        felder={"funktion": "Leitung"},
    )
    assert aktualisiert["nachher"]["funktion"] == "Leitung"
    assert aktualisiert["nachher"]["telefone"] == gelesen["telefone"]
    konflikt = _rufe(
        client, token, "kontakt_aktualisieren", kontakt_id=erstellt["id"], version=gelesen["version"], felder={}
    )
    assert konflikt["konflikt"] and "neu laden" in konflikt["fehler"]
    dubletten = _rufe(
        client, token, "kontakt_duplikate_pruefen", anzeigename="Crud Person", email="crud.person@example.test"
    )
    assert dubletten["duplikate_gefunden"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(AuditLog).filter_by(action="kontakt.mcp_angelegt", entity_id=erstellt["id"]).count() == 1
    finally:
        db.close()


def test_kontakt_archivieren_merge_cross_org_und_keine_freigabe(client):
    seed = _seed("kontakt-merge", {"admin": "kontakt_verwalter"})
    other = _seed("kontakt-merge-other", {"admin": "kontakt_verwalter"})
    token = _token(client, seed, "admin")
    assert "__fehler__" in _rufe(client, token, "kontakt_lesen", kontakt_id=other["kontakt_id"])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(
            ObjektKontakt(
                org_id=seed["org_id"], objekt_id=seed["objekt_id"], kontakt_id=seed["kontakt_id"], art="betreiber"
            )
        )
        db.commit()
    finally:
        db.close()
    assert "__fehler__" in _rufe(client, token, "kontakt_archivieren", kontakt_id=seed["kontakt_id"])
    archiv = _rufe(client, token, "kontakt_archivieren", kontakt_id=seed["kontakt_id"], bestaetigt=True)
    assert archiv["objektzuordnungen_anzahl"] == 1
    quelle = _anlegen(client, token, "Quelle")
    ziel = _anlegen(client, token, "Ziel")
    assert "__fehler__" in _rufe(client, token, "kontakt_zusammenfuehren", quelle_id=quelle["id"], ziel_id=ziel["id"])
    merge = _rufe(
        client, token, "kontakt_zusammenfuehren", quelle_id=quelle["id"], ziel_id=ziel["id"], bestaetigt=True
    )
    assert merge["ziel_id"] == ziel["id"] and merge["freigabe_konflikte"] == []
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Kontakt, quelle["id"]).archiviert
        assert db.query(ObjektKontakt).filter_by(kontakt_id=ziel["id"]).count() == 0
    finally:
        db.close()


def test_kontakt_tools_bei_deaktiviertem_kontaktmodul_nicht_nutzbar(client):
    seed = _seed("kontakt-modul-aus", {"admin": "kontakt_verwalter"}, kontakte=False)
    token = _token(client, seed, "admin")
    assert "__fehler__" in _rufe(client, token, "kontakt_suchen", q="Max")
    assert "__fehler__" in _rufe(client, token, "kontakt_kategorien")
