"""Drei lesende Code-/CI-Werkzeuge; der Codex-Auftrag bleibt ausschliesslich Text."""

import json
import re

from sqlalchemy import select

from app import log_buffer
from app.admin_mcp.masking import mask_text
from app.config import settings
from app.models.diagnose import DiagFinding
from app.services import code_analyse as code
from app.services import github_token
from app.services.github_readonly import GitHubReadonly, unavailable, valid_sha

TITLE_WORDS = frozenset(
    {
        "gateway",
        "fehler",
        "ci",
        "deploy",
        "redis",
        "worker",
        "code",
        "analyse",
        "laufzeitbefund",
        "untersuchen",
        "pruefen",
        "beheben",
        "regression",
        "diagnose",
        "mcp",
        "sms",
        "druck",
        "suchindex",
        "outbox",
    }
)


def safe_title(value: str | None) -> str:
    if value is None:
        return "Laufzeitbefund untersuchen"
    # Nur feste technische Woerter koennen keinen Personen-/Einsatznamen enthalten.
    masked = mask_text(value)
    words = re.findall(r"[A-Za-z]+|\[MASKIERT\]", masked)
    return " ".join(w if w.casefold() in TITLE_WORDS else "[MASKIERT]" for w in words[:20]) or "Laufzeitbefund"


PFLICHTSATZ = "Aenderungen nur ueber Branch, Tests und Pull Request, nie direkt im Produktionscontainer."


def deploy_info() -> dict:
    return {
        "deploy_sha": settings.GIT_SHA if valid_sha(settings.GIT_SHA) else None,
        "version": settings.APP_VERSION if re.fullmatch(r"[0-9.]{1,30}", settings.APP_VERSION) else "unbekannt",
    }


def ci_status(sha: str | None = None) -> dict:
    if sha is not None and not valid_sha(sha):
        raise ValueError("Ungueltige SHA")
    github = GitHubReadonly()
    head = github.head_of_main()
    deploy = deploy_info()
    target = sha or deploy["deploy_sha"] or head.get("daten", {}).get("sha")
    comparison = github.compare(deploy["deploy_sha"]) if deploy["deploy_sha"] else unavailable("deploy_sha_unbekannt")
    runs = github.workflow_runs_for_sha(target) if target else unavailable("sha_unbekannt")
    pr = github.pr_for_commit(target) if target else unavailable("sha_unbekannt")
    restricted = any(v["status"] != "ok" for v in (head, comparison, runs, pr)) or any(
        run.get("jobs", {}).get("status") != "ok" for run in runs.get("daten", [])
    )
    return {
        "status": "eingeschraenkt" if restricted else "ok",
        "evidence": {
            **deploy,
            "abgefragte_sha": target,
            "main": head,
            "vergleich": comparison,
            "workflows": runs,
            "pull_requests": pr,
            "hinweis": "GitHub-Daten eingeschraenkt oder nicht verfuegbar."
            if restricted
            else "Lesender GitHub-Abgleich.",
        },
    }


