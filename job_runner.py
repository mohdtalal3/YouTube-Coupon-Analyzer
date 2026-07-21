#!/usr/bin/env python3
"""
Reusable scrape-job launcher.

Builds the run_full.py CLI command, creates a job record, and starts it in
a background thread. Used by both the manual "Run Bot" form (app.py) and
the channel-schedule scanner (scheduler.py) so there is a single scraping
pipeline entry point.
"""

import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

from data_store import BASE_DIR, DATA_DIR, append_log, load_jobs, save_jobs, update_job


def run_job_background(job_id: str, cmd: list, env: dict, prompt_file=None):
    """Run the pipeline subprocess in a background daemon thread."""
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(BASE_DIR),
        )

        update_job(job_id, {"pid": process.pid, "status": "running"})

        for line in iter(process.stdout.readline, ""):
            stripped = line.rstrip()
            if stripped:
                ts = datetime.now().strftime("%H:%M:%S")
                append_log(job_id, f"[{ts}] {stripped}")

        process.wait()
        ts = datetime.now().strftime("%H:%M:%S")

        if process.returncode == 0:
            update_job(job_id, {"status": "completed", "finished_at": datetime.now().isoformat()})
            append_log(job_id, f"[{ts}] ✅ Pipeline completed successfully")
        else:
            update_job(job_id, {"status": "failed", "finished_at": datetime.now().isoformat()})
            append_log(job_id, f"[{ts}] ❌ Pipeline failed (exit code {process.returncode})")

    except Exception as exc:
        ts = datetime.now().strftime("%H:%M:%S")
        update_job(job_id, {"status": "failed", "finished_at": datetime.now().isoformat()})
        append_log(job_id, f"[{ts}] ❌ Exception: {exc}")
    finally:
        if prompt_file and prompt_file.exists():
            prompt_file.unlink()


def launch_scrape_job(
    ws: dict,
    urls: list[str],
    page_id: int | None = None,
    generate_images_flag: bool = True,
    scrape_source_flag: bool = True,
    publish_mode: str = "publish",
    product_limit: int | None = None,
    triggered_by: str = "manual",
) -> str:
    """Create a job record for `ws` and start run_full.py in the background.

    Returns the new job_id. Shared by the manual "Run Bot" form and the
    scheduled channel scanner — this is the single scraping pipeline entry
    point (do not duplicate this logic elsewhere).
    """
    if page_id is None:
        page_id = ws.get("default_page_id")

    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "workspace_id": ws["id"],
        "workspace_name": ws["name"],
        "urls": urls,
        "page_id": page_id,
        "wp_url": ws.get("wp_url"),
        "generate_images": generate_images_flag,
        "scrape_source": scrape_source_flag,
        "source_type": ws.get("source_type", "none"),
        "publish_mode": publish_mode,
        "product_limit": product_limit,
        "status": "pending",
        "triggered_by": triggered_by,
        "created_at": datetime.now().isoformat(),
        "finished_at": None,
        "pid": None,
    }
    jobs = load_jobs()
    jobs.append(job)
    save_jobs(jobs)

    # ── Build CLI command ──
    cmd = [sys.executable, str(BASE_DIR / "run_full.py"), "--urls"] + urls

    if page_id:
        cmd += ["--page-id", str(page_id), f"--{publish_mode}"]
    else:
        cmd.append("--no-publish")

    if not generate_images_flag:
        cmd.append("--no-images")

    if scrape_source_flag and ws.get("source_type", "none") != "none":
        cmd += ["--source", ws["source_type"]]

    publish_target = ws.get("publish_target", "retailshout")
    cmd += ["--publish-target", publish_target]

    if product_limit:
        cmd += ["--product-limit", str(product_limit)]

    # Write workspace image prompt to temp file if provided
    ws_prompt = ws.get("image_prompt", "").strip() if ws.get("image_prompt") else ""
    prompt_file = None
    if ws_prompt:
        prompt_file = DATA_DIR / f"prompt_{job_id}.txt"
        prompt_file.write_text(ws_prompt, encoding="utf-8")
        cmd += ["--prompt-file", str(prompt_file)]

    # ── Write startup log entries ──
    env = os.environ.copy()
    ts = datetime.now().strftime("%H:%M:%S")
    append_log(job_id, f"[{ts}] ── Job {job_id[:8]}... created ({triggered_by}) ──────────────")
    append_log(job_id, f"[{ts}] Workspace  : {ws['name']}")
    append_log(job_id, f"[{ts}] Page ID    : {page_id or 'not set (local only)'}")
    append_log(job_id, f"[{ts}] Publish    : {publish_mode}")
    append_log(job_id, f"[{ts}] AI Images  : {'yes' if generate_images_flag else 'no'}")
    append_log(job_id, f"[{ts}] Source     : {ws.get('source_type', 'none').upper()}")
    append_log(job_id, f"[{ts}] Limit      : {product_limit or 'all products'}")
    append_log(job_id, f"[{ts}] URLs ({len(urls)}):")
    for u in urls:
        append_log(job_id, f"[{ts}]   {u}")
    append_log(job_id, f"[{ts}] ────────────────────────────────────────────────────")

    # Force unbuffered stdout so lines stream in real time from the subprocess
    env["PYTHONUNBUFFERED"] = "1"

    # ── Start daemon thread ──
    t = threading.Thread(target=run_job_background, args=(job_id, cmd, env, prompt_file), daemon=True)
    t.start()

    return job_id
