"""Ermittelt den Download der aktuellen Android-App."""
from __future__ import annotations

import time

import httpx

from app.config import settings

_ERFOLG_TTL = 60 * 60
_FEHLER_TTL = 5 * 60
_cache_version: str | None = None
_cache_url: str | None = None
_cache_bis = 0.0


def _fallback_url() -> str:
    return f"https://github.com/{settings.ANDROID_APP_REPO}/releases/latest"


def android_apk_url() -> str:
    """Liefert die aktuelle APK-URL, mit kurzzeitigem Fallback bei GitHub-Fehlern."""
    global _cache_bis, _cache_url, _cache_version

    now = time.monotonic()
    if _cache_url is not None and now < _cache_bis:
        return _cache_url

    _cache_version = None
    fallback = _fallback_url()
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{settings.ANDROID_APP_REPO}/releases/latest",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Einsatzcockpit-App-Download/1.0",
            },
            timeout=5.0,
        )
        response.raise_for_status()
        release = response.json()
        tag = release.get("tag_name")
        _cache_version = tag if isinstance(tag, str) else None
        assets = release.get("assets", [])
        apk = next(
            (
                asset.get("browser_download_url")
                for asset in assets
                if isinstance(asset, dict)
                and str(asset.get("name", "")).startswith(settings.ANDROID_APK_PRAEFIX)
                and str(asset.get("name", "")).endswith(".apk")
                and asset.get("browser_download_url")
            ),
            None,
        )
        if not apk:
            _cache_url = fallback
            _cache_bis = now + _FEHLER_TTL
            return _cache_url
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        _cache_version = None
        _cache_url = fallback
        _cache_bis = now + _FEHLER_TTL
    else:
        _cache_url = apk
        _cache_bis = now + _ERFOLG_TTL
    return _cache_url


def android_release_version() -> str | None:
    """Release-Tag aus demselben Abruf und Cache wie der APK-Download."""
    android_apk_url()
    return _cache_version
