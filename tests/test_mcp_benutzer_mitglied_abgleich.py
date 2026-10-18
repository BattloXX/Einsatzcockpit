"""Abgleich und geschuetzte MCP-Verknuepfungen, inklusive echter Transportaufrufe."""

import asyncio
import json

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.registry import TOOLS
from app.models.master import Member
from app.models.user import AuditLog, User
from tests.test_mcp_benutzer import _rufe, _seed, _token


@pytest.fixture
def ctx(request):
    seed = _seed(f"abgleich-{request.node.name}", {"admin": "admin"})
    db = SessionLocal()
    set_tenant_context(db, seed["org_id"])
    context = MCPContext(db, db.get(User, seed["users"]["admin"]), seed["org_id"], "test-mcp-client")
    yield context
    db.close()


def call(ctx, tool, **kwargs):
    return asyncio.run(TOOLS[tool].handler(ctx, **kwargs))


def pair(ctx, suffix="", **member_values):
    member = Member(org_id=ctx.org_id, firstname="Jörg", lastname=f"Müller{suffix}",
                    email=f"joerg{suffix}-{ctx.org_id}@example.org", phone="+43 664 1234567")
    for key, value in member_values.items():
        setattr(member, key, value)
    user = User(org_id=ctx.org_id, username=f"joerg-{ctx.org_id}-{suffix}", display_name="Joerg",
                full_name=f"Joerg Mueller{suffix}", email=f"JOERG{suffix}-{ctx.org_id}@example.org",
                phone="00436641234567",
                password_hash="x")
    ctx.db.add_all([member, user])
    ctx.db.commit()
    return user, member


def row_for(result, category, user):
    return next(row for row in result[category] if row["benutzer_id"] == user.id)


def audit(ctx):
    return ctx.db.query(AuditLog).filter(AuditLog.org_id == ctx.org_id,
                                       AuditLog.action.like("mcp.user.member_%")).all()


def test_abgleich_lesend_umlaute_telefon_und_bestehende(ctx):
    user, member = pair(ctx)
    ctx.db.commit = lambda: pytest.fail("Lesender Abgleich darf nicht committen")
    result = call(ctx, "benutzer_mitglied_abgleichen")
    row = row_for(result, "sicher", user)
    assert row["mitglied_id"] == member.id
    assert row["kandidaten"][0]["merkmale"] == ["name", "email", "telefon"]
    assert result["bestehend"]
    assert not (ctx.db.new or ctx.db.dirty or ctx.db.deleted)
    assert user.member_id is None and not audit(ctx)


@pytest.mark.parametrize("case", ["name", "email", "telefon", "typo", "duplicate_member", "duplicate_user",
                                  "inactive_member", "inactive_user", "taken"])
def test_unsichere_treffer_werden_nie_automatisch_verknuepft(ctx, case):
    user, member = pair(ctx)
    if case == "name":
        user.email = None
        user.phone = None
    elif case == "email":
        user.full_name = "Andere Person"
        user.phone = None
    elif case == "telefon":
        user.full_name = "Andere Person"
        user.email = None
    elif case == "typo":
        user.full_name = "Joerg Muellerr"
        user.email = user.phone = None
    elif case == "duplicate_member":
        ctx.db.add(Member(org_id=ctx.org_id, firstname=member.firstname, lastname=member.lastname,
                          email=member.email, phone=member.phone))
    elif case == "duplicate_user":
        ctx.db.add(User(org_id=ctx.org_id, username=f"double-{ctx.org_id}", display_name="Double",
                        full_name=user.full_name, phone=user.phone, password_hash="x"))
    elif case == "inactive_member":
        member.active = False
    elif case == "inactive_user":
        user.active = False
    elif case == "taken":
        ctx.user.member_id = member.id
    ctx.db.commit()
    result = call(ctx, "benutzer_mitglied_abgleichen")
    assert row_for(result, "pruefen", user)["automatisch_verknuepfbar"] is False
    with pytest.raises(ValueError):
        call(ctx, "benutzer_mitglied_verknuepfen", benutzer_id=user.id, mitglied_id=member.id)
    assert user.member_id is None and not audit(ctx)


