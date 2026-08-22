from app.models.mailing import MailingRecipientListEntry
from app.services.mailing_service import import_recipients
from tests.mailing_phase2_helpers import campaign, db_session

def test_api_import_deduplicates_like_csv_import():
    db=db_session(); _,lst=campaign(db)
    result=import_recipients(db,lst,[{"email":"A@example.at","display_name":"A"},{"email":"a@example.at","display_name":"Duplikat"}])
    assert result=={"added":1,"skipped":1}
    assert db.query(MailingRecipientListEntry).filter_by(list_id=lst.id).count()==1
    db.close()
