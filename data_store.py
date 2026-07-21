#!/usr/bin/env python3
"""
Shared data-access layer for workspaces.json and jobs.json.

Extracted from app.py so both the Flask app and the background scheduler
(scheduler.py) can read/write the same files safely without importing
app.py itself (which would re-run the Flask app as a side effect).
"""

import json
import threading
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"
WORKSPACES_FILE = DATA_DIR / "workspaces.json"
JOBS_FILE = DATA_DIR / "jobs.json"

# Ensure data directories and files exist on import
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
if not WORKSPACES_FILE.exists():
    WORKSPACES_FILE.write_text("[]", encoding="utf-8")
if not JOBS_FILE.exists():
    JOBS_FILE.write_text("[]", encoding="utf-8")

_lock = threading.Lock()


# ── WORKSPACES ──────────────────────────────────────────────────────────────

def load_workspaces() -> list:
    with _lock:
        return json.loads(WORKSPACES_FILE.read_text(encoding="utf-8"))


def save_workspaces(data: list):
    with _lock:
        WORKSPACES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def update_workspace(workspace_id: str, updates: dict):
    """Atomically merge `updates` into the workspace dict with the given id."""
    with _lock:
        workspaces = json.loads(WORKSPACES_FILE.read_text(encoding="utf-8"))
        for ws in workspaces:
            if ws["id"] == workspace_id:
                ws.update(updates)
                break
        WORKSPACES_FILE.write_text(json.dumps(workspaces, indent=2, ensure_ascii=False), encoding="utf-8")


# ── JOBS ────────────────────────────────────────────────────────────────────

def load_jobs() -> list:
    with _lock:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))


def save_jobs(data: list):
    with _lock:
        JOBS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def update_job(job_id: str, updates: dict):
    with _lock:
        jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        for job in jobs:
            if job["id"] == job_id:
                job.update(updates)
                break
        JOBS_FILE.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


# ── LOGS ────────────────────────────────────────────────────────────────────

def append_log(job_id: str, line: str):
    log_file = LOGS_DIR / f"{job_id}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_logs(job_id: str) -> str:
    log_file = LOGS_DIR / f"{job_id}.log"
    if log_file.exists():
        return log_file.read_text(encoding="utf-8")
    return ""
