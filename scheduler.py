#!/usr/bin/env python3
"""
Channel schedule watcher.

Runs a lightweight APScheduler job every second that only re-reads
workspaces.json and (re)registers a per-workspace CronTrigger job whenever
that workspace's schedule config changed. YouTube itself is NEVER touched
by this per-second tick — it is only touched by the CronTrigger job when
the configured day/time is actually due (or via "Run Now").

Each workspace's schedule config lives in:
    workspace["schedule"] = {
        "enabled": bool,
        "day": "saturday",
        "time": "08:00",
        "timezone": "Asia/Karachi",
    }
    workspace["youtube_channels"] = ["https://www.youtube.com/@handle", ...]
    workspace["source_keywords"] = ["meijer"]
    workspace["schedule_state"] = {
        "last_run_at": iso str,
        "last_run_status": "ok" | "no_channels" | "partial_error" | "error",
        "last_matched_count": int,
        "last_job_id": str | None,
        "last_errors": [str],
        "processed_video_ids": [str],
    }
"""

import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, available_timezones

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from channel_fetcher import (
    WEEKDAYS,
    get_playlist_videos_between,
    get_previous_weekday_window,
    resolve_channel,
)
from data_store import append_log, load_workspaces, update_workspace
from job_runner import launch_scrape_job

_scheduler = BackgroundScheduler(timezone="UTC")
_job_signatures: dict[str, tuple] = {}   # workspace_id -> (day, hour, minute, tz) currently registered

_scanning_lock = threading.Lock()
_scanning: set[str] = set()               # workspace_ids currently being scanned (overlap guard)


# ── HELPERS ───────────────────────────────────────────────────────────────────

_TZ_ALIASES = {
    "PKT": "Asia/Karachi",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "EST": "America/New_York",
    "EDT": "America/New_York",
}


def _safe_tz(tz_name: str) -> str:
    tz_name = _TZ_ALIASES.get(tz_name, tz_name)
    return tz_name if tz_name in available_timezones() else "UTC"


def _parse_time(time_str: str) -> tuple[int, int]:
    try:
        hour, minute = time_str.strip().split(":")
        return int(hour), int(minute)
    except Exception:
        return 8, 0


