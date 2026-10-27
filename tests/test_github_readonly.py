"""GitHub bleibt in Tests vollstaendig lokal, begrenzt und geheimnisfrei."""
import json
import multiprocessing
import time

import httpx
import pytest

from app.config import settings
from app.services import github_readonly as gh

SHA = "abcdef1234567890abcdef1234567890abcdef12"
SECRET = "seed_github_READ_secret_987654321"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    gh._CACHE.clear()
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", SECRET)
    monkeypatch.setattr(settings, "GITHUB_REPOSITORY", "BattloXX/Einsatzcockpit")
    monkeypatch.setattr(gh, "_permit", lambda: True)
    yield
    gh._CACHE.clear()


def client(handler):
    return gh.GitHubReadonly(httpx.MockTransport(handler))


@pytest.mark.parametrize("token", ["", SECRET])
def test_header_cache_and_small_projection(monkeypatch, token):
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", token)
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "GET" and request.url.host == "api.github.com"
        assert request.headers.get("Authorization") == ("Bearer " + token if token else None)
        return httpx.Response(200, json={"object": {"sha": SHA}, "commit": {"message": SECRET}},
                              headers={"x-ratelimit-remaining": "17"})
    github = client(handler)
    a = github.head_of_main()
    a["daten"]["sha"] = "changed"
    b = github.head_of_main()
    assert b["daten"] == {"sha": SHA} and len(requests) == 1
    assert b["status"] == ("ok" if token else "eingeschraenkt")
    assert b["rate_limit"]["remaining"] == 17
    assert SECRET not in json.dumps(b)
    gh._CACHE.clear()
    monkeypatch.setattr(gh, "_permit", lambda: False)
    assert github.head_of_main()["code"] == "gedrosselt"
    assert len(requests) == 1


@pytest.mark.parametrize("status,code", [(401, "nicht_autorisiert"), (403, "zugriff_verweigert"),
    (404, "nicht_gefunden"), (429, "rate_limit"), (500, "github_fehler"), (302, "redirect_abgelehnt")])
def test_errors_redirect_and_no_secret(status, code, caplog):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=SECRET, headers={"Location": "https://evil.example/" + SECRET})
    result = client(handler).head_of_main()
    assert result["code"] == code and result["status"] == "eingeschraenkt"
    assert len(calls) == 1
    assert SECRET not in json.dumps(result) + caplog.text


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "netz_oder_antwort_fehler")])
def test_network_failure(error, code, caplog):
    def handler(request):
        raise error(SECRET, request=request)
    result = client(handler).head_of_main()
    assert result["code"] == code
    assert SECRET not in json.dumps(result) + caplog.text


@pytest.mark.parametrize("repo", ["../repo", "owner/../repo", "owner/repo/extra", "https://evil/repo", "owner/repo?x", "owner/..", "@/repo"])
def test_invalid_repository(monkeypatch, repo):
    monkeypatch.setattr(settings, "GITHUB_REPOSITORY", repo)
    assert client(lambda _: pytest.fail("Netz fuer ungueltiges Repo")).head_of_main()["code"] == "ungueltiges_repository"


@pytest.mark.parametrize("sha", ["main", "ABCDEF12", "abcdef", "a" * 41, "../main", "a?x=1", None])
def test_invalid_sha(sha):
    github = client(lambda _: pytest.fail("Netz fuer ungueltige SHA"))
    for method in (github.compare, github.commit_info, github.workflow_runs_for_sha, github.pr_for_commit):
        assert method(sha)["status"] == "eingeschraenkt"


