"""Begrenzter GitHub-Lesezugriff: feste Endpunkte, keine Inhalte oder Mutationen."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from typing import Any

import httpx
from limits import parse
from limits.storage import storage_from_string
from limits.strategies import FixedWindowRateLimiter

from app.config import settings
from app.core.git_sha import valid_sha
from app.db import SessionLocal
from app.services import github_token

REPOSITORY = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")
MAX_BYTES = 512 * 1024


class ResponseCache:
    """Bewusst pro Worker: nur erneut ladbare Antworten, keine Rechte/Kontingente.

    Eigentum, 60-Sekunden-TTL und 128-Eintraege-Budget liegen beim Cache-Objekt;
    das autoritative Aufrufkontingent bleibt ausschliesslich im gemeinsamen Redis.
    """

    def __init__(self):
        self.entries: dict[tuple, tuple[float, dict]] = {}
        self.lock = threading.Lock()

    def get(self, key: tuple) -> dict | None:
        with self.lock:
            cached = self.entries.get(key)
            if cached and cached[0] > time.monotonic():
                return copy.deepcopy(cached[1])
        return None

    def put(self, key: tuple, result: dict):
        with self.lock:
            if len(self.entries) >= 128:
                self.entries.clear()
            self.entries[key] = (time.monotonic() + 60, copy.deepcopy(result))

    def clear(self):
        with self.lock:
            self.entries.clear()


_CACHE = ResponseCache()
_LIMITER = FixedWindowRateLimiter(storage_from_string(settings.REDIS_URL or "memory://"))


def unavailable(code: str) -> dict:
    return {
        "status": "eingeschraenkt",
        "code": code,
        "hinweis": "GitHub nicht vollstaendig verfuegbar; lokale Diagnose bleibt moeglich.",
    }


def _permit() -> bool:
    """Redis koordiniert das Prozess-/Hostkontingent ohne Datei-/DB-Schreibzugriff.

    Ohne gemeinsamen Speicher geschlossen ablehnen, niemals auf ein nur lokales
    Kontingent zurueckfallen. Lokale Codekorrelation funktioniert weiterhin.
    """
    try:
        return bool(settings.REDIS_URL) and _LIMITER.hit(parse("30/minute"), "github-readonly")
    except Exception:
        return False


class GitHubReadonly:
    def __init__(self, transport: httpx.BaseTransport | None = None, db=None, persist: bool = True):
        self._db = db
        self._persist = persist
        self._transport = transport
        self._deadline = time.monotonic() + max(0.1, min(settings.GITHUB_API_TIMEOUT_S, 8.0))

    def _get(self, suffix: str, project) -> dict:
        if self._db is not None:
            return self._get_with_db(suffix, project, self._db)
        with SessionLocal() as db:
            self._db = db
            try:
                result = self._get_with_db(suffix, project, db)
                if self._persist:
                    db.commit()
                return result
            finally:
                self._db = None

    def _get_with_db(self, suffix: str, project, db) -> dict:
        repo = settings.GITHUB_REPOSITORY
        if not REPOSITORY.fullmatch(repo) or any(p in (".", "..") for p in repo.split("/")):
            return unavailable("ungueltiges_repository")
        token, _, invalid = github_token.resolve(db)
        if invalid:
            return unavailable("nicht_autorisiert")
        key = (repo, hashlib.sha256(token.encode()).digest(), suffix)
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            return unavailable("timeout")
        if not _permit():
            return unavailable("gedrosselt")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = "Bearer " + token
        result: dict
        try:
            with httpx.Client(
                transport=self._transport, timeout=remaining, follow_redirects=False, trust_env=False
            ) as client:
                with client.stream("GET", f"https://api.github.com/repos/{repo}/{suffix}", headers=headers) as reply:
                    if self._persist:
                        github_token.observe(db, token, reply.status_code,
                                             reply.headers.get("github-authentication-token-expiration"))
                    rate = {}
                    for name in ("remaining", "reset", "limit"):
                        value = reply.headers.get(f"x-ratelimit-{name}", "")
                        if value.isdigit() and len(value) <= 12:
                            rate[name] = int(value)
                    if reply.status_code != 200:
                        code = {
                            401: "nicht_autorisiert",
                            403: "zugriff_verweigert",
                            404: "nicht_gefunden",
                            429: "rate_limit",
                        }.get(reply.status_code, "github_fehler")
                        if 300 <= reply.status_code < 400:
                            code = "redirect_abgelehnt"
                        if rate.get("remaining") == 0:
                            code = "rate_limit"
                        result = {**unavailable(code), "rate_limit": rate}
                    else:
                        data = bytearray()
                        for chunk in reply.iter_bytes():
                            if len(data) + len(chunk) > MAX_BYTES:
                                return unavailable("antwort_zu_gross")
                            if time.monotonic() > self._deadline:
                                return unavailable("timeout")
                            data.extend(chunk)
                        projected = project(json.loads(data))
                        # Selbst ein boesartiger/fehlkonfigurierter Upstream darf das Token nie reflektieren.
                        if token and token in json.dumps(projected, ensure_ascii=False):
                            return unavailable("ungueltige_antwort")
                        result = {
                            "status": "ok" if token else "eingeschraenkt",
                            "code": "ok" if token else "ohne_token",
                            "daten": projected,
                            "rate_limit": rate,
                        }
                        if not token:
                            result["hinweis"] = "Ohne Token: oeffentliche Daten mit niedrigem GitHub-Rate-Limit."
        except httpx.TimeoutException:
            result = unavailable("timeout")
        except Exception:
            # Niemals Exception-/Request-/Response-Texte loggen, verketten oder zurueckgeben.
            result = unavailable("netz_oder_antwort_fehler")
        _CACHE.put(key, result)
        return result

    def connection_checks(self) -> list[dict]:
        """Drei feste, kleine Leseproben ohne Jobs-/PR-Inhalte."""
        return [self.head_of_main(), self._get("actions/runs?per_page=1", lambda d: {}),
                self._get("pulls?per_page=1", lambda d: {})]

    def head_of_main(self) -> dict:
        return self._get("git/ref/heads/main", lambda d: {"sha": _sha(d.get("object", {}).get("sha"))})

    def compare(self, deployed_sha: str, base: str = "main") -> dict:
        if not valid_sha(deployed_sha) or base != "main":
            return unavailable("ungueltige_sha_oder_basis")

        # GitHub vergleicht base...head: ahead_by bezeichnet main gegenueber Deploy.
        def project(d):
            return {
                "commits_hinter_main": _count(d.get("ahead_by")),
                "commits_vor_main": _count(d.get("behind_by")),
                "anzahl_commits": _count(d.get("total_commits")),
                "dateien": [
                    {"datei": _filename(f.get("filename")), "status": _enum(f.get("status"), FILE_STATES)}
                    for f in d.get("files", [])[:50]
                ],
                "dateien_begrenzt": len(d.get("files", [])) >= 50 or bool(d.get("total_commits", 0) > 250),
            }

        return self._get(f"compare/{deployed_sha}...main", project)

    def commit_info(self, sha: str) -> dict:
        if not valid_sha(sha):
            return unavailable("ungueltige_sha")
        return self._get(f"git/commits/{sha}", lambda d: {"sha": _sha(d.get("sha")), "url": _url(d.get("html_url"))})

    def workflow_runs_for_sha(self, sha: str) -> dict:
        if not valid_sha(sha):
            return unavailable("ungueltige_sha")

        def project(d):
            runs = []
            for r in d.get("workflow_runs", [])[:10]:
                path = r.get("path", "")
                if path not in (".github/workflows/ci.yml", ".github/workflows/docker-build.yml"):
                    continue
                ident = _count(r.get("id"))
                jobs = self._get(
                    f"actions/runs/{ident}/jobs?per_page=30",
                    lambda j: [{"name": _enum(v.get("name"), JOB_NAMES), **_state(v)} for v in j.get("jobs", [])[:30]],
                )
                runs.append({"name": "CI" if path.endswith("/ci.yml") else "Docker Build", **_state(r), "jobs": jobs})
            return runs

        return self._get(f"actions/runs?head_sha={sha}&per_page=10", project)

    def pr_for_commit(self, sha: str) -> dict:
        if not valid_sha(sha):
            return unavailable("ungueltige_sha")
        return self._get(
            f"commits/{sha}/pulls?per_page=5",
            lambda d: [
                {
                    "nummer": _count(p.get("number")),
                    "status": _enum(p.get("state"), {"open", "closed"}),
                    "url": _url(p.get("html_url")),
                }
                for p in d[:5]
            ],
        )


FILE_STATES = {"added", "removed", "modified", "renamed", "copied", "changed", "unchanged"}
JOB_NAMES = {
    "Änderungen prüfen",
    "JS-Tests (Outbox)",
    "Lint (ruff)",
    "Type-Check (mypy)",
    "Migration (MariaDB)",
    "Tests (pytest)",
    "Board ohne Full-Reload (Playwright)",
    "Build (und Push auf main/Tags)",
}


def _enum(value: Any, allowed: set[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "unbekannt"


def _count(value: Any) -> int:
    return max(0, value) if type(value) is int else 0


def _sha(value: Any) -> str | None:
    return value if valid_sha(value) else None


def _filename(value: Any) -> str:
    if isinstance(value, str) and len(value) <= 240 and re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        if not value.startswith("/") and all(p not in ("", ".", "..") for p in value.split("/")):
            return value
    return "[MASKIERT]"


def _url(value: Any) -> str | None:
    repo = re.escape(settings.GITHUB_REPOSITORY)
    return (
        value
        if isinstance(value, str)
        and re.fullmatch(
            rf"https://github\.com/{repo}/(?:actions/runs/[0-9]+(?:/job/[0-9]+)?|pull/[0-9]+|commit/[0-9a-f]{{7,40}})",
            value,
        )
        else None
    )


def _state(row: dict) -> dict:
    stamp = row.get("created_at") or row.get("started_at")
    return {
        "status": _enum(row.get("status"), {"queued", "in_progress", "completed", "waiting", "pending", "requested"}),
        "conclusion": _enum(
            row.get("conclusion"),
            {"success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required", "stale"},
        ),
        "url": _url(row.get("html_url")),
        "zeit": stamp if isinstance(stamp, str) and re.fullmatch(r"[0-9TZ:.-]{10,30}", stamp) else None,
    }
