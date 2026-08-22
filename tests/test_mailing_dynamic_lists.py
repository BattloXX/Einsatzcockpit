import json
from uuid import uuid4
from app.models.master import Member,MemberTag,MemberTagAssignment
from app.services.mailing_recipients import resolve_dynamic_list
from tests.mailing_phase2_helpers import db_session
def test_dynamic_list_ands_active_and_tag_filters():
    db=db_session(); tag=MemberTag(org_id=1,name="tag"+uuid4().hex); good=Member(org_id=1,firstname="Good",lastname="One",email="GOOD@EXAMPLE.AT",active=True); wrong=Member(org_id=1,firstname="Wrong",lastname="Two",email="wrong@example.at",active=False); db.add_all([tag,good,wrong]); db.flush(); db.add_all([MemberTagAssignment(member_id=good.id,tag_id=tag.id),MemberTagAssignment(member_id=wrong.id,tag_id=tag.id)]); db.flush(); rows=resolve_dynamic_list(db,1,json.dumps({"active":True,"tag_ids":[tag.id]})); assert [x["email"] for x in rows]==["good@example.at"]; db.rollback(); db.close()
