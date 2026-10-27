"""Leichter Deploy-Check; kein Ausfallbefund bei fehlenden GitHub-Daten."""

from app.config import settings
from app.services.diagnose.registry import CheckResult, Finding, register_check
from app.services.github_readonly import GitHubReadonly, valid_sha


@register_check("deploy.version", "deploy", "Build-Commit und Abstand zu main lesen")
def check_deploy(db, budget):
    if not valid_sha(settings.GIT_SHA):
        return CheckResult([Finding("sha_unbekannt", "Deploy-Commit unbekannt", "warn", {"deploy_sha_bekannt": False})])
    # Netzwerk ist im isolierten Prozess erlaubt; dessen harte Gesamtfrist umfasst
    # auch blockierende Netzwerkzugriffe. Tests koennen einen Snapshot uebergeben.
    result = budget.runtime.get("deploy")
    if result is None:
        result = GitHubReadonly().compare(settings.GIT_SHA)
    data = result.get("daten")
    if data is None:
        # Unvollstaendig verhindert, dass ein alter Versionsbefund wegen Netzausfall
        # automatisch als behoben markiert wird. Kein neuer Stoerungsbefund.
        return CheckResult([], False, "unbekannt: GitHub-Abgleich nicht verfuegbar")
    behind = data.get("commits_hinter_main", 0)
    if behind:
        return CheckResult(
            [Finding("hinter_main", "Deploy-Commit liegt hinter main", "warn", {"commits_hinter_main": behind})]
        )
    return CheckResult()
