import asyncio
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import gateway_routing, linux_print_service, sms_outbox


@pytest.fixture(params=[gateway_routing, sms_outbox, linux_print_service], ids=lambda module: module.__name__)
def service(request, monkeypatch):
    module = request.param

    async def idle():
        await asyncio.Event().wait()

    monkeypatch.setattr(settings, "TESTING", False)
    monkeypatch.setattr(module, "_task", None)
    monkeypatch.setattr(module, "_poll" if module is gateway_routing else "_loop", idle)
    if module is gateway_routing:
        monkeypatch.setattr(module, "_sockets", {})
    return module


def test_start_stop_across_asyncio_runs(service):
    asyncio.run(service.start())
    old_task = service._task
    assert old_task.get_loop().is_closed()

    async def restart():
        await service.start()
        task = service._task
        assert task is not old_task
        assert task.get_loop() is asyncio.get_running_loop()
        await service.start()
        assert service._task is task
        await service.stop()
        assert service._task is None
        assert task.cancelled()

    asyncio.run(restart())


@pytest.mark.parametrize("restart", [False, True], ids=["stop_foreign", "replace_foreign"])
def test_pending_foreign_task_is_not_awaited(service, restart):
    owner = asyncio.new_event_loop()
    old_task = None
    try:
        owner.run_until_complete(service.start())
        old_task = service._task
        assert not old_task.done()

        async def next_loop():
            if restart:
                await service.start()
                assert service._task is not old_task
                assert service._task.get_loop() is asyncio.get_running_loop()
            await service.stop()
            assert service._task is None
            assert not old_task.done()

        asyncio.run(next_loop())
    finally:
        if old_task is not None:
            old_task.cancel()

            async def drain():
                await asyncio.gather(old_task, return_exceptions=True)

            owner.run_until_complete(drain())
        owner.close()


def test_gateway_stop_ends_local_sockets(monkeypatch):
    ended = []
    connection = object()
    monkeypatch.setattr(gateway_routing, "_task", None)
    monkeypatch.setattr(gateway_routing, "_sockets", {"test": SimpleNamespace(connection=connection)})
    monkeypatch.setattr(gateway_routing, "end", ended.append)

    asyncio.run(gateway_routing.stop())

    assert ended == [connection]
    assert gateway_routing._sockets == {}
