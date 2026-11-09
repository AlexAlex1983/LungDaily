"""Slot clock: starts Pipeline runs on time, because GitHub's own cron is often hours late or skipped.

Runs inside the "Slot clock" workflow. It reads publishing.slots and publishing.write_times
(local time, publishing.timezone) from config/settings.yaml, sleeps until each one and starts the
Pipeline workflow (action publish-slot or write) with `gh workflow run`. After about 5h40m it
starts a fresh copy of itself and exits, so the clock keeps going without relying on cron
(an hourly cron in slot-clock.yml only restarts it if the chain ever breaks).

Safe to start twice: publish-slot only publishes when a slot is due and not yet used
(catch-up rule in process.publish_slot), and a duplicate write just tops the ready queue up.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUN_SECONDS = int(os.environ.get("CLOCK_RUN_SECONDS", 5 * 3600 + 40 * 60))
WRITE_GRACE_MIN = 50   # on start, run a write if one was due within this many minutes (gap between clocks)


def load():
    pub = yaml.safe_load((ROOT / "config/settings.yaml").read_text())["publishing"]
    tz = ZoneInfo(pub.get("timezone", "Europe/Berlin"))
    return tz, sorted(pub.get("slots") or []), sorted(pub.get("write_times") or [])


def events(tz, slots, writes, start, end):
    """(time, action) pairs between start and end, over today and tomorrow (local)."""
    out = []
    day0 = start.astimezone(tz).date()
    for d in (day0, day0 + dt.timedelta(days=1)):
        for action, times in (("write", writes), ("publish-slot", slots)):
            for hhmm in times:
                h, m = map(int, hhmm.split(":"))
                t = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
                if start < t <= end:
                    out.append((t, action))
    # write first when both fall on the same minute
    return sorted(out, key=lambda e: (e[0], e[1] != "write"))


def recent_write_due(tz, writes, now) -> bool:
    for hhmm in writes:
        h, m = map(int, hhmm.split(":"))
        local = now.astimezone(tz)
        t = local.replace(hour=h, minute=m, second=0, microsecond=0)
        if dt.timedelta(0) <= local - t <= dt.timedelta(minutes=WRITE_GRACE_MIN):
            return True
    return False


def dispatch(workflow: str, action: str | None = None):
    cmd = ["gh", "workflow", "run", workflow, "--ref", "main"]
    if action:
        cmd += ["-f", f"action={action}"]
    for attempt in range(3):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"{dt.datetime.now(dt.timezone.utc):%H:%M:%SZ} started {workflow} {action or ''}", flush=True)
            return
        print(f"dispatch failed ({attempt + 1}): {r.stderr.strip()}", flush=True)
        time.sleep(20)


def main():
    tz, slots, writes = load()
    start = dt.datetime.now(dt.timezone.utc)
    end = start + dt.timedelta(seconds=RUN_SECONDS)
    print(f"Clock {start:%Y-%m-%d %H:%M}Z → {end:%H:%M}Z; slots {slots}; writes {writes}", flush=True)

    # cover anything missed while no clock was running
    if recent_write_due(tz, writes, start):
        dispatch("pipeline.yml", "write")
    dispatch("pipeline.yml", "publish-slot")    # no-op unless a slot is due and unused

    plan = events(tz, slots, writes, start, end)
    for t, action in plan:
        print(f"  planned {t:%H:%M %Z} {action}", flush=True)
    for t, action in plan:
        wait = (t - dt.datetime.now(dt.timezone.utc)).total_seconds()
        if wait > 0:
            time.sleep(wait)
        dispatch("pipeline.yml", action)

    wait = (end - dt.datetime.now(dt.timezone.utc)).total_seconds()
    if wait > 0:
        time.sleep(wait)
    dispatch("slot-clock.yml")     # hand over to a fresh clock


if __name__ == "__main__":
    sys.exit(main())
