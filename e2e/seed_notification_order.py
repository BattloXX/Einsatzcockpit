"""Idempotently seed live-MariaDB notification ordering fixtures."""
from app.core.crypto import encrypt_secret
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import GATEWAY_STATUS_ONLINE, AlarmIngest, Gateway
from app.models.incident import Incident
from app.models.master import FireDept, Member, OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt
from app.models.org_sms import OrgSmsConfig
from app.models.sms import SmsEinsatzinfoRecipient
from app.models.teams_bot import TeamsAlarmConfig
from app.models.user import ApiKey, User

API_KEY = "e2e-notification-api-key"
GATEWAY_TOKEN = "e2e-notification-gateway-token"

db = SessionLocal()
set_tenant_context(db, None)
try:
    user = db.query(User).filter(User.username == "e2e-admin").one()
    if user.org_id is None:
        org = FireDept(slug="notification-e2e", name="Notification E2E Org",
                       city="Teststadt", is_home_org=True)
        db.add(org); db.flush(); user.org_id = org.id
        db.add(OrgSettings(org_id=org.id))
        db.commit()
    org_id = user.org_id
    settings = db.query(OrgSettings).filter_by(org_id=org_id).one()
    settings.einsatzinfo_sms_enabled = True
    settings.objekt_module_enabled = True
    flag = db.get(SystemSettings, "objekt_module_enabled") or SystemSettings(key="objekt_module_enabled")
    db.add(flag); flag.value = "true"
    # Keep repeated local runs independent without touching non-E2E records.
    ids = [row[0] for row in db.query(Incident.id).filter(Incident.report_text.like("%E2E-%"))]
    if ids:
        db.query(AlarmIngest).filter(AlarmIngest.einsatz_id.in_(ids)).delete(synchronize_session=False)
        db.query(Incident).filter(Incident.id.in_(ids)).delete(synchronize_session=False)
    member = db.query(Member).filter_by(org_id=org_id, lastname="Notification-E2E").first()
    if not member:
        member = Member(org_id=org_id, firstname="Probe", lastname="Notification-E2E",
                        phone="+436641234567", active=True)
        db.add(member); db.flush()
        db.add(SmsEinsatzinfoRecipient(org_id=org_id, member_id=member.id))
    sms = db.query(OrgSmsConfig).filter_by(org_id=org_id).first() or OrgSmsConfig(org_id=org_id)
    db.add(sms)
    sms.primary_provider = "eus"; sms.fallback_provider = None; sms.eus_enabled = True
    sms.eus_base_url = "http://fake-sink:8089"; sms.eus_auth_mode = "basic"
    sms.eus_client_id = "e2e"; sms.eus_client_secret_enc = encrypt_secret("e2e")
    teams = db.query(TeamsAlarmConfig).filter_by(org_id=org_id).first() or TeamsAlarmConfig(org_id=org_id)
    db.add(teams); teams.enabled = True; teams.bot_enabled = False
    teams.webhook_url_alarm = "https://fake-sink:8443/teams"
    if not db.query(Objekt).filter_by(org_id=org_id, name="Notification E2E Object").first():
        db.add(Objekt(org_id=org_id, nummer=990001, name="Notification E2E Object",
                      strasse="Orderingweg", hausnummer="7", ort="Teststadt",
                      status=OBJEKT_STATUS_FREIGEGEBEN))
    key = db.query(ApiKey).filter_by(key_hash=hash_api_key(API_KEY)).first()
    if not key: db.add(ApiKey(key_hash=hash_api_key(API_KEY), label="notification-e2e",
                              org_id=org_id, created_by_user_id=user.id))
    gw = db.query(Gateway).filter_by(name="Notification E2E Gateway", org_id=org_id).first()
    if not gw:
        gw = Gateway(name="Notification E2E Gateway", org_id=org_id)
        db.add(gw)
    gw.device_token_hash = hash_api_key(GATEWAY_TOKEN); gw.status = GATEWAY_STATUS_ONLINE
    db.commit()
    print({"org_id": org_id, "api_key": API_KEY, "gateway_token": GATEWAY_TOKEN})
finally:
    db.close()
