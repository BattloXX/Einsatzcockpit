"""Leichter Deploy-Check; kein Ausfallbefund bei fehlenden GitHub-Daten."""

from app.admin_mcp.zeit import iso_utc
from app.config import settings
from app.services import github_token
from app.services.diagnose.registry import CheckResult, Finding, register_check
from app.services.github_readonly import GitHubReadonly, valid_sha


@register_check("deploy.version", "deploy", "Build-Commit und Abstand zu main lesen")
def check_deploy(db, budget):
    access = github_token.status(db)
    findings = []
    days = access["restlaufzeit_tage"]
    if access["quelle"] != "keine" and days is not None and 0 <= days <= 30:
        findings.append(Finding(
            "github_token_laeuft_ab", "GitHub-Lesetoken laeuft bald ab", "warn" if days <= 14 else "info",
            {"restlaufzeit_tage": days, "ablauf_am": iso_utc(access["ablauf_am"])},
            ursache_hypothese=("Das hinterlegte GitHub-Lesetoken erreicht sein Ablaufdatum; "
                                  "CI-/Deploy-Abfragen fallen danach aus."),
            empfehlung=("Neues fine-grained GitHub-Lesetoken mit ausschliesslich lesenden "
                           "Rechten fuer Contents und Actions erstellen "
                       "und unter Systemeinstellungen > GitHub-Zugang samt Ablaufdatum hinterlegen."),
        ))
    if access["status"] in ("abgelaufen", "ungueltig", "keine_berechtigung"):
        findings.append(Finding("github.token", "github.token: GitHub-Zugang pruefen", "warn",
                                {"status": access["status"], "tage": access["restlaufzeit_tage"]}))
    if not valid_sha(settings.GIT_SHA):
        return CheckResult(findings + [
            Finding("sha_unbekannt", "Deploy-Commit unbekannt", "warn", {"deploy_sha_bekannt": False})])
    # Netzwerk ist im isolierten Prozess erlaubt; dessen harte Gesamtfrist umfasst
    # auch blockierende Netzwerkzugriffe. Tests koennen einen Snapshot uebergeben.
    result = budget.runtime.get("deploy")
    if result is None:
        result = GitHubReadonly(db=db, persist=False).compare(settings.GIT_SHA)
    if not findings and access["quelle"] != "keine" and result.get("code") in (
            "nicht_autorisiert", "zugriff_verweigert", "nicht_gefunden"):
        state = "ungueltig" if result["code"] == "nicht_autorisiert" else "keine_berechtigung"
        findings.append(Finding("github.token", "github.token: GitHub-Zugang pruefen", "warn",
                                {"status": state, "tage": access["restlaufzeit_tage"]}))
    data = result.get("daten")
    if data is None:
        # Unvollstaendig verhindert, dass ein alter Versionsbefund wegen Netzausfall
        # automatisch als behoben markiert wird. Kein neuer Stoerungsbefund.
        hinweis = (
            "unbekannt: GitHub-Abgleich nicht verfuegbar"
            if access["quelle"] != "keine"
            else "Info: kein GitHub-Token gesetzt; GitHub-Abgleich unbekannt"
        )
        return CheckResult(findings, False, hinweis)
    behind = data.get("commits_hinter_main", 0)
    if behind:
        return CheckResult(
            findings + [Finding("hinter_main", "Deploy-Commit liegt hinter main", "warn",
                                {"commits_hinter_main": behind})]
        )
    return CheckResult(findings)
