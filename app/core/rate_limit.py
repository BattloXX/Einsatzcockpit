"""Shared slowapi limiter instance.

Import this in routers that need per-endpoint rate limiting.
The limiter is None when slowapi is not installed; callers must guard.
"""
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    limiter: Limiter | None = Limiter(
        key_func=get_remote_address,
        default_limits=["300/minute"],
    )
except ImportError:
    limiter = None
