"""Cross-Org-Regressionsnetz für öffentliche Token-Routen (Audit A7 / SEC-11).

Anonyme Endpunkte laufen OHNE Tenant-Filter (dependencies.py, SEC-11) und
müssen selbst über ihren Token scopen. Diese Tests halten das fest: Ein
gültiger Token der Org A darf niemals Daten der Org B preisgeben.

Neue öffentliche Routen (Token/QR/PIN/Signatur) bitte hier mit einem
Cross-Org-Fall ergänzen (siehe CLAUDE.md, Abschnitt Tenant-Scoping).
"""

from datetime import UTC, datetime

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, Member, OrgSettings, VehicleMaster
from app.models.objekt import AlarmInfoscreenToken, Objekt, ObjektEinsatz, ObjektKartenObjekt
from app.models.teams_bot import AlarmToken
from app.models.stats import StatistikDashboardToken
from app.models.wasserstelle import Wasserstelle
from app.models.mailing import (
    MailingCampaign,
    MailingConfig,
    MailingQueueItem,
    MailingRecipientList,
    MailingRecipientListEntry,
    MailingSuppressionEntry,
    MailingTemplate,
)
from app.models.user import ApiKey
from app.models.api_message import ApiMessage, ApiMessageRecipient
from app.models.sms import SmsGroup
from app.core.crypto import encrypt_secret
from app.core.security import sign_mailing_track_token, sign_mailing_webhook_org

ORG_A = 1  # FF Wolfurt (seeded)


def test_fahrtenbuch_autocomplete_token_zeigt_keine_fremden_mitglieder(client):
    org_b_id = _setup_zwei_orgs()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add_all(
            [
                Member(org_id=ORG_A, firstname="Eigen", lastname="Suchname", active=True),
                Member(org_id=org_b_id, firstname="Geheim", lastname="Suchname", active=True),
                Member(org_id=None, firstname="Eigen", lastname="Suchname", active=True),
            ]
        )
        db.commit()
    finally:
        db.close()
    response = client.get(f"/fahrtenbuch/hx/maschinist?t={FAB_TOKEN_A}&q=Suchname")
    assert response.status_code == 200
    assert "Suchname Eigen" in response.text
    assert response.text.count("class='autocomplete-item'") == 1
    assert "Suchname Geheim" not in response.text


