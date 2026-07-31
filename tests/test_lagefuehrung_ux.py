from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
JS = (_ROOT / "app" / "static" / "js" / "lagefuehrung.js").read_text(encoding="utf-8")
BACKEND = (_ROOT / "app" / "routers" / "ui_lagefuehrung.py").read_text(encoding="utf-8")


def test_websocket_updates_features_incrementally() -> None:
    assert "renderFeature(data.feature)" in JS
    assert "delete featureLayers[data.feature_id]" in JS
    created_branch = JS.split('data.type === "lagefuehrung.feature.created"', 1)[1]
    created_branch = created_branch.split('data.type === "lagefuehrung.feature.deleted"', 1)[0]
    assert "ladeFeatures();" in created_branch  # payload fallback only


def test_backend_websocket_payload_contract() -> None:
    assert '{"type": "lagefuehrung.feature.created", "feature": out}' in BACKEND
    assert '{"type": "lagefuehrung.feature.updated", "feature": out}' in BACKEND
    assert '{"type": "lagefuehrung.feature.deleted", "feature_id": feature.id}' in BACKEND