def collect_code(db, name: str, **args) -> dict:
    result = _collect_code(db, name, **args)
    access = github_token.status(db)

    def github_errors(value):
        if isinstance(value, dict):
            yield value.get("code")
            for child in value.values():
                yield from github_errors(child)
        elif isinstance(value, list):
            for child in value:
                yield from github_errors(child)

    errors = set(github_errors(result))
    override = ("ungueltig" if "nicht_autorisiert" in errors else "keine_berechtigung"
                if errors & {"zugriff_verweigert", "nicht_gefunden"} else "eingeschraenkt"
                if errors & {"timeout", "gedrosselt", "rate_limit", "github_fehler", "redirect_abgelehnt",
                             "netz_oder_antwort_fehler", "ungueltiges_repository", "antwort_zu_gross",
                             "ungueltige_antwort"} else None)
    parts = [value for key, value in result.get("evidence", {}).items()
             if key in ("main", "vergleich", "workflows", "pull_requests") and isinstance(value, dict)]
    if override is None and parts and all(part.get("status") == "ok" for part in parts):
        override = "ok"
    if override:
        access = github_token.status(db, override=override)
    if access["status"] == "nicht_getestet":
        access["status"] = "eingeschraenkt"
    result["github_zugang"] = access
    if access["status"] != "ok":
        result["status"] = "eingeschraenkt"
        for value in result.get("evidence", {}).values():
            if isinstance(value, dict) and "status" in value:
                value["status"] = "warn"
    token, _, _ = github_token.resolve(db)

    def redact(value):
        if isinstance(value, str):
            for secret in (token, settings.GITHUB_READ_TOKEN):
                if secret:
                    value = value.replace(secret, "[MASKIERT]")
            return value
        if isinstance(value, dict):
            return {redact(k): redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [redact(v) for v in value]
        return value

    return redact(result)


def _collect_code(db, name: str, **args) -> dict:
    if name == "admin_ci_status":
        return ci_status(args.get("sha"))
    finding_key = args.get("finding_key")
    source = args.get("quelle", "logs")
    limit = args.get("limit", 100)
    level = args.get("level") or "ERROR"
    if (
        source not in ("logs", "befund")
        or type(limit) is not int
        or not 1 <= limit <= 100
        or level not in log_buffer.LEVEL_ORDER
    ):
        raise ValueError("Ungueltiger Filter")
    finding = None
    if finding_key is not None:
        if not isinstance(finding_key, str) or not re.fullmatch(r"[0-9a-f]{64}", finding_key):
            raise ValueError("Ungueltiger Befundschluessel")
        finding = db.scalar(select(DiagFinding).where(DiagFinding.finding_key == finding_key))
        if finding is None:
            raise ValueError("Befund nicht gefunden")
    if source == "befund" and finding is None:
        raise ValueError("Befundschluessel erforderlich")
    groups = code.finding_groups(finding) if finding else code.group_logs(log_buffer.get_entries(2000, level)[-limit:])
    modules = code.modules_for_check(finding.check_id) if finding else sorted({g["datei"] for g in groups})
    deploy = deploy_info()
    comparison = (
        GitHubReadonly().compare(deploy["deploy_sha"]) if deploy["deploy_sha"] else unavailable("deploy_sha_unbekannt")
    )
    changes = comparison.get("daten", {}).get("dateien")
    for group in groups:
        # Fehlende/abgeschnittene Vergleiche liefern unbekannt statt falscher Entwarnung.
        changed = changes is not None and any(f["datei"] == group["datei"] for f in changes)
        group["seit_deploy_geaendert"] = (
            True
            if changed
            else None
            if (changes is None or comparison.get("daten", {}).get("dateien_begrenzt"))
            else False
        )
    evidence = {
        **deploy,
        "quelle": "befund" if finding else "lokaler Worker-Ringpuffer",
        "fehlerstellen": groups,
        "module": modules,
        "vergleich": comparison,
    }
    state = "ok" if comparison["status"] == "ok" else "eingeschraenkt"
    if name == "admin_code_analyse":
        return {"status": state, "evidence": evidence}
    selector = args.get("fehlerstelle")
    if (finding_key is None) == (selector is None):
        raise ValueError("Genau Befund oder Fehlerstelle angeben")
    if selector is not None:
        if not isinstance(selector, dict) or set(selector) != {"datei", "funktion", "exception_typ"}:
            raise ValueError("Ungueltige Fehlerstelle")
        groups = [g for g in groups if all(g[k] == v for k, v in selector.items())]
        if not groups:
            raise ValueError("Fehlerstelle nicht in lokaler Evidenz")
        modules = sorted({g["datei"] for g in groups})
    # Freie Titel auf ein kontrolliertes technisches Vokabular begrenzen.
    title = args.get("titel")
    if title is not None and (not isinstance(title, str) or len(title) > 200):
        raise ValueError("Ungueltiger Titel")
    title = safe_title(title)
    markdown = "\n\n".join(
        [
            "# Codex-Auftrag: " + title,
            "## Fehlerbeschreibung\nDie folgenden lokal belegten Fehlerstellen beziehungsweise Befundmodule pruefen.",
            "## Maskierte Evidenz\n```json\n" + json.dumps(groups, ensure_ascii=False, indent=2) + "\n```",
            "## Deploy und Abweichung zu main\n```json\n"
            + json.dumps({**deploy, "vergleich": comparison}, ensure_ascii=False)
            + "\n```",
            "## Vermutete Ursache\nHypothese: Fehler im betroffenen Modul oder unvollstaendige Konfiguration. "
            "Nicht bestaetigt.",
            "## Reproduktionsschritte\nunbekannt; aus dieser Evidenz nicht sicher ableitbar.",
            "## Testidee\nRegressionstest fuer die belegte Stelle und den Exception-Typ ergaenzen; "
            "externe Dienste mocken.",
            "## Betroffene Dateien\n" + "\n".join("- " + m for m in modules),
            "## Abnahme und Verifikation\nCLAUDE.md strikt befolgen. "
            "Ursache anhand eines reproduzierbaren Tests bestaetigen. "
            "Regressionstest, ruff, mypy und pytest ausfuehren; "
            "Tenant-Isolation und bestehende MCP-Werkzeuge bewahren.",
            PFLICHTSATZ,
        ]
    )
    # Keine generische Maskierung der technischen SHA/Pfade: alle Felder stammen aus
    # engen Projektionen. Exaktes Secret-Redacting bleibt die letzte Sicherheitsgrenze.
    if settings.GITHUB_READ_TOKEN:
        markdown = markdown.replace(settings.GITHUB_READ_TOKEN, "[MASKIERT]")
    return {"status": state, "evidence": {"markdown": markdown, "titel": title, "vergleich": comparison}}
