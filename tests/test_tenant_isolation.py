"""Tenant-Isolation-Tests (PR 1 + PR 2 + PR 3 + PR 5 Grundlage).

Akzeptanz-Kriterium PR 1: ungefiltertes select(Member) liefert nur Org-A-Daten,
wenn Org A im Session-Kontext gesetzt ist – obwohl beide Orgs in der DB liegen.

Akzeptanz-Kriterium PR 2: Zwei Orgs können beide Alarmtyp 'B3' mit unterschiedlichem
Label führen; get_alarm_type_by_code liefert org-scoped Ergebnis.

Akzeptanz-Kriterium PR 3: TaskSuggestion, MessageSuggestion, LageHint, DefaultMessage
sind vollständig org-isoliert; Org-B-Inhalte nicht aus Org-A-Kontext sichtbar.

Diese Fixture wird in jedem Folge-PR um neue Endpoints erweitert.
"""
import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite-Testumgebung
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import (
    AIPromptVersion,
    AlarmType,
    DefaultMessage,
    FireDept,
    LageHint,
    Member,
    MessageSuggestion,
    TaskSuggestion,
)
from app.models.user import Role
from app.services.alarm_service import get_alarm_type_by_code


TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture(scope="module")
def isolation_db():
    """Eigene In-Memory-DB für Isolation-Tests (unabhängig von conftest.py)."""
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # Zwei unabhängige Orgs
    org_a = FireDept(slug="org-a", name="Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="org-b", name="Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    # Je 2 Members pro Org
    db.add_all([
        Member(org_id=org_a.id, firstname="Anna", lastname="Alpha"),
        Member(org_id=org_a.id, firstname="Anton", lastname="Alpha"),
        Member(org_id=org_b.id, firstname="Bernd", lastname="Beta"),
        Member(org_id=org_b.id, firstname="Berta", lastname="Beta"),
    ])
    db.commit()

    yield db, org_a.id, org_b.id

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_tenant_filter_org_a(isolation_db):
    """Mit Org-A-Kontext: nur Org-A-Members sichtbar."""
    db, org_a_id, org_b_id = isolation_db
    set_tenant_context(db, org_a_id)

    members = db.query(Member).all()

    assert len(members) == 2
    assert all(m.org_id == org_a_id for m in members)
    lastnames = {m.lastname for m in members}
    assert lastnames == {"Alpha"}


def test_tenant_filter_org_b(isolation_db):
    """Mit Org-B-Kontext: nur Org-B-Members sichtbar."""
    db, org_a_id, org_b_id = isolation_db
    set_tenant_context(db, org_b_id)

    members = db.query(Member).all()

    assert len(members) == 2
    assert all(m.org_id == org_b_id for m in members)
    lastnames = {m.lastname for m in members}
    assert lastnames == {"Beta"}


def test_tenant_filter_none_sees_all(isolation_db):
    """Ohne Tenant-Kontext (system_admin): alle Members sichtbar."""
    db, org_a_id, org_b_id = isolation_db
    set_tenant_context(db, None)

    members = db.query(Member).all()

    assert len(members) == 4


def test_tenant_filter_include_all_tenants_bypass(isolation_db):
    """execution_option include_all_tenants=True umgeht den Filter."""
    db, org_a_id, org_b_id = isolation_db
    set_tenant_context(db, org_a_id)

    from sqlalchemy import select
    members = db.execute(
        select(Member).execution_options(include_all_tenants=True)
    ).scalars().unique().all()

    assert len(members) == 4


def test_no_cross_tenant_leak(isolation_db):
    """Org A kann keinen Member von Org B über .get() finden wenn Filter aktiv."""
    db, org_a_id, org_b_id = isolation_db
    set_tenant_context(db, None)

    # Hole eine Org-B-Member-ID
    b_member = db.query(Member).filter(Member.org_id == org_b_id).first()
    assert b_member is not None
    b_member_id = b_member.id

    # Jetzt Org-A-Kontext setzen und versuchen Org-B-Member abzufragen
    set_tenant_context(db, org_a_id)
    db.expire_all()

    found = db.query(Member).filter(Member.id == b_member_id).first()
    assert found is None, "Org-A darf keinen Org-B-Member sehen!"


# ---------------------------------------------------------------------------
# PR 2: AlarmType-Isolation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def alarm_type_db():
    """Eigene In-Memory-DB für AlarmType-Isolationstests."""
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    org_a = FireDept(slug="at-org-a", name="AT Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="at-org-b", name="AT Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    # Beide Orgs haben Alarmtyp 'B3' – mit unterschiedlichem Label (Akzeptanz-Kriterium PR 2)
    db.add_all([
        AlarmType(org_id=org_a.id, code="B3", category="B", label="Brand Mittel – Org A"),
        AlarmType(org_id=org_a.id, code="T1", category="T", label="Technisch Klein – Org A"),
        AlarmType(org_id=org_b.id, code="B3", category="B", label="Brand Mittel – Org B"),
        AlarmType(org_id=org_b.id, code="T2", category="T", label="Technisch Groß – Org B"),
    ])
    db.commit()

    yield db, org_a.id, org_b.id

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_alarm_type_isolation_org_a(alarm_type_db):
    """Mit Org-A-Kontext: nur Org-A-AlarmTypes sichtbar."""
    db, org_a_id, org_b_id = alarm_type_db
    set_tenant_context(db, org_a_id)

    alarm_types = db.query(AlarmType).all()

    assert len(alarm_types) == 2
    assert all(at.org_id == org_a_id for at in alarm_types)
    codes = {at.code for at in alarm_types}
    assert codes == {"B3", "T1"}


def test_alarm_type_isolation_org_b(alarm_type_db):
    """Mit Org-B-Kontext: nur Org-B-AlarmTypes sichtbar."""
    db, org_a_id, org_b_id = alarm_type_db
    set_tenant_context(db, org_b_id)

    alarm_types = db.query(AlarmType).all()

    assert len(alarm_types) == 2
    assert all(at.org_id == org_b_id for at in alarm_types)
    codes = {at.code for at in alarm_types}
    assert codes == {"B3", "T2"}


def test_alarm_type_same_code_different_orgs(alarm_type_db):
    """Akzeptanz PR 2: Zwei Orgs können beide Code 'B3' mit unterschiedlichem Label führen."""
    db, org_a_id, org_b_id = alarm_type_db
    set_tenant_context(db, None)

    all_b3 = db.query(AlarmType).filter(AlarmType.code == "B3").all()

    assert len(all_b3) == 2
    labels = {at.label for at in all_b3}
    assert "Brand Mittel – Org A" in labels
    assert "Brand Mittel – Org B" in labels
    org_ids = {at.org_id for at in all_b3}
    assert org_ids == {org_a_id, org_b_id}


def test_get_alarm_type_by_code_org_scoped(alarm_type_db):
    """get_alarm_type_by_code gibt den Alarmtyp der richtigen Org zurück."""
    db, org_a_id, org_b_id = alarm_type_db
    set_tenant_context(db, None)

    at_a = get_alarm_type_by_code(db, org_a_id, "B3")
    at_b = get_alarm_type_by_code(db, org_b_id, "B3")

    assert at_a is not None
    assert at_b is not None
    assert at_a.id != at_b.id
    assert at_a.label == "Brand Mittel – Org A"
    assert at_b.label == "Brand Mittel – Org B"


def test_get_alarm_type_by_code_missing_returns_none(alarm_type_db):
    """get_alarm_type_by_code liefert None für nicht existierenden Code."""
    db, org_a_id, _ = alarm_type_db
    set_tenant_context(db, None)

    result = get_alarm_type_by_code(db, org_a_id, "NONEXISTENT")

    assert result is None


def test_alarm_type_no_cross_tenant_leak(alarm_type_db):
    """Org A kann Alarmtyp 'T2' (nur Org B) nicht sehen wenn Filter aktiv."""
    db, org_a_id, org_b_id = alarm_type_db
    set_tenant_context(db, org_a_id)
    db.expire_all()

    result = db.query(AlarmType).filter(AlarmType.code == "T2").first()

    assert result is None, "Org-A darf keinen Org-B-AlarmType sehen!"


# ---------------------------------------------------------------------------
# PR 3: Vorlagen-Isolation (TaskSuggestion, MessageSuggestion, LageHint, DefaultMessage)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def stammdaten_db():
    """Eigene In-Memory-DB für Vorlagen-Isolationstests."""
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    org_a = FireDept(slug="sd-org-a", name="SD Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="sd-org-b", name="SD Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    db.add_all([
        TaskSuggestion(org_id=org_a.id, text="Auftrag Org A"),
        TaskSuggestion(org_id=org_b.id, text="Auftrag Org B"),
        MessageSuggestion(org_id=org_a.id, text="Meldung Org A"),
        MessageSuggestion(org_id=org_b.id, text="Meldung Org B"),
        LageHint(org_id=org_a.id, text="Lage-Hinweis Org A", display_order=0),
        LageHint(org_id=org_b.id, text="Lage-Hinweis Org B", display_order=0),
        DefaultMessage(org_id=org_a.id, text="Default Org A"),
        DefaultMessage(org_id=org_b.id, text="Default Org B"),
    ])
    db.commit()

    yield db, org_a.id, org_b.id

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_task_suggestion_isolation_org_a(stammdaten_db):
    """Mit Org-A-Kontext: nur Org-A-TaskSuggestions sichtbar."""
    db, org_a_id, org_b_id = stammdaten_db
    set_tenant_context(db, org_a_id)
    db.expire_all()

    results = db.query(TaskSuggestion).all()

    assert len(results) == 1
    assert results[0].text == "Auftrag Org A"


def test_message_suggestion_isolation_org_b(stammdaten_db):
    """Mit Org-B-Kontext: nur Org-B-MessageSuggestions sichtbar."""
    db, org_a_id, org_b_id = stammdaten_db
    set_tenant_context(db, org_b_id)
    db.expire_all()

    results = db.query(MessageSuggestion).all()

    assert len(results) == 1
    assert results[0].text == "Meldung Org B"


def test_lage_hint_isolation(stammdaten_db):
    """Mit Org-A-Kontext: nur Org-A-LageHints sichtbar."""
    db, org_a_id, org_b_id = stammdaten_db
    set_tenant_context(db, org_a_id)
    db.expire_all()

    results = db.query(LageHint).all()

    assert len(results) == 1
    assert results[0].text == "Lage-Hinweis Org A"


def test_default_message_isolation(stammdaten_db):
    """Mit Org-B-Kontext: nur Org-B-DefaultMessages sichtbar."""
    db, org_a_id, org_b_id = stammdaten_db
    set_tenant_context(db, org_b_id)
    db.expire_all()

    results = db.query(DefaultMessage).all()

    assert len(results) == 1
    assert results[0].text == "Default Org B"


def test_stammdaten_no_context_sees_all(stammdaten_db):
    """Ohne Tenant-Kontext (system_admin): alle Vorlagen aller Orgs sichtbar."""
    db, org_a_id, org_b_id = stammdaten_db
    set_tenant_context(db, None)

    assert db.query(TaskSuggestion).count() == 2
    assert db.query(MessageSuggestion).count() == 2
    assert db.query(LageHint).count() == 2
    assert db.query(DefaultMessage).count() == 2


# ---------------------------------------------------------------------------
# PR 5: AIPromptVersion-Isolation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ai_prompt_db():
    """Eigene In-Memory-DB für AIPromptVersion-Isolationstests."""
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    org_a = FireDept(slug="ai-org-a", name="AI Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="ai-org-b", name="AI Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    # Beide Orgs haben den gleichen prompt_key "suggest" – mit verschiedenem Inhalt
    db.add_all([
        AIPromptVersion(
            org_id=org_a.id, prompt_key="suggest", version=1,
            variable_part="Org A Prompt", created_by_user_id=None,
        ),
        AIPromptVersion(
            org_id=org_a.id, prompt_key="report", version=1,
            variable_part="Org A Report", created_by_user_id=None,
        ),
        AIPromptVersion(
            org_id=org_b.id, prompt_key="suggest", version=1,
            variable_part="Org B Prompt", created_by_user_id=None,
        ),
    ])
    db.commit()

    yield db, org_a.id, org_b.id

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_ai_prompt_isolation_org_a(ai_prompt_db):
    """Mit Org-A-Kontext: nur Org-A-AIPromptVersions sichtbar."""
    db, org_a_id, org_b_id = ai_prompt_db
    set_tenant_context(db, org_a_id)
    db.expire_all()

    versions = db.query(AIPromptVersion).all()

    assert len(versions) == 2
    assert all(v.org_id == org_a_id for v in versions)
    keys = {v.prompt_key for v in versions}
    assert keys == {"suggest", "report"}


def test_ai_prompt_isolation_org_b(ai_prompt_db):
    """Mit Org-B-Kontext: nur Org-B-AIPromptVersions sichtbar."""
    db, org_a_id, org_b_id = ai_prompt_db
    set_tenant_context(db, org_b_id)
    db.expire_all()

    versions = db.query(AIPromptVersion).all()

    assert len(versions) == 1
    assert versions[0].org_id == org_b_id
    assert versions[0].prompt_key == "suggest"
    assert versions[0].variable_part == "Org B Prompt"


def test_ai_prompt_same_key_different_orgs(ai_prompt_db):
    """Akzeptanz PR 5: Zwei Orgs können gleichen prompt_key 'suggest' mit unterschiedlichem Inhalt führen."""
    db, org_a_id, org_b_id = ai_prompt_db
    set_tenant_context(db, None)

    all_suggest = db.query(AIPromptVersion).filter(AIPromptVersion.prompt_key == "suggest").all()

    assert len(all_suggest) == 2
    parts = {v.variable_part for v in all_suggest}
    assert "Org A Prompt" in parts
    assert "Org B Prompt" in parts


def test_ai_prompt_no_cross_tenant_leak(ai_prompt_db):
    """Org A darf Org-B-Prompts nicht sehen."""
    db, org_a_id, org_b_id = ai_prompt_db
    set_tenant_context(db, org_a_id)
    db.expire_all()

    result = db.query(AIPromptVersion).filter(
        AIPromptVersion.variable_part == "Org B Prompt"
    ).first()

    assert result is None, "Org-A darf keinen Org-B-Prompt sehen!"


def test_ai_prompt_no_context_sees_all(ai_prompt_db):
    """Ohne Tenant-Kontext: alle Prompt-Versionen aller Orgs sichtbar."""
    db, org_a_id, org_b_id = ai_prompt_db
    set_tenant_context(db, None)

    assert db.query(AIPromptVersion).count() == 3