def compute_next_run(schedule: dict | None, now: datetime | None = None) -> datetime | None:
    """Pure calculation of the next fire time for display purposes only."""
    if not schedule or not schedule.get("enabled"):
        return None
    day_name = (schedule.get("day") or "saturday").lower()
    if day_name not in WEEKDAYS:
        return None
    hour, minute = _parse_time(schedule.get("time") or "08:00")
    tz = ZoneInfo(_safe_tz(schedule.get("timezone") or "UTC"))

    now = now.astimezone(tz) if now else datetime.now(tz)
    target_weekday = WEEKDAYS[day_name]
    days_ahead = (target_weekday - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _match_keywords(video: dict, channel_name: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = " ".join([
        video.get("title") or "",
        video.get("description") or "",
        channel_name or "",
    ]).lower()
    return any(kw in haystack for kw in keywords)


def _which_keyword_matched(video: dict, channel_name: str, keywords: list[str]) -> str:
    """Return the first keyword that matched, or 'all' if no keywords configured."""
    if not keywords:
        return "all"
    haystack = " ".join([
        video.get("title") or "",
        video.get("description") or "",
        channel_name or "",
    ]).lower()
    for kw in keywords:
        if kw in haystack:
            return kw
    return "?"


def _log_scan_only(workspace_id: str, lines: list[str]):
    """Write scan info to a standalone log file when no job was created."""
    from data_store import LOGS_DIR
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"scan_{workspace_id[:8]}_{ts}.log"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[scheduler] Scan log written to {log_file.name}")


# ── SCAN (heavy job — only runs when due, or via Run Now) ───────────────────

def _do_scan(workspace_id: str, manual: bool = False):
    workspaces = load_workspaces()
    ws = next((w for w in workspaces if w["id"] == workspace_id), None)
    if not ws:
        return

    schedule = ws.get("schedule") or {}
    day_name = (schedule.get("day") or "saturday").lower()
    channels = ws.get("youtube_channels") or []
    keywords = [k.strip().lower() for k in (ws.get("source_keywords") or []) if k.strip()]
    gen_images = schedule.get("generate_images", True)
    scrape_source = schedule.get("scrape_source", True)

    now_utc = datetime.now(timezone.utc)
    existing_state = ws.get("schedule_state") or {}

    scan_log_lines = []
    scan_log_lines.append(f"{'━' * 60}")
    scan_log_lines.append(f"📋 CHANNEL SCAN — {ws['name']}")
    scan_log_lines.append(f"   Triggered by: {'Run Now (manual)' if manual else 'Schedule'}")
    scan_log_lines.append(f"   Target weekday: {day_name}")
    scan_log_lines.append(f"   Keywords: {keywords if keywords else '(none — match all)'}")
    scan_log_lines.append(f"   Channels: {len(channels)}")
    scan_log_lines.append(f"   AI Images: {'ON' if gen_images else 'OFF'}")
    scan_log_lines.append(f"   Scrape Source: {'ON' if scrape_source else 'OFF'}")
    scan_log_lines.append("")

    if not channels:
        scan_log_lines.append("⚠️  No channels configured — nothing to scan.")
        update_workspace(workspace_id, {
            "schedule_state": {**existing_state, "last_run_at": now_utc.isoformat(), "last_run_status": "no_channels"},
        })
        # Still log to a standalone scan log file so user can see what happened
        _log_scan_only(workspace_id, scan_log_lines)
        return

    all_videos = []
    errors = []
    for ch_url in channels:
        try:
            channel = resolve_channel(ch_url)
            start_date, end_date = get_previous_weekday_window(day_name, now=now_utc)
            videos = get_playlist_videos_between(
                uploads_playlist_id=channel["uploads_playlist_id"],
                start_date=start_date,
                end_date=end_date,
            )
            for v in videos:
                v["channel_name"] = channel["channel_name"]
            all_videos.extend(videos)
            scan_log_lines.append(f"📡 {channel['channel_name']}: {len(videos)} videos found ({start_date.date()} → {end_date.date()})")
        except Exception as e:
            errors.append(f"{ch_url}: {e}")
            scan_log_lines.append(f"❌ {ch_url}: {e}")

    processed_ids = set(existing_state.get("processed_video_ids") or [])

    matched = []
    seen_ids = set()
    skipped_processed = 0
    skipped_no_match = 0
    for v in all_videos:
        vid = v.get("video_id")
        if not vid or vid in processed_ids or vid in seen_ids:
            if vid and vid in processed_ids:
                skipped_processed += 1
            continue
        if not _match_keywords(v, v.get("channel_name", ""), keywords):
            skipped_no_match += 1
            continue
        seen_ids.add(vid)
        matched.append(v)
        matched_kw = _which_keyword_matched(v, v.get("channel_name", ""), keywords)
        scan_log_lines.append(f"  ✅ MATCHED: {v.get('title','?')[:60]}  [{v.get('video_id')}]  (keyword: {matched_kw})")
        scan_log_lines.append(f"     URL: {v.get('url','')}")

    scan_log_lines.append("")
    scan_log_lines.append(f"📊 Summary: {len(all_videos)} fetched, {len(matched)} matched, {skipped_processed} already processed, {skipped_no_match} no keyword match")

    job_id = None
    if matched:
        urls = [v["url"] for v in matched]
        scan_log_lines.append(f"🚀 Launching scrape job with {len(urls)} URL(s):")
        for u in urls:
            scan_log_lines.append(f"   • {u}")
        scan_log_lines.append("")
        job_id = launch_scrape_job(
            ws,
            urls=urls,
            generate_images_flag=gen_images,
            scrape_source_flag=scrape_source and ws.get("source_type", "none") != "none",
            publish_mode="publish",
            product_limit=None,
            triggered_by="manual-run-now" if manual else "schedule",
        )
        # Prepend scan info to the job's log so user can see what was fetched and why
        for line in scan_log_lines:
            append_log(job_id, line)
    else:
        scan_log_lines.append("ℹ️  No new matching videos found — nothing to scrape.")
        _log_scan_only(workspace_id, scan_log_lines)

    new_processed = list(processed_ids | seen_ids)
    new_processed = new_processed[-1000:]  # cap unbounded growth

    update_workspace(workspace_id, {
        "schedule_state": {
            **existing_state,
            "last_run_at": now_utc.isoformat(),
            "last_run_status": "ok" if not errors else "partial_error",
            "last_matched_count": len(matched),
            "last_job_id": job_id,
            "processed_video_ids": new_processed,
            "last_errors": errors,
        },
    })


def scan_workspace(workspace_id: str, manual: bool = False):
    """Entry point invoked by CronTrigger (scheduled) or Run Now (manual)."""
    with _scanning_lock:
        if workspace_id in _scanning:
            print(f"[scheduler] Scan already running for workspace {workspace_id}, skipping")
            return
        _scanning.add(workspace_id)
    try:
        _do_scan(workspace_id, manual=manual)
    except Exception as e:
        print(f"[scheduler] Scan error for workspace {workspace_id}: {e}")
    finally:
        with _scanning_lock:
            _scanning.discard(workspace_id)


def run_now(workspace_id: str) -> str | None:
    """Run a scan synchronously and return the job_id (if any) so the caller can redirect.

    Used by the 'Run Now' button. The scan itself is fast (YouTube API metadata only);
    the actual scraping runs in its own background thread via launch_scrape_job.
    """
    ws = next((w for w in load_workspaces() if w["id"] == workspace_id), None)
    if not ws:
        return None

    # Run scan synchronously — overlap guard still applies
    with _scanning_lock:
        if workspace_id in _scanning:
            print(f"[scheduler] Scan already running for workspace {workspace_id}, skipping")
            return None
        _scanning.add(workspace_id)
    try:
        _do_scan(workspace_id, manual=True)
    except Exception as e:
        print(f"[scheduler] Scan error for workspace {workspace_id}: {e}")
        return None
    finally:
        with _scanning_lock:
            _scanning.discard(workspace_id)

    # Read back the job_id that _do_scan stored
    ws = next((w for w in load_workspaces() if w["id"] == workspace_id), None)
    if ws:
        state = ws.get("schedule_state") or {}
        return state.get("last_job_id")
    return None


# ── WATCHER (runs every second — cheap, no network calls) ───────────────────

def _watcher_tick():
    try:
        workspaces = load_workspaces()
    except Exception:
        return

    current_ids = set()

    for ws in workspaces:
        wsid = ws["id"]
        schedule = ws.get("schedule") or {}
        job_id = f"scan_{wsid}"

        if not schedule.get("enabled"):
            if _scheduler.get_job(job_id):
                _scheduler.remove_job(job_id)
            _job_signatures.pop(wsid, None)
            continue

        day_name = (schedule.get("day") or "saturday").lower()
        if day_name not in WEEKDAYS:
            continue
        hour, minute = _parse_time(schedule.get("time") or "08:00")
        tz_name = _safe_tz(schedule.get("timezone") or "UTC")

        current_ids.add(wsid)
        signature = (day_name, hour, minute, tz_name)

        if _job_signatures.get(wsid) == signature and _scheduler.get_job(job_id):
            continue  # unchanged — nothing to do

        day_abbr = day_name[:3]  # 'saturday' -> 'sat'
        trigger = CronTrigger(day_of_week=day_abbr, hour=hour, minute=minute, timezone=tz_name)
        _scheduler.add_job(
            scan_workspace,
            trigger=trigger,
            id=job_id,
            args=[wsid],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        _job_signatures[wsid] = signature

    # Remove jobs for workspaces that were deleted
    for job in list(_scheduler.get_jobs()):
        if job.id.startswith("scan_"):
            wsid = job.id[len("scan_"):]
            if wsid not in current_ids:
                _scheduler.remove_job(job.id)
                _job_signatures.pop(wsid, None)


def init_scheduler():
    """Start the background scheduler. Safe to call multiple times."""
    if _scheduler.running:
        return
    _scheduler.add_job(
        _watcher_tick,
        trigger=IntervalTrigger(seconds=1),
        id="workspace_schedule_watcher",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    # Restore schedules immediately on startup without waiting for the first tick
    _watcher_tick()