def test_lage_qr_public_flow_isoliert_org_pin_und_open_site(client):
    """Direkter POST, fremdes PIN-Cookie und fremde Site öffnen keine Lage."""
    from urllib.parse import parse_qs, urlparse

    from app.core.security import hash_pin, sign_lage_pin_access_token
    from app.models.major_incident import IncidentSite, MajorIncident
    from app.services.incident_qr_service import lage_qr_login_url

    org_b_id = _setup_zwei_orgs()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage_a = MajorIncident(org_id=ORG_A, name="QR Isolation A", access_pin_hash=hash_pin("1111"))
        lage_b = MajorIncident(org_id=org_b_id, name="QR Isolation B", access_pin_hash=hash_pin("2222"))
        db.add_all([lage_a, lage_b])
        db.flush()
        site_b = IncidentSite(
            major_incident_id=lage_b.id,
            org_id=org_b_id,
            bezeichnung="Geheime Site B",
        )
        db.add(site_b)
        db.commit()
        url_a = lage_qr_login_url(db, lage_a)
        token_a = parse_qs(urlparse(url_a).query)["token"][0]
        lage_a_id, lage_b_id, site_b_id = lage_a.id, lage_b.id, site_b.id
    finally:
        db.close()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    direkt = client.post(
        f"/lage/{lage_a_id}/qr-login?token={token_a}",
        data={"display_name": "Direkt", "_csrf": csrf},
        follow_redirects=False,
    )
    assert direkt.status_code == 403

    client.cookies.set("board_pin_lage", sign_lage_pin_access_token(lage_b_id))
    fremdes_cookie = client.post(
        f"/lage/{lage_a_id}/qr-login?token={token_a}",
        data={"display_name": "Fremd", "_csrf": csrf},
        follow_redirects=False,
    )
    assert fremdes_cookie.status_code == 403

    einstieg = client.get(
        f"/lage/{lage_a_id}/qr-login?token={token_a}&open_site={site_b_id}",
        follow_redirects=False,
    )
    assert einstieg.status_code == 302
    assert "open_site" not in einstieg.headers["location"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(MajorIncident, lage_a_id).status = "closed"
        db.get(MajorIncident, lage_b_id).status = "closed"
        db.commit()
    finally:
        db.close()


def test_dienst_monitor_token_zeigt_keinen_status_fremder_org(client):
    """Monitoring-Token A darf selbst bei identischem Dienst-Key nie Zeilen von B lesen."""
    from app.models.dienst_monitor import DienstMonitorToken, DienstStatus

    org_b_id = _setup_zwei_orgs()
    raw = "dienst-monitor-isolation-a"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        token = DienstMonitorToken(
            token_hash=hash_api_key(raw),
            label="Isolation A",
            org_id=ORG_A,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
        fremd = DienstStatus(
            org_id=org_b_id,
            key="print_gateway",
            state="down",
            last_error="GEHEIM-ORG-B",
            outage_notified_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add_all([token, fremd])
        db.commit()
    finally:
        db.close()

    response = client.get("/health/dienste", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code in {200, 503}
    assert "GEHEIM-ORG-B" not in response.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(DienstMonitorToken).filter(DienstMonitorToken.token_hash == hash_api_key(raw)).delete()
        db.query(DienstStatus).filter(DienstStatus.org_id == org_b_id, DienstStatus.key == "print_gateway").delete()
        db.commit()
    finally:
        db.close()


def test_mailing_tracking_token_cannot_mutate_other_org(client):
    org_b_id = _setup_zwei_orgs()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        tpl = MailingTemplate(org_id=org_b_id, name="Isolation Tracking", subject="x", body_html="x")
        lst = MailingRecipientList(org_id=org_b_id, name="Isolation Tracking", kind="static")
        db.add_all([tpl, lst])
        db.flush()
        campaign = MailingCampaign(org_id=org_b_id, template_id=tpl.id, recipient_list_id=lst.id, status="sent")
        db.add(campaign)
        db.flush()
        item = MailingQueueItem(org_id=org_b_id, campaign_id=campaign.id, email="secret-b@example.at", status="sent")
        db.add(item)
        db.commit()
        iid = item.id
        forged = sign_mailing_track_token(iid, ORG_A)
        assert client.get(f"/mailing/t/{forged}.png").status_code == 200
        assert client.get(f"/mailing/c/{forged}?u=https%3A%2F%2Fexample.at", follow_redirects=False).status_code == 302
        assert client.post(f"/mailing/u/{forged}").status_code == 200
        db.expire_all()
        row = (
            db.query(MailingQueueItem)
            .execution_options(include_all_tenants=True)
            .filter(MailingQueueItem.id == iid)
            .one()
        )
        assert row.open_count == 0 and row.click_count == 0
        assert (
            db.query(MailingSuppressionEntry)
            .execution_options(include_all_tenants=True)
            .filter_by(org_id=org_b_id, email="secret-b@example.at")
            .count()
            == 0
        )
    finally:
        db.close()


def _svix(secret: bytes, body: bytes, event_id: str):
    import base64, hashlib, hmac, time

    stamp = str(int(time.time()))
    signature = base64.b64encode(
        hmac.new(secret, f"{event_id}.{stamp}.{body.decode()}".encode(), hashlib.sha256).digest()
    ).decode()
    return {"svix-id": event_id, "svix-timestamp": stamp, "svix-signature": "v1," + signature}


def test_mailing_webhook_secret_and_org_token_are_both_required(client, monkeypatch):
    import base64, json

    org_b_id = _setup_zwei_orgs()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        secret_a = b"secret-a"
        secret_b = b"secret-b"
        for oid, secret in ((ORG_A, secret_a), (org_b_id, secret_b)):
            cfg = db.query(MailingConfig).filter_by(org_id=oid).first() or MailingConfig(org_id=oid)
            cfg.resend_webhook_secret_enc = encrypt_secret("whsec_" + base64.b64encode(secret).decode())
            db.add(cfg)
        tpl = MailingTemplate(org_id=ORG_A, name="Webhook Isolation", subject="x", body_html="x")
        lst = MailingRecipientList(org_id=ORG_A, name="Webhook Isolation", kind="static")
        db.add_all([tpl, lst])
        db.flush()
        campaign = MailingCampaign(org_id=ORG_A, template_id=tpl.id, recipient_list_id=lst.id, status="sent")
        db.add(campaign)
        db.flush()
        item = MailingQueueItem(
            org_id=ORG_A, campaign_id=campaign.id, email="a@example.at", status="sent", resend_message_id="shared-mail"
        )
        db.add(item)
        db.commit()
        from app.services.mailing_service import mailing_webhook_secret

        assert mailing_webhook_secret(db.query(MailingConfig).filter_by(org_id=org_b_id).one()).endswith(
            base64.b64encode(secret_b).decode()
        )
        from app.services.mailing_webhook_service import verify_resend_webhook_signature as real_verify

        seen = []

        def checked(secret, *args, **kwargs):
            seen.append(secret)
            return real_verify(secret, *args, **kwargs)

        monkeypatch.setattr("app.routers.mailing_webhook.verify_resend_webhook_signature", checked)
        body = json.dumps(
            {"type": "email.delivered", "data": {"email_id": "shared-mail"}}, separators=(",", ":")
        ).encode()
        cross = client.post(
            f"/mailing/webhook/resend/{sign_mailing_webhook_org(org_b_id)}",
            content=body,
            headers=_svix(secret_a, body, "evt-cross"),
        )
        assert cross.status_code == 401, (cross.text, seen)
        assert seen[-1].endswith(base64.b64encode(secret_b).decode())
        assert (
            client.post(
                f"/mailing/webhook/resend/{sign_mailing_webhook_org(ORG_A)}",
                content=body,
                headers=_svix(secret_a, body, "evt-own"),
            ).status_code
            == 200
        )
        db.expire_all()
        assert db.get(MailingQueueItem, item.id).status == "delivered"
    finally:
        db.close()


def test_mailing_api_import_cannot_target_foreign_list(client):
    org_b_id = _setup_zwei_orgs()
    raw = "mailing-import-org-a"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        key = db.query(ApiKey).filter_by(key_hash=hash_api_key(raw)).first() or ApiKey(
            key_hash=hash_api_key(raw), label="Mailing isolation", org_id=ORG_A
        )
        target = MailingRecipientList(org_id=org_b_id, name="Foreign API list", kind="static")
        db.add_all([key, target])
        db.commit()
        target_id = target.id
        response = client.post(
            f"/api/v1/mailing/recipient-lists/{target_id}/import",
            headers={"X-API-Key": raw},
            json={"Key": "cross-org", "recipients": [{"email": "forbidden@example.at"}]},
        )
        assert response.status_code == 404
        assert (
            db.query(MailingRecipientListEntry)
            .execution_options(include_all_tenants=True)
            .filter_by(list_id=target_id)
            .count()
            == 0
        )
    finally:
        db.close()


def test_nachrichten_api_isoliert_gruppen_listen_und_jobs(client, monkeypatch):
    org_b_id = _setup_zwei_orgs()
    raw = f"messaging-isolation-{datetime.now(UTC).timestamp()}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        key = ApiKey(
            key_hash=hash_api_key(raw),
            label="Messaging isolation",
            org_id=ORG_A,
            scopes="sms:send,mail:send",
        )
        group_b = SmsGroup(org_id=org_b_id, name=f"Foreign SMS {raw}")
        list_b = MailingRecipientList(org_id=org_b_id, name=f"Foreign Mail {raw}", kind="static")
        db.add_all([key, group_b, list_b])
        db.flush()
        message_b = ApiMessage(
            org_id=org_b_id,
            api_key_id=key.id,
            external_key=f"foreign-job-{raw}",
            kanal="sms",
            body_text="geheim",
            recipient_count=1,
        )
        db.add(message_b)
        db.flush()
        db.add(ApiMessageRecipient(org_id=org_b_id, message_id=message_b.id, ziel="+436649999999"))
        db.commit()
        group_id, list_id, message_id = group_b.id, list_b.id, message_b.id
    finally:
        db.close()
    monkeypatch.setattr("app.routers.api_messaging.sms_available", lambda *_: True)
    monkeypatch.setattr("app.services.mail_service.mail_available", lambda *_: True)
    headers = {"X-API-Key": raw}
    sms = client.post(
        "/api/v1/sms",
        headers=headers,
        json={
            "Key": f"cross-sms-{raw}",
            "text": "x",
            "empfaenger": {"gruppen_ids": [group_id]},
        },
    )
    mail = client.post(
        "/api/v1/mail",
        headers=headers,
        json={
            "Key": f"cross-mail-{raw}",
            "betreff": "x",
            "text": "x",
            "empfaenger": {"listen_ids": [list_id]},
        },
    )
    status = client.get(f"/api/v1/nachricht/{message_id}", headers=headers)
    assert (sms.status_code, mail.status_code, status.status_code) == (404, 404, 404)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert (
            db.query(ApiMessageRecipient)
            .execution_options(include_all_tenants=True)
            .join(ApiMessage)
            .filter(ApiMessage.external_key.in_([f"cross-sms-{raw}", f"cross-mail-{raw}"]))
            .count()
            == 0
        )
    finally:
        db.close()


RAW_TOKEN_A = "iso-test-infoscreen-token-org-a"
RAW_TOKEN_B = "iso-test-infoscreen-token-org-b"
FAB_TOKEN_A = "iso-test-fahrtenbuch-a"
FAB_TOKEN_B = "iso-test-fahrtenbuch-b"
FAHRZEUG_B_CODE = "ISO-GEHEIM-TLF-B"  # Formular rendert fz.code, nicht fz.name
STATS_TOKEN_A = "iso-test-statistik-a"
STATS_TOKEN_B = "iso-test-statistik-b"


def _setup_zwei_orgs() -> int:
    """Org B mit aktivem Einsatz, Infoscreen-Tokens + Fahrtenbuch-Tokens für A und B.

    Idempotent (Session-DB wird zwischen Tests nicht zurückgesetzt).
    Returns org_b_id.
    """
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_b = db.query(FireDept).filter(FireDept.slug == "iso-test-org-b").first()
        if org_b is None:
            org_b = FireDept(slug="iso-test-org-b", name="Isolationstest Org B")
            db.add(org_b)
            db.flush()

            db.add(
                Incident(
                    primary_org_id=org_b.id,
                    alarm_type_code="B3",
                    status="active",
                    address_street="Geheime Adresse der Org B",
                    address_no="42",
                    started_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            for org_id, raw in ((ORG_A, RAW_TOKEN_A), (org_b.id, RAW_TOKEN_B)):
                db.add(
                    AlarmInfoscreenToken(
                        org_id=org_id,
                        token_hash=hash_api_key(raw),
                        name=f"Isolationstest {org_id}",
                        aktiv=True,
                    )
                )
            for org_id, fab in ((ORG_A, FAB_TOKEN_A), (org_b.id, FAB_TOKEN_B)):
                org_s = (
                    db.query(OrgSettings)
                    .filter(OrgSettings.org_id == org_id)
                    .execution_options(include_all_tenants=True)
                    .first()
                )
                if org_s is None:
                    org_s = OrgSettings(org_id=org_id)
                    db.add(org_s)
                org_s.fahrtenbuch_token = fab
                org_s.fahrtenbuch_modul_aktiv = True
            db.add(
                VehicleMaster(
                    dept_id=org_b.id,
                    code=FAHRZEUG_B_CODE,
                    name="Isolationstest TLF B",
                    active=True,
                    deleted=False,
                    is_adhoc=False,
                    is_external=False,
                )
            )
            db.commit()
        return org_b.id
    finally:
        db.close()


# ── Alarm-Infoscreen (/infoscreen/alarm/{token}) ──────────────────────────────


def test_infoscreen_token_sieht_nur_eigene_org(client):
    _setup_zwei_orgs()

    # Der aktive Einsatz der Org B darf über den A-Token NICHT sichtbar werden.
    # (Kein Assert auf modus=="idle": andere Tests der Suite hinterlassen
    # aktive Einsätze in der seeded Org A — entscheidend ist die Isolation.)
    r_a = client.get(f"/infoscreen/alarm/{RAW_TOKEN_A}/daten")
    assert r_a.status_code == 200
    assert r_a.json()["org_name"] != "Isolationstest Org B"
    assert "Geheime Adresse der Org B" not in r_a.text

    # Org B sieht ihren eigenen Einsatz (modus "alarm").
    r_b = client.get(f"/infoscreen/alarm/{RAW_TOKEN_B}/daten")
    assert r_b.status_code == 200
    assert r_b.json()["modus"] == "alarm"
    assert "Geheime Adresse der Org B" in r_b.text


def test_alarm_objekt_token_sieht_keine_kartenobjekte_fremder_org(client):
    import hashlib

    org_b_id = _setup_zwei_orgs()
    raw = "iso-test-public-alarm-objekt-a"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident_a = Incident(
            primary_org_id=ORG_A,
            alarm_type_code="B3",
            status="active",
            started_at=datetime.now(UTC).replace(tzinfo=None),
        )
        objekt_b = Objekt(org_id=org_b_id, nummer=9876, name="Geheimobjekt B")
        db.add_all([incident_a, objekt_b])
        db.flush()
        db.add_all(
            [
                AlarmToken(
                    incident_id=incident_a.id,
                    token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                ),
                ObjektEinsatz(
                    org_id=org_b_id,
                    incident_id=incident_a.id,
                    objekt_id=objekt_b.id,
                    quelle="manuell",
                    status="bestaetigt",
                ),
                ObjektKartenObjekt(
                    org_id=org_b_id,
                    objekt_id=objekt_b.id,
                    typ="bmz",
                    lat=48.123,
                    lng=10.456,
                    label="GEHEIM-ORG-B",
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    response = client.get(f"/alarm/{raw}/objekt.json")
    assert response.status_code == 200
    assert response.json() == {"karten_objekte": [], "symbole": []}
    assert "GEHEIM-ORG-B" not in response.text


def test_infoscreen_hydranten_sind_tenant_isoliert(client, monkeypatch):
    from app.config import settings

    org_b_id = _setup_zwei_orgs()
    monkeypatch.setattr(settings, "HYDRANT_ENABLED", False)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        geheim = "ISO-GEHEIME-WASSERSTELLE-B"
        incident_a = Incident(
            primary_org_id=ORG_A,
            alarm_type_code="B3",
            status="active",
            address_street="Isolation Wasser A",
            started_at=datetime.now(UTC).replace(tzinfo=None),
            lat=47.465,
            lng=9.750,
        )
        db.add(incident_a)
        if not (
            db.query(Wasserstelle)
            .execution_options(include_all_tenants=True)
            .filter(Wasserstelle.org_id == org_b_id, Wasserstelle.bezeichnung == geheim)
            .first()
        ):
            db.add(
                Wasserstelle(
                    org_id=org_b_id,
                    bezeichnung=geheim,
                    typ="loeschteich",
                    lat=47.4651,
                    lng=9.7501,
                    aktiv=True,
                )
            )
        db.commit()
    finally:
        db.close()

    r = client.get(f"/infoscreen/alarm/{RAW_TOKEN_A}/hydranten.json")
    assert r.status_code == 200
    assert geheim not in r.text
    assert all(h["quelle"] != "stammdaten" or h["ref"] != geheim for h in r.json()["hydranten"])


def test_infoscreen_unbekannter_token_abgelehnt(client):
    # 401 wird vom globalen Exception-Handler für Browser in einen
    # Login-Redirect übersetzt — beides gilt als "abgelehnt".
    r = client.get("/infoscreen/alarm/voellig-unbekannter-token/daten", follow_redirects=False)
    assert r.status_code in (302, 401)
    assert "Geheime Adresse" not in r.text


def test_infoscreen_gesperrter_token_401(client):
    _setup_zwei_orgs()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = (
            db.query(AlarmInfoscreenToken)
            .filter(AlarmInfoscreenToken.token_hash == hash_api_key(RAW_TOKEN_A))
            .execution_options(include_all_tenants=True)
            .one()
        )
        row.aktiv = False
        db.commit()
        r = client.get(f"/infoscreen/alarm/{RAW_TOKEN_A}/daten", follow_redirects=False)
        assert r.status_code in (302, 401)
    finally:
        row.aktiv = True
        db.commit()
        db.close()


# ── Statistik-Infoscreen (/infoscreen/statistik/{token}) ─────────────────────


def _setup_statistik_tokens(org_b_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        if (
            not db.query(Incident)
            .filter(
                Incident.primary_org_id == org_b_id,
                Incident.address_street == "Geheime Adresse der Org B",
            )
            .first()
        ):
            db.add(
                Incident(
                    primary_org_id=org_b_id,
                    alarm_type_code="B3",
                    status="active",
                    address_street="Geheime Adresse der Org B",
                    address_no="42",
                    started_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
        for org_id, raw in ((ORG_A, STATS_TOKEN_A), (org_b_id, STATS_TOKEN_B)):
            settings_row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
            if settings_row is None:
                settings_row = OrgSettings(org_id=org_id)
                db.add(settings_row)
            settings_row.statistik_infoscreen_enabled = True
            if (
                not db.query(StatistikDashboardToken)
                .filter(StatistikDashboardToken.token_hash == hash_api_key(raw))
                .first()
            ):
                db.add(StatistikDashboardToken(org_id=org_id, token_hash=hash_api_key(raw), label="Test"))
        db.commit()
    finally:
        db.close()


def test_statistik_infoscreen_token_sieht_keine_fremde_org(client):
    org_b_id = _setup_zwei_orgs()
    _setup_statistik_tokens(org_b_id)
    response_a = client.get(f"/infoscreen/statistik/{STATS_TOKEN_A}")
    assert response_a.status_code == 200
    assert "Geheime Adresse der Org B" not in response_a.text
    response_b = client.get(f"/infoscreen/statistik/{STATS_TOKEN_B}")
    assert response_b.status_code == 200
    assert "Geheime Adresse der Org B" in response_b.text


def test_statistik_infoscreen_token_statuscodes(client):
    org_b_id = _setup_zwei_orgs()
    _setup_statistik_tokens(org_b_id)
    unknown = client.get("/infoscreen/statistik/unbekannt", follow_redirects=False)
    assert unknown.status_code in (302, 401)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        settings_row = db.query(OrgSettings).filter(OrgSettings.org_id == ORG_A).one()
        settings_row.statistik_infoscreen_enabled = False
        db.commit()
        disabled = client.get(f"/infoscreen/statistik/{STATS_TOKEN_A}", follow_redirects=False)
        assert disabled.status_code == 403
    finally:
        settings_row.statistik_infoscreen_enabled = True
        db.commit()
        db.close()


# ── Fahrtenbuch-Erfassung ohne Login (/f/{token}) ─────────────────────────────


def test_fahrtenbuch_token_zeigt_nur_eigene_fahrzeuge(client):
    _setup_zwei_orgs()

    r_a = client.get(f"/f/{FAB_TOKEN_A}")
    assert r_a.status_code == 200
    assert FAHRZEUG_B_CODE not in r_a.text  # Fahrzeug der Org B unsichtbar für Org A

    r_b = client.get(f"/f/{FAB_TOKEN_B}")
    assert r_b.status_code == 200
    assert FAHRZEUG_B_CODE in r_b.text


def test_fahrtenbuch_unbekannter_token_404(client):
    r = client.get("/f/voellig-unbekannter-token")
    assert r.status_code == 404


# ── Förderstrecken-Maschinisten-Zettel ohne Login (/m/foerderstrecke/{token}) ──

FS_TOKEN_A = "iso-fs-token-org-a"
FS_TOKEN_B = "iso-fs-token-org-b"
FS_NAME_A = "ISO-Foerderstrecke-A"
FS_NAME_B = "ISO-GEHEIM-Foerderstrecke-B"


def _setup_fs_tokens() -> int:
    """Je eine Strecke + Maschinisten-Token in Org A und Org B (idempotent)."""
    import hashlib

    from app.models.foerderstrecke import (
        FoerderMaschinistToken,
        FoerderStation,
        Foerderstrecke,
    )

    org_b_id = _setup_zwei_orgs()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:

        def _ensure(org_id, name, token):
            vorhanden = (
                db.query(FoerderMaschinistToken)
                .filter(FoerderMaschinistToken.token_hash == hashlib.sha256(token.encode()).hexdigest())
                .first()
            )
            if vorhanden:
                return
            s = Foerderstrecke(org_id=org_id, name=name, ansaug_json='{"seehoehe_m":430,"geodaetische_saughoehe_m":2}')
            db.add(s)
            db.flush()
            db.add(FoerderStation(org_id=org_id, strecke_id=s.id, sort=0, typ="quellpumpe", lat=47.4, lng=9.7))
            db.add(
                FoerderMaschinistToken(
                    org_id=org_id, strecke_id=s.id, token_hash=hashlib.sha256(token.encode()).hexdigest()
                )
            )

        _ensure(ORG_A, FS_NAME_A, FS_TOKEN_A)
        _ensure(org_b_id, FS_NAME_B, FS_TOKEN_B)
        db.commit()
        return org_b_id
    finally:
        db.close()


def test_foerderstrecke_token_zeigt_nur_eigene_org(client):
    _setup_fs_tokens()

    r_a = client.get(f"/m/foerderstrecke/{FS_TOKEN_A}")
    assert r_a.status_code == 200
    assert FS_NAME_A in r_a.text
    assert FS_NAME_B not in r_a.text  # Strecke der Org B unsichtbar für Token A

    r_b = client.get(f"/m/foerderstrecke/{FS_TOKEN_B}")
    assert r_b.status_code == 200
    assert FS_NAME_B in r_b.text
    assert FS_NAME_A not in r_b.text


def test_foerderstrecke_unbekannter_token_404(client):
    assert client.get("/m/foerderstrecke/voellig-unbekannter-token").status_code == 404


# ── Öffentlicher Probenplan und ICS: identische SEC-11-Beweiskette ──

def test_probenplan_und_ics_token_isolieren_org_und_fremde_verknuepfungen(client):
    import hashlib
    import secrets
    from app.models.probenplanung import Probeart, ProbePublicToken
    from app.models.teilnahme import Termin
    from tests.test_probenplanung_checkliste import _flags

    org_b = _setup_zwei_orgs()
    _flags()
    tokens = {}
    ids = {}
    with SessionLocal() as db:
        set_tenant_context(db, None)
        for org_id, name in [(ORG_A, "Public-Org-A"), (org_b, "GEHEIM-Org-B")]:
            settings = db.query(OrgSettings).filter_by(org_id=org_id).first()
            if settings is None:
                settings = OrgSettings(org_id=org_id)
                db.add(settings)
            settings.probenplanung_modul_aktiv = True
            settings.probenplanung_public_aktiv = True
            art = Probeart(org_id=org_id, name=name, kurz=name[:10], farbe="#123456")
            db.add(art)
            db.flush()
            termin = Termin(org_id=org_id, typ="uebung", titel=name, probeart_id=art.id,
                            beginn=datetime(2026, 7, 10, 18), public_sichtbar=True)
            db.add(termin)
            db.flush()
            ids[org_id] = (termin.id, art.id)
            plain = secrets.token_urlsafe(32)
            tokens[org_id] = plain
            db.add(ProbePublicToken(org_id=org_id, art="plan",
                                    token_hash=hashlib.sha256(plain.encode()).hexdigest()))
        db.commit()
    client.cookies.clear()
    for suffix in ("", ".ics"):
        a = client.get(f"/p/probenplan/{tokens[ORG_A]}{suffix}")
        b = client.get(f"/p/probenplan/{tokens[org_b]}{suffix}")
        assert a.status_code == b.status_code == 200
        assert "Public-Org-A" in a.text and "GEHEIM-Org-B" not in a.text
        assert "GEHEIM-Org-B" in b.text and "Public-Org-A" not in b.text
    # Manipulierte FKs dürfen auch bei gültigem Token keine fremden Details öffnen.
    with SessionLocal() as db:
        set_tenant_context(db, None)
        token_a = db.query(ProbePublicToken).filter_by(
            token_hash=hashlib.sha256(tokens[ORG_A].encode()).hexdigest()).one()
        token_a.termin_id = ids[org_b][0]
        db.get(Termin, ids[ORG_A][0]).probeart_id = ids[org_b][1]
        db.commit()
    for suffix in ("", ".ics"):
        r = client.get(f"/p/probenplan/{tokens[ORG_A]}{suffix}")
        assert r.status_code == 200 and "GEHEIM-Org-B" not in r.text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.query(ProbePublicToken).filter_by(
            token_hash=hashlib.sha256(tokens[ORG_A].encode()).hexdigest()).one().termin_id = None
        db.commit()
    for suffix in ("", ".ics"):
        r = client.get(f"/p/probenplan/{tokens[ORG_A]}{suffix}")
        assert "Public-Org-A" in r.text and "GEHEIM-Org-B" not in r.text
