"""Gemeinsames Austauschformat fuer Backend, JS und kuenftige Android-Tests."""
import json
from pathlib import Path


def test_gemeinsame_logik_vektoren_schema():
    data = json.loads((Path(__file__).parent / "data/asuew/logik_vektoren.json").read_text())
    assert set(data) == {"schema_version", "description", "cases"}
    assert data["schema_version"] == 1 and isinstance(data["description"], str)
    assert data["cases"]
    functions = {"isValidLocalEvent": 1, "applyLocalEvents": 2, "pruneLocalEvents": 3,
                 "serializeLocalEvents": 1, "parseLocalEvents": 1, "parseState": 1,
                 "timeWarnings": 2, "activeAlarms": 2, "nextDeadlines": 2,
                 "nextWakeupServerMs": 2, "clockOffsetMs": 2, "serverNow": 2,
                 "serverToLocal": 2, "connectionStatus": 2, "batteryStatus": 2,
                 "notificationLines": 2}
    names = []
    for case in data["cases"]:
        assert set(case) == {"name", "function", "args", "expected"}
        assert isinstance(case["name"], str) and case["name"]
        assert case["function"] in functions
        assert isinstance(case["args"], list) and len(case["args"]) == functions[case["function"]]
        names.append(case["name"])
    assert len(names) == len(set(names))
    assert {case["function"] for case in data["cases"]} == functions.keys()
