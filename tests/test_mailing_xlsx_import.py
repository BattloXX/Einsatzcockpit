import io
from uuid import uuid4

from openpyxl import Workbook

from app.models.mailing import MailingRecipientList, MailingRecipientListEntry
from app.models.master import MemberTag
from app.services.mailing_service import import_xlsx
from tests.mailing_phase2_helpers import db_session


def _xlsx(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["E-Mail", "Name", "Gruppe"])
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _list(db):
    item = MailingRecipientList(org_id=1, name="XLSX " + uuid4().hex, kind="static")
    db.add(item)
    db.flush()
    return item


def test_xlsx_repeat_import_upserts_without_duplicates():
    db = db_session()
    item = _list(db)
    first = import_xlsx(db, item, _xlsx([["person@example.at", "Erster Name", "Nord"]]))
    second = import_xlsx(db, item, _xlsx([["PERSON@example.at", "Neuer Name", "Nord, Presse"]]))

    entries = db.query(MailingRecipientListEntry).filter(MailingRecipientListEntry.list_id == item.id).all()
    assert first == {"added": 1, "updated": 0, "skipped": 0}
    assert second == {"added": 0, "updated": 1, "skipped": 0}
    assert len(entries) == 1
    assert entries[0].display_name == "Neuer Name"
    assert {tag.name for tag in entries[0].tags} == {"Nord", "Presse"}
    db.rollback()
    db.close()


def test_xlsx_import_creates_missing_member_tags_case_insensitively():
    db = db_session()
    item = _list(db)
    existing = MemberTag(org_id=1, name="Bestehend")
    db.add(existing)
    db.flush()

    result = import_xlsx(db, item, _xlsx([["person@example.at", "Person", "bestehend, Neue Gruppe"]]))

    assert result == {"added": 1, "updated": 0, "skipped": 0}
    assert db.query(MemberTag).filter(MemberTag.org_id == 1, MemberTag.name == "Neue Gruppe").count() == 1
    entry = db.query(MailingRecipientListEntry).filter(MailingRecipientListEntry.list_id == item.id).one()
    assert {tag.id for tag in entry.tags} == {existing.id, next(tag.id for tag in entry.tags if tag.name == "Neue Gruppe")}
    db.rollback()
    db.close()


def test_xlsx_import_rejects_invalid_email_addresses():
    db = db_session()
    item = _list(db)

    result = import_xlsx(db, item, _xlsx([["ungueltig", "Niemand", "Test"]]))

    assert result == {"added": 0, "updated": 0, "skipped": 1}
    assert db.query(MailingRecipientListEntry).filter(MailingRecipientListEntry.list_id == item.id).count() == 0
    assert db.query(MemberTag).filter(MemberTag.org_id == 1, MemberTag.name == "Test").count() == 0
    db.rollback()
    db.close()