def test_keine_leerwert_treffer(ctx):
    user, _ = pair(ctx)
    user.full_name = user.display_name = "Unbekannte Person"
    user.email = user.phone = None
    ctx.db.commit()
    assert row_for(call(ctx, "benutzer_mitglied_abgleichen"), "kein_treffer", user)["kandidaten"] == []


def test_schreiben_dry_run_idempotenz_audit_und_rollen_unveraendert(ctx):
    user, member = pair(ctx)
    original = (user.role_codes, user.active, user.email, user.full_name)
    args = {"benutzer_id": user.id, "mitglied_id": member.id}
    preview = call(ctx, "benutzer_mitglied_verknuepfen", **args, dry_run=True)
    assert preview["status"] == "simuliert" and preview["wuerde_aendern"]
    assert user.member_id is None and not audit(ctx)
    assert call(ctx, "benutzer_mitglied_verknuepfen", **args)["geaendert"]
    assert call(ctx, "benutzer_mitglied_verknuepfen", **args)["status"] == "uebersprungen"
    entries = audit(ctx)
    assert len(entries) == 1
    assert entries[0].user_id == ctx.user.id and entries[0].created_at
    payload = json.loads(entries[0].payload_json)
    assert payload["mitglied_id"] == {"alt": None, "neu": member.id}
    assert payload["client_id"] == "test-mcp-client"
    assert call(ctx, "benutzer_mitglied_trennen", benutzer_id=user.id, dry_run=True)["status"] == "simuliert"
    assert user.member_id == member.id
    assert call(ctx, "benutzer_mitglied_trennen", benutzer_id=user.id)["geaendert"]
    assert call(ctx, "benutzer_mitglied_trennen", benutzer_id=user.id)["status"] == "uebersprungen"
    assert len(audit(ctx)) == 2 and user.member_id is None
    assert json.loads(audit(ctx)[-1].payload_json)["mitglied_id"] == {"alt": member.id, "neu": None}
    assert (user.role_codes, user.active, user.email, user.full_name) == original


def test_bestehende_links_niemals_ueberschreiben_auch_mit_bestaetigung(ctx):
    user, member = pair(ctx)
    call(ctx, "benutzer_mitglied_verknuepfen", benutzer_id=user.id, mitglied_id=member.id)
    other = Member(org_id=ctx.org_id, firstname="Anderes", lastname="Mitglied")
    ctx.db.add(other)
    ctx.db.commit()
    with pytest.raises(ValueError, match="bereits verknuepft"):
        call(ctx, "benutzer_mitglied_verknuepfen", benutzer_id=user.id, mitglied_id=other.id,
             manuell_bestaetigt=True)
    with pytest.raises(ValueError, match="bereits mit einem Benutzer"):
        call(ctx, "benutzer_mitglied_verknuepfen", benutzer_id=ctx.user.id, mitglied_id=member.id,
             manuell_bestaetigt=True)
    assert user.member_id == member.id and len(audit(ctx)) == 1


def test_manuelle_bestaetigung_nach_pruefung(ctx):
    user, member = pair(ctx)
    user.email = user.phone = None
    ctx.db.commit()
    call(ctx, "benutzer_mitglied_verknuepfen", benutzer_id=user.id, mitglied_id=member.id, manuell_bestaetigt=True)
    assert user.member_id == member.id
    assert json.loads(audit(ctx)[0].payload_json)["manuell_bestaetigt"] is True


