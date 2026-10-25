"""Gunicorn-Worker mit mehr Toleranz fuer verzoegerte WebSocket-Pongs."""

from uvicorn.workers import UvicornWorker


class TolerantUvicornWorker(UvicornWorker):
    CONFIG_KWARGS = {
        **UvicornWorker.CONFIG_KWARGS,
        "ws_ping_interval": 20.0,
        "ws_ping_timeout": 60.0,
    }
