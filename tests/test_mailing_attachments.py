from pathlib import Path
import pytest
from fastapi import HTTPException
from app.services.mailing_attachments import delete_attachment, store_attachment
from tests.mailing_phase2_helpers import campaign, db_session
def test_attachment_magic_detection_storage_and_delete(monkeypatch,tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings,"MEDIA_STORAGE_DIR",str(tmp_path)); monkeypatch.setattr("app.services.mailing_attachments._detect_mime",lambda data:"application/pdf")
    db=db_session(); c,_=campaign(db); obj=store_attachment(db,c,"unsafe/notice.exe",b"%PDF-1.7\n",1); assert obj.mime_type=="application/pdf" and Path(obj.storage_path).read_bytes().startswith(b"%PDF"); path=Path(obj.storage_path); delete_attachment(db,obj); assert not path.exists(); db.rollback(); db.close()
def test_attachment_rejects_non_draft(monkeypatch):
    monkeypatch.setattr("app.services.mailing_attachments._detect_mime",lambda data:"application/pdf"); db=db_session(); c,_=campaign(db,status="queued")
    with pytest.raises(HTTPException): store_attachment(db,c,"x.pdf",b"%PDF",1)
    db.rollback(); db.close()