def test_compare_workflows_jobs_pr_and_commit():
    requests = []
    def handler(request):
        requests.append(request)
        path = request.url.path
        if "/compare/" in path:
            return httpx.Response(200, json={"ahead_by": 3, "behind_by": 1, "total_commits": 3,
                "files": [{"filename": f"app/file{i}.py", "status": "modified", "patch": SECRET} for i in range(70)]})
        if path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": [{"name": name, "status": "completed", "conclusion": "success"}
                                                       for name in gh.JOB_NAMES]})
        if path.endswith("/runs"):
            return httpx.Response(200, json={"workflow_runs": [
                {"id": i, "path": path, "name": SECRET, "status": "completed", "conclusion": "success",
                 "html_url": f"https://github.com/BattloXX/Einsatzcockpit/actions/runs/{i}",
                 "created_at": "2026-10-27T10:00:00Z"} for i, path in enumerate([
                    ".github/workflows/ci.yml", ".github/workflows/docker-build.yml"], start=1)]})
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[{"number": 7, "title": SECRET, "state": "open",
                "html_url": "https://github.com/BattloXX/Einsatzcockpit/pull/7"}])
        return httpx.Response(200, json={"sha": SHA, "html_url": "https://evil.example/" + SECRET})
    github = client(handler)
    compare = github.compare(SHA)["daten"]
    assert compare["commits_hinter_main"] == 3 and compare["commits_vor_main"] == 1
    assert len(compare["dateien"]) == 50 and compare["dateien_begrenzt"]
    runs = github.workflow_runs_for_sha(SHA)
    assert [r["name"] for r in runs["daten"]] == ["CI", "Docker Build"]
    assert {j["name"] for j in runs["daten"][0]["jobs"]["daten"]} == gh.JOB_NAMES
    assert github.pr_for_commit(SHA)["daten"][0]["nummer"] == 7
    assert github.commit_info(SHA)["daten"]["url"] is None
    assert SECRET not in json.dumps([compare, runs, github.pr_for_commit(SHA)])
    assert all(r.method == "GET" for r in requests)


def test_rate_limit_oversize_bad_json_reflection_and_deadline(monkeypatch):
    assert client(lambda r: httpx.Response(403, headers={"x-ratelimit-remaining": "0"})).head_of_main()["code"] == "rate_limit"
    gh._CACHE.clear()
    assert client(lambda r: httpx.Response(200, content=b"x" * (gh.MAX_BYTES + 1))).head_of_main()["code"] == "antwort_zu_gross"
    assert client(lambda r: httpx.Response(200, text=SECRET)).head_of_main()["code"] == "netz_oder_antwort_fehler"
    gh._CACHE.clear()
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", "abcdef12")
    assert client(lambda r: httpx.Response(200, json={"object": {"sha": "abcdef12"}})).head_of_main()["code"] == "ungueltige_antwort"
    github = client(lambda r: pytest.fail("deadline ignoriert"))
    github._deadline = time.monotonic() - 1
    assert github.compare(SHA)["code"] == "timeout"


def test_cache_expiry_and_auth_isolation(monkeypatch):
    calls = []
    def handler(r):
        calls.append(r)
        return httpx.Response(200, json={"object": {"sha": SHA}})
    github = client(handler)
    github.head_of_main()
    key = next(iter(gh._CACHE.entries))
    gh._CACHE.entries[key] = (0, gh._CACHE.entries[key][1])
    github.head_of_main()
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", "")
    assert github.head_of_main()["status"] == "eingeschraenkt"
    assert len(calls) == 3
    assert SECRET not in repr(gh._CACHE.entries) and SECRET not in repr(settings)


def _permit_child(queue, directory):
    import tempfile
    from app.config import settings
    from app.services.github_readonly import _permit
    settings.REDIS_URL = ""
    tempfile.tempdir = directory
    queue.put(_permit())


def test_process_quota_without_shared_storage(tmp_path):
    # Ohne Redis darf kein Prozess unkoordiniert auf GitHub zugreifen.
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    children = [context.Process(target=_permit_child, args=(queue, str(tmp_path))) for _ in range(2)]
    for child in children:
        child.start()
    for child in children:
        child.join(20)
        assert child.exitcode == 0
    assert [queue.get(timeout=2) for _ in children] == [False, False]
    assert list(tmp_path.iterdir()) == []


def test_shared_quota_and_unavailable_storage(monkeypatch):
    from limits.storage import MemoryStorage
    from limits.strategies import FixedWindowRateLimiter
    import importlib.util
    spec = importlib.util.spec_from_file_location("github_quota_test", gh.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://configured")
    module._LIMITER = FixedWindowRateLimiter(MemoryStorage())
    assert all(module._permit() for _ in range(30))
    assert not module._permit()
    monkeypatch.setattr(module._LIMITER, "hit", lambda *a: (_ for _ in ()).throw(RuntimeError(SECRET)))
    assert not module._permit()
