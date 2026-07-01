"""Regressionstests PR9 (MOB-1, MOB-3): Scripts in base.html sind defer (kein
Render-Blocking mehr), Touch-Targets (.btn, .prio-quick-btn) sind >=44px."""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import Role, User, UserRole


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def test_base_html_scripts_are_deferred(client, setup_db):
    """base.html wird von so gut wie allen eingeloggten Seiten geerbt; hier über
    /fahrtenbuch/neu geprüft (kein GSL-Board-Setup nötig)."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        from app.models.master import FireDept
        org = db.query(FireDept).first()
        user = User(username="mobtestuser", password_hash=hash_password("Test1234!"),
                    display_name="Mob Test", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "readonly").first()
        if role:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()

    r = _login(client, "mobtestuser", "Test1234!")
    assert r.status_code == 302

    r = client.get("/fahrtenbuch/neu")
    assert r.status_code == 200
    text = r.text
    for src in ("htmx.min.js", "sortable.min.js", "app.js", "sortable-glue.js",
                "media-upload.js", "media-viewer.js", "pwa-install.js", "tooltips.js"):
        idx = text.index(src)
        # Das <script ...>-Tag, das src enthaelt, muss defer tragen (MOB-1).
        tag_start = text.rfind("<script", 0, idx)
        tag_end = text.index(">", idx)
        tag = text[tag_start:tag_end]
        assert "defer" in tag, f"<script src=...{src}...> ist nicht defer (Render-Blocking, MOB-1-Regression): {tag}"


def test_btn_and_prio_button_have_44px_touch_target():
    css = open("app/static/css/app.css", encoding="utf-8").read()
    assert "min-height:44px;min-width:44px" in css.replace(" ", "") or (
        "min-height:44px" in css and "min-width:44px" in css
    )
    assert ".prio-quick-btn{" in css
    prio_rule_start = css.index(".prio-quick-btn{")
    prio_rule_end = css.index("}", prio_rule_start)
    prio_rule = css[prio_rule_start:prio_rule_end]
    assert "min-height:44px" in prio_rule
    assert "min-width:44px" in prio_rule
