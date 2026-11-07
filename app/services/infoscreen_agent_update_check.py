"""Repo-Updates: nur Freigabe und Policy, keine Paket- oder Einsatzdaten."""
from datetime import UTC, datetime, time, timedelta
from functools import cmp_to_key
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenRelease, InfoscreenReleaseDiscoveryLock
from app.services import infoscreen_agent_releases as releases

MAX_RELEASES = 1000
MAX_DISCOVERIES_PER_DAY = 3


def plausible(version: str, baseline: str) -> bool:
    """Neue Epochs und Spruenge ueber mehr als zehn Hauptversionen brauchen einen Admin."""
    def parts(value: str) -> tuple[int, int]:
        import re
        epoch, sep, rest = value.partition(":")
        upstream = rest if sep else value
        return int(epoch) if sep else 0, int(re.match(r"[0-9]+", upstream)[0])  # type: ignore[index]
    epoch, major = parts(version)
    old_epoch, old_major = parts(baseline)
    return epoch <= old_epoch and major <= old_major + 10


def discover(db: Session, device: InfoscreenDevice, version: str, channel: str, arch: str,
             current: str, now: datetime) -> InfoscreenRelease | None:
    query = db.query(InfoscreenRelease).filter_by(version=version, channel=channel, arch=arch)
    row = query.first()
    if row is not None:
        return row
    # UPDATE serialisiert auch unter SQLite. Der folgende Locking-Read sieht unter MariaDB
    # den aktuellen Stand statt eines vor dem Mutex angelegten Repeatable-Read-Snapshots.
    if db.get(InfoscreenReleaseDiscoveryLock, 1) is None:
        try:
            with db.begin_nested():
                db.add(InfoscreenReleaseDiscoveryLock(id=1))
                db.flush()
        except IntegrityError:
            # Ein anderer Worker hat den initialen Mutex bereits angelegt.
            db.expire_all()
    db.execute(update(InfoscreenReleaseDiscoveryLock).where(
        InfoscreenReleaseDiscoveryLock.id == 1).values(generation=InfoscreenReleaseDiscoveryLock.generation + 1))
    rows = db.query(InfoscreenRelease).with_for_update().all()
    row = next((r for r in rows if (r.version, r.channel, r.arch) == (version, channel, arch)), None)
    if row is not None:
        return row
    db.refresh(device, with_for_update=True)
    count = device.release_discovery_count if device.release_discovery_day == now.date() else 0
    known = [r.version for r in rows if r.channel == channel and r.arch == arch] + [device.agent_version or "0"]
    highest = max(known, key=cmp_to_key(releases.version_compare))
    if len(rows) >= MAX_RELEASES or count >= MAX_DISCOVERIES_PER_DAY or not plausible(version, highest):
        return None
    try:
        with db.begin_nested():
            row = InfoscreenRelease(version=version, channel=channel, arch=arch, rollout_percent=0, blocked=False)
            db.add(row)
            db.flush()
    except IntegrityError:
        return query.populate_existing().first()
    device.release_discovery_day, device.release_discovery_count = now.date(), count + 1
    return row


def window_status(window: dict, now: datetime) -> tuple[bool, int]:
    zone = ZoneInfo(window["timezone"])
    local = now.replace(tzinfo=UTC).astimezone(zone)
    start, end = time.fromisoformat(window["start"]), time.fromisoformat(window["end"])
    clock = local.time().replace(tzinfo=None)
    inside = start == end or (start <= clock < end if start < end else clock >= start or clock < end)
    next_start = datetime.combine(local.date(), start, zone)
    if next_start <= local:
        next_start += timedelta(days=1)
    return inside, max(1, int((next_start.astimezone(UTC) - now.replace(tzinfo=UTC)).total_seconds()))


def check(db: Session, device: InfoscreenDevice, version: str, channel: str, arch: str,
          current: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    # Ein schreibender Lock serialisiert die erstmalige Arch-Bindung auch unter SQLite.
    db.execute(update(InfoscreenDevice).where(InfoscreenDevice.id == device.id,
        InfoscreenDevice.org_id == device.org_id).values(last_update_check_at=now))
    db.refresh(device)
    policy = releases.update_policy(db, device, now)
    result = {key: policy[key] for key in
              ("maintenance_window", "critical_allowed", "check_interval_h", "pinned_version")}
    reason, retry = "unknown", None
    if channel == device.channel and (device.arch is None or arch == device.arch):
        # Die erste authentifizierte Meldung bindet die Architektur (Heartbeat hatte bisher kein Arch-Feld).
        if device.arch is None:
            device.arch = arch
            db.flush()
        if releases.version_compare(version, current) > 0:
            row = discover(db, device, version, channel, arch, current, now)
            if row is not None:
                if row.blocked:
                    reason = "blocked"
                elif device.update_locked:
                    reason = "locked"
                elif device.update_pinned_version and device.update_pinned_version != version:
                    reason = "pinned"
                elif not policy["update_allowed"]:
                    reason, retry = "incident", 300
                elif releases.rollout_bucket(device.id, version) >= row.rollout_percent:
                    reason = "rollout"
                else:
                    inside, wait = window_status(policy["maintenance_window"], now)
                    if not inside and not (policy["critical_allowed"] and row.critical):
                        reason, retry = "window", wait
                    else:
                        reason = "ok"
    device.last_update_check_at, device.last_update_check_reason = now, reason
    return {"allowed": reason == "ok", "reason": reason, "retry_after_s": retry, **result}