def test_bulk_simulation_fehlerisolierung_und_wiederholung(ctx, monkeypatch):
    user, member = pair(ctx, "eins", phone=None)
    other, other_member = pair(ctx, "zwei", phone=None)
    pairs = [{"benutzer_id": user.id, "mitglied_id": member.id},
             {"benutzer_id": other.id, "mitglied_id": other_member.id}]
    preview = call(ctx, "benutzer_mitglieder_bulk_verknuepfen", zuordnungen=pairs, dry_run=True)
    assert preview["zusammenfassung"]["simuliert"] == 2 and not audit(ctx)
    from app.services import benutzer_service

    real = benutzer_service.mitglied_verknuepfung_setzen

    def fail_one(db, actor, org_id, benutzer_id, *args, **kwargs):
        if benutzer_id == user.id:
            raise IntegrityError("test", {}, Exception("test"))
        return real(db, actor, org_id, benutzer_id, *args, **kwargs)

    monkeypatch.setattr(benutzer_service, "mitglied_verknuepfung_setzen", fail_one)
    result = call(ctx, "benutzer_mitglieder_bulk_verknuepfen", zuordnungen=pairs)
    assert result["zusammenfassung"]["erfolgreich"] == result["zusammenfassung"]["fehlgeschlagen"] == 1
    assert other.member_id == other_member.id and user.member_id is None
    monkeypatch.setattr(benutzer_service, "mitglied_verknuepfung_setzen", real)
    repeated = call(ctx, "benutzer_mitglieder_bulk_verknuepfen", zuordnungen=pairs + [pairs[0]])
    assert repeated["zusammenfassung"]["erfolgreich"] == 1
    assert repeated["zusammenfassung"]["uebersprungen"] == 2
    assert len(audit(ctx)) == 2


def test_bulk_unsichere_und_ungueltige_zuordnungen(ctx):
    user, member = pair(ctx)
    user.email = user.phone = None
    ctx.db.commit()
    result = call(ctx, "benutzer_mitglieder_bulk_verknuepfen", zuordnungen=[
        {"benutzer_id": user.id, "mitglied_id": member.id}, {"benutzer_id": user.id},
    ])
    assert result["zusammenfassung"]["uebersprungen"] == result["zusammenfassung"]["fehlgeschlagen"] == 1
    assert user.member_id is None and not audit(ctx)


def test_transport_berechtigungen_organisationsgrenzen_und_listen(client):
    own = _seed("abgleich-transport", {"admin": "admin", "fahrer": "fahrtenbuch_admin", "leser": "readonly"})
    foreign = _seed("abgleich-transport-fremd", {"admin": "admin"})
    admin = _token(client, own["usernames"]["admin"])
    driver = _token(client, own["usernames"]["fahrer"])
    reader = _token(client, own["usernames"]["leser"])
    users = _rufe(client, admin, "benutzer_liste", limit=200)["benutzer"]
    assert own["inactive"] in {u["id"] for u in users}
    assert foreign["visible"] not in {u["id"] for u in users}
    assert all({"vorname", "nachname", "email", "telefon", "mitglied_id", "organisations_id"} <= u.keys()
               for u in users)
    assert "__fehler__" in _rufe(client, driver, "benutzer_liste")
    assert "__fehler__" in _rufe(client, reader, "mitglieder_liste")
    members = _rufe(client, driver, "mitglieder_liste")["mitglieder"]
    assert all(m["organisations_id"] == own["org_id"] for m in members)
    member = next(m for m in members if m["benutzer_id"] == own["visible"])
    assert _rufe(client, admin, "mitglied_lesen", mitglied_id=member["id"])["benutzer_id"] == own["visible"]
    for tool in ("benutzer_mitglied_abgleichen", "benutzer_mitglied_trennen"):
        args = {} if tool.endswith("abgleichen") else {"benutzer_id": own["visible"]}
        assert "__fehler__" in _rufe(client, driver, tool, **args)
    assert "__fehler__" in _rufe(client, admin, "benutzer_mitglied_trennen", benutzer_id=foreign["visible"])
    assert "__fehler__" in _rufe(client, admin, "benutzer_mitglied_verknuepfen", benutzer_id=own["inactive"],
                                 mitglied_id=member["id"], manuell_bestaetigt=True)
    # Ein echter MCP-Schreibaufruf transportiert die OAuth-Client-ID ins Audit.
    result = _rufe(client, admin, "benutzer_mitglied_trennen", benutzer_id=own["visible"])
    assert result["geaendert"]
    db = SessionLocal()
    set_tenant_context(db, own["org_id"])
    try:
        entry = db.query(AuditLog).filter(AuditLog.org_id == own["org_id"],
                                          AuditLog.action == "mcp.user.member_unlinked").one()
        assert json.loads(entry.payload_json)["client_id"]
    finally:
        db.close()


