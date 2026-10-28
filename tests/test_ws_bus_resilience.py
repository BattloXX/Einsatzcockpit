import asyncio
import json

import pytest

from app.services import ws_bus


@pytest.fixture
def isolate(monkeypatch):
    monkeypatch.setattr(ws_bus, "_handlers", {})
    monkeypatch.setattr(ws_bus, "_diagnostics", {"publish_failures": 0, "subscriber_failures": 0, "reconnects": 0})
    monkeypatch.setattr(ws_bus, "_last_warning", {})
    monkeypatch.setattr(ws_bus.settings, "REDIS_URL", "redis://secret-user:SECRET_TOKEN@invalid/0")


@pytest.mark.usefixtures("isolate")
async def test_disconnect_and_close_failure_reconnect_and_resubscribe_all(monkeypatch, caplog):
    reconnected = []

    async def on_reconnect():
        assert ws_bus._subscribed
        assert ws_bus._redis is not None
        reconnected.append(True)

    monkeypatch.setattr(ws_bus, "_reconnect_callbacks", [on_reconnect])
    delivered = []
    complete = asyncio.Event()
    channels = [ws_bus.CH_WS, ws_bus.CH_GW, ws_bus.CH_LAGEDOKUMENT, ws_bus.CH_LAGEDOKUMENT_AWARENESS]
    for channel in channels:
        async def handler(message):
            delivered.append(message)
            if len(delivered) == 4:
                complete.set()
        ws_bus.register(channel, handler)
    class Pubsub:
        def __init__(self, number):
            self.number = number
            self.channels = []
            self.acks = []
            self.messages = list(channels)
        async def subscribe(self, *names):
            self.channels = list(names)
            self.acks = list(names)
        async def get_message(self, **kwargs):
            if self.acks:
                return {"type": "subscribe", "channel": self.acks.pop()}
            if self.number == 1:
                raise ConnectionError("subscriber EOF")
            if self.messages:
                channel = self.messages.pop()
                return {"type": "message", "channel": channel, "data": json.dumps({"channel": channel})}
            await asyncio.sleep(.01)
            return None
        async def aclose(self):
            if self.number == 1:
                raise RuntimeError("SECRET_TOKEN")
    class Client:
        def __init__(self, number):
            self.sub = Pubsub(number)
        async def ping(self):
            return True
        def pubsub(self):
            return self.sub
        async def aclose(self):
            pass
    clients = []
    def connect(*args, **kwargs):
        result = Client(len(clients) + 1)
        if kwargs.get("socket_timeout") is None:
            clients.append(result)
        return result
    monkeypatch.setattr("redis.asyncio.from_url", connect)
    try:
        await ws_bus.start()
        await asyncio.wait_for(complete.wait(), timeout=2)
        assert all(set(c.sub.channels) == set(channels) for c in clients)
        assert ws_bus.status()["connected"]
        assert ws_bus.status()["subscriber_failures"] == 1
        assert len(reconnected) == 2
        assert "SECRET_TOKEN" not in caplog.text
    finally:
        await ws_bus.stop()


@pytest.mark.usefixtures("isolate")
async def test_publish_failure_and_handler_exception_never_log_payload(monkeypatch, caplog):
    class Client:
        async def publish(self, *args):
            raise ConnectionError("SECRET_TOKEN private full SMS")
    monkeypatch.setattr(ws_bus, "_redis", Client())
    async def handler(payload):
        raise RuntimeError(payload["sms"])
    ws_bus.register(ws_bus.CH_GW, handler)
    assert not await ws_bus.publish(ws_bus.CH_GW, {"sms": "private full SMS"})
    queue = asyncio.Queue(maxsize=1000)
    monkeypatch.setattr(ws_bus, "_queue", queue)
    consumer = asyncio.create_task(ws_bus._consume(queue))
    try:
        await ws_bus._dispatch({"type": "message", "channel": ws_bus.CH_GW,
                                "data": json.dumps({"sms": "private full SMS"})})
        await queue.join()
    finally:
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
    assert ws_bus.status()["publish_failures"] == 1
    assert "SECRET_TOKEN" not in caplog.text and "private full SMS" not in caplog.text
    assert "publish_failed" in caplog.text and "handler_failed" in caplog.text


@pytest.mark.usefixtures("isolate")
async def test_zero_subscribers_is_not_delivery_success(monkeypatch):
    class Client:
        async def publish(self, *args):
            return 0
    monkeypatch.setattr(ws_bus, "_redis", Client())
    assert not await ws_bus.publish(ws_bus.CH_GW, {"kind": "hint"})
    assert ws_bus.status()["last_error"] == "publish_no_subscribers"


async def test_silent_subscriber_blackhole_times_out(monkeypatch):
    monkeypatch.setattr(ws_bus, "HEALTH_INTERVAL_S", .01)
    monkeypatch.setattr(ws_bus, "HEALTH_TIMEOUT_S", .01)
    class Pubsub:
        async def get_message(self, **kwargs):
            await asyncio.sleep(.005)
            return None
        async def ping(self):
            pass  # Keine Antwort auf der Subscriber-Verbindung.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(ws_bus._reader(Pubsub()), timeout=1)
