import os

import pytest

from e2e.multi_worker.lab import Lab


def pytest_addoption(parser):
    parser.addoption("--ecp-workers", type=int, choices=(1, 2, 3, 4), default=2)
    parser.addoption("--ecp-artifacts", default=None)


@pytest.fixture(scope="session")
def lab(request, tmp_path_factory):
    if os.environ.get("ECP_RUN_MULTIWORKER_ACCEPTANCE") != "1":
        pytest.skip("Opt-in: eigene Docker-Testdienste und echte Gunicorn-Prozesse")
    count = request.config.getoption("--ecp-workers")
    if count > 2 and os.environ.get("ECP_POST_PRODUCTION_LOAD_APPROVED") != "1":
        pytest.skip("3/4-Worker-Laststufe erst nach freigegebener 2-Worker-Produktionsabnahme")
    directory = request.config.getoption("--ecp-artifacts") or tmp_path_factory.mktemp(f"multi-worker-{count}")
    patch = pytest.MonkeyPatch()
    instance = Lab(count, directory, patch)
    request.config._phase6_lab = instance
    try:
        instance.start()
        yield instance
    finally:
        instance.close()
        patch.undo()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    report = result.get_result()
    if report.when == "call":
        reports = getattr(item.config, "_phase6_reports", [])
        reports.append({"test": item.nodeid, "outcome": report.outcome, "duration_s": report.duration})
        item.config._phase6_reports = reports


def pytest_sessionfinish(session, exitstatus):
    import json

    instance = getattr(session.config, "_phase6_lab", None)
    if instance:
        (instance.directory / "pytest-results.json").write_text(
            json.dumps(
                {
                    "exit_code": int(exitstatus),
                    "tests": getattr(session.config, "_phase6_reports", []),
                },
                indent=2,
            )
        )