def test_transport_verknuepfen_bulk_dry_run_und_paginierung(ctx, client):
    user, member = pair(ctx, "eins", phone=None)
    other, other_member = pair(ctx, "zwei", phone=None)
    inactive = Member(org_id=ctx.org_id, firstname="Inaktiv", lastname="Mitglied", active=False)
    ctx.db.add(inactive)
    ctx.db.commit()
    token = _token(client, ctx.user.username)
    assert _rufe(client, token, "benutzer_mitglied_abgleichen")["sicher"]
    args = {"benutzer_id": user.id, "mitglied_id": member.id}
    assert _rufe(client, token, "benutzer_mitglied_verknuepfen", **args, dry_run=True)["status"] == "simuliert"
    assert _rufe(client, token, "benutzer_mitglied_verknuepfen", **args)["status"] == "erfolgreich"
    result = _rufe(client, token, "benutzer_mitglieder_bulk_verknuepfen", zuordnungen=[
        args, {"benutzer_id": other.id, "mitglied_id": other_member.id},
    ])
    assert result["zusammenfassung"]["erfolgreich"] == result["zusammenfassung"]["uebersprungen"] == 1
    assert inactive.id in {m["id"] for m in _rufe(client, token, "mitglieder_liste", limit=200)["mitglieder"]}
    page1 = _rufe(client, token, "benutzer_liste", limit=1, seite=1)
    page2 = _rufe(client, token, "benutzer_liste", limit=1, seite=2)
    assert page1["truncated"] and page1["benutzer"][0]["id"] != page2["benutzer"][0]["id"]
    assert "__fehler__" in _rufe(client, token, "benutzer_liste", limit=201)
    assert "__fehler__" in _rufe(client, token, "benutzer_mitglied_verknuepfen", benutzer_id=user.id,
                                 mitglied_id=None)


def test_staler_sessionzustand_kann_bestehende_verknuepfung_nicht_ersetzen(ctx):
    user, member = pair(ctx)
    assert user.member_id is None
    other = Member(org_id=ctx.org_id, firstname="Andere", lastname="Zuordnung")
    ctx.db.add(other)
    ctx.db.commit()
    # Eine zweite Session hat inzwischen verknuepft; die erste haelt noch das alte Objekt.
    db = SessionLocal()
    set_tenant_context(db, ctx.org_id)
    try:
        db.get(User, user.id).member_id = other.id
        db.commit()
    finally:
        db.close()
    with pytest.raises(ValueError, match="bereits verknuepft"):
        call(ctx, "benutzer_mitglied_verknuepfen", benutzer_id=user.id, mitglied_id=member.id,
             manuell_bestaetigt=True)
    ctx.db.refresh(user)
    assert user.member_id == other.id and not audit(ctx)


def test_datenbank_unique_konflikt_wird_isoliert(ctx, monkeypatch):
    from app.services import benutzer_service

    user, member = pair(ctx)
    ctx.user.member_id = member.id
    ctx.db.commit()
    # Simuliert eine konkurrierende Belegung nach der fachlichen Vorpruefung.
    monkeypatch.setattr(benutzer_service, "validate_member_link", lambda *args: None)
    with pytest.raises(ValueError, match="zwischenzeitlich"):
        call(ctx, "benutzer_mitglied_verknuepfen", benutzer_id=user.id, mitglied_id=member.id,
             manuell_bestaetigt=True)
    assert user.member_id is None and not audit(ctx)
    assert ctx.db.get(User, ctx.user.id).member_id == member.id
