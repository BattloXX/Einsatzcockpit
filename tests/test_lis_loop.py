"""Tests for persistent LIS clients and per-organization poll scheduling."""
import asyncio

from app.models.lis import OrgLisConfig
from app.models.master import FireDept
from app.services.lis import lis_loop
from tests.conftest import TestingSession


class _LoopClient:
    created = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.closed = False
        self.created.append(self)

    async def aclose(self):
        self.closed = True


def test_get_client_reuses_matching_fingerprint_and_closes_replaced_client(monkeypatch):
    from app.services.lis import lis_client

    _LoopClient.created.clear()
    monkeypatch.setattr(lis_client, "LisClient", _LoopClient)
    values = ("https://lis.invalid", "LIS", "test-user", "secret", "cipher-a", None, False, "org-guid")
    first = asyncio.run(lis_loop._get_client(77, values))
    assert asyncio.run(lis_loop._get_client(77, values)) is first
    second = asyncio.run(lis_loop._get_client(77, values[:4] + ("cipher-b",) + values[5:]))
    assert second is not first
    assert first.closed is True


def test_sync_one_org_honors_ten_second_interval(monkeypatch, setup_db):
    db = TestingSession()
    config = None
    try:
        org = db.get(FireDept, 1)
        config = OrgLisConfig(
            org_id=org.id, enabled=True, base_url="https://lis.invalid", site="LIS",
            username="test-user", password_enc="cipher", organization_id="org-guid", poll_interval_seconds=10,
        )
        db.add(config)
        db.commit()
        config_id = config.id
        calls = []

        async def fake_sync(*args, **kwargs):
            calls.append(kwargs["client"])

        async def fake_get_client(*args):
            return object()

        times = iter([100.0, 109.9, 110.0])
        monkeypatch.setattr("app.core.crypto.decrypt_secret", lambda value: "secret")
        monkeypatch.setattr("app.services.lis.lis_sync.sync_organization", fake_sync)
        monkeypatch.setattr(lis_loop, "_get_client", fake_get_client)
        monkeypatch.setattr(lis_loop, "monotonic", lambda: next(times))
        asyncio.run(lis_loop._sync_one_org(1, config_id))
        asyncio.run(lis_loop._sync_one_org(1, config_id))
        asyncio.run(lis_loop._sync_one_org(1, config_id))
        assert len(calls) == 2
    finally:
        if config is not None:
            db.delete(config)
            db.commit()
        db.close()
