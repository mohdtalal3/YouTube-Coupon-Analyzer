#!/usr/bin/env python3
"""Flask frontend for YouTube Analyzer Bot"""

import uuid
from datetime import datetime

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash

from data_store import (
    load_workspaces, save_workspaces,
    load_jobs, save_jobs,
    get_logs,
    WORKSPACES_FILE, JOBS_FILE,
)
from job_runner import launch_scrape_job
import scheduler as sched_module

app = Flask(__name__)
app.secret_key = "yt-bot-dashboard-secret-2024"


# ── CONTEXT PROCESSOR ─────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    jobs = load_jobs()
    running_count = sum(1 for j in jobs if j.get("status") == "running")
    return {
        "all_workspaces": load_workspaces(),
        "running_jobs_count": running_count,
        "compute_next_run": sched_module.compute_next_run,
    }


# ── ROUTES: DASHBOARD ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    workspaces = load_workspaces()
    jobs = load_jobs()
    recent = sorted(jobs, key=lambda j: j.get("created_at", ""), reverse=True)[:10]
    stats = {
        "total_workspaces": len(workspaces),
        "total_jobs": len(jobs),
        "running": sum(1 for j in jobs if j.get("status") == "running"),
        "completed": sum(1 for j in jobs if j.get("status") == "completed"),
        "failed": sum(1 for j in jobs if j.get("status") == "failed"),
    }
    return render_template("index.html", workspaces=workspaces, recent_jobs=recent, stats=stats)


# ── ROUTES: WORKSPACES ────────────────────────────────────────────────────────

@app.route("/workspaces/new", methods=["GET", "POST"])
def workspace_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        page_id_raw = request.form.get("default_page_id", "").strip()
        image_prompt = request.form.get("image_prompt", "").strip()

        if not name:
            return render_template("workspace_form.html", workspace=None, error="Workspace name is required.")

        wp_url = request.form.get("wp_url", "").strip().rstrip("/")
        source_type = request.form.get("source_type", "none")
        ws = {
            "id": str(uuid.uuid4()),
            "name": name,
            "default_page_id": int(page_id_raw) if page_id_raw.isdigit() else None,
            "wp_url": wp_url or None,
            "image_prompt": image_prompt,
            "source_type": source_type,
            "publish_target": request.form.get("publish_target", "retailshout"),
            "created_at": datetime.now().isoformat(),
            "youtube_channels": [],
            "source_keywords": [],
            "schedule": {"enabled": False, "day": "saturday", "time": "08:00", "timezone": "UTC"},
            "schedule_state": {},
        }
        workspaces = load_workspaces()
        workspaces.append(ws)
        save_workspaces(workspaces)
        return redirect(url_for("workspace_detail", workspace_id=ws["id"]))

    return render_template("workspace_form.html", workspace=None, error=None)


@app.route("/workspaces/<workspace_id>")
def workspace_detail(workspace_id):
    workspaces = load_workspaces()
    ws = next((w for w in workspaces if w["id"] == workspace_id), None)
    if not ws:
        return redirect(url_for("index"))

    jobs = load_jobs()
    ws_jobs = sorted(
        [j for j in jobs if j.get("workspace_id") == workspace_id],
        key=lambda j: j.get("created_at", ""),
        reverse=True,
    )
    return render_template("workspace_detail.html", workspace=ws, jobs=ws_jobs)


@app.route("/workspaces/<workspace_id>/edit", methods=["GET", "POST"])
def workspace_edit(workspace_id):
    workspaces = load_workspaces()
    ws = next((w for w in workspaces if w["id"] == workspace_id), None)
    if not ws:
        return redirect(url_for("index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        page_id_raw = request.form.get("default_page_id", "").strip()
        if not name:
            return render_template("workspace_form.html", workspace=ws, error="Workspace name is required.")
        ws["name"] = name
        ws["default_page_id"] = int(page_id_raw) if page_id_raw.isdigit() else None
        ws["wp_url"] = request.form.get("wp_url", "").strip().rstrip("/") or None
        ws["image_prompt"] = request.form.get("image_prompt", "").strip()
        ws["source_type"] = request.form.get("source_type", "none")
        ws["publish_target"] = request.form.get("publish_target", "retailshout")
        save_workspaces(workspaces)
        return redirect(url_for("workspace_detail", workspace_id=workspace_id))

    return render_template("workspace_form.html", workspace=ws, error=None)


@app.route("/workspaces/<workspace_id>/delete", methods=["POST"])
def workspace_delete(workspace_id):
    workspaces = [w for w in load_workspaces() if w["id"] != workspace_id]
    save_workspaces(workspaces)
    return redirect(url_for("index"))


@app.route("/workspaces/<workspace_id>/run", methods=["POST"])
def workspace_run(workspace_id):
    workspaces = load_workspaces()
    ws = next((w for w in workspaces if w["id"] == workspace_id), None)
    if not ws:
        flash("Workspace not found.", "danger")
        return redirect(url_for("index"))

    # ── Parse form ──
    urls_raw = request.form.get("urls", "").strip()
    urls = [u.strip() for u in urls_raw.splitlines() if u.strip()]

    page_id_raw = request.form.get("page_id", "").strip()
    page_id = int(page_id_raw) if page_id_raw.isdigit() else ws.get("default_page_id")

    # checkbox: present = "on", absent = skip images
    generate_images_flag = request.form.get("generate_images", "off") == "on"
    scrape_source_flag = request.form.get("scrape_source", "off") == "on"
    publish_mode = request.form.get("publish_mode", "publish")

    product_limit_raw = request.form.get("product_limit", "").strip()
    product_limit = int(product_limit_raw) if product_limit_raw.isdigit() else None

    if not urls:
        flash("Please enter at least one YouTube URL.", "warning")
        return redirect(url_for("workspace_detail", workspace_id=workspace_id))

    job_id = launch_scrape_job(
        ws,
        urls=urls,
        page_id=page_id,
        generate_images_flag=generate_images_flag,
        scrape_source_flag=scrape_source_flag,
        publish_mode=publish_mode,
        product_limit=product_limit,
        triggered_by="manual",
    )

    return redirect(url_for("job_detail", job_id=job_id))


# ── ROUTES: JOBS ──────────────────────────────────────────────────────────────

@app.route("/jobs")
def jobs_list():
    jobs = sorted(load_jobs(), key=lambda j: j.get("created_at", ""), reverse=True)
    return render_template("jobs.html", jobs=jobs)


@app.route("/jobs/<job_id>")
def job_detail(job_id):
    jobs = load_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return redirect(url_for("jobs_list"))
    return render_template("job_detail.html", job=job)


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/jobs/<job_id>/logs")
def api_job_logs(job_id):
    logs = get_logs(job_id)
    jobs = load_jobs()
    job = next((j for j in jobs if j["id"] == job_id), {"status": "unknown"})
    return jsonify({"logs": logs, "status": job["status"]})


@app.route("/api/jobs/<job_id>/status")
def api_job_status(job_id):
    jobs = load_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "status": job["status"],
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
    })


@app.route("/api/stats")
def api_stats():
    jobs = load_jobs()
    return jsonify({
        "total": len(jobs),
        "pending": sum(1 for j in jobs if j.get("status") == "pending"),
        "running": sum(1 for j in jobs if j.get("status") == "running"),
        "completed": sum(1 for j in jobs if j.get("status") == "completed"),
        "failed": sum(1 for j in jobs if j.get("status") == "failed"),
    })


# ── ROUTES: CHANNEL SCHEDULE ──────────────────────────────────────────────────

@app.route("/workspaces/<workspace_id>/schedule", methods=["POST"])
def workspace_schedule_save(workspace_id):
    workspaces = load_workspaces()
    ws = next((w for w in workspaces if w["id"] == workspace_id), None)
    if not ws:
        flash("Workspace not found.", "danger")
        return redirect(url_for("index"))

    enabled = request.form.get("schedule_enabled", "off") == "on"
    day = request.form.get("schedule_day", "saturday").strip().lower()
    time_str = request.form.get("schedule_time", "08:00").strip()
    tz_name = request.form.get("schedule_timezone", "UTC").strip()

    channels_raw = request.form.get("youtube_channels", "").strip()
    channels = [c.strip() for c in channels_raw.splitlines() if c.strip()]

    keywords_raw = request.form.get("source_keywords", "").strip()
    keywords = [k.strip() for k in keywords_raw.replace("\n", ",").split(",") if k.strip()]

    generate_images = request.form.get("schedule_generate_images", "off") == "on"
    scrape_source = request.form.get("schedule_scrape_source", "off") == "on"

    ws["youtube_channels"] = channels
    ws["source_keywords"] = keywords
    ws["schedule"] = {
        "enabled": enabled,
        "day": day,
        "time": time_str,
        "timezone": tz_name,
        "generate_images": generate_images,
        "scrape_source": scrape_source,
    }
    save_workspaces(workspaces)
    flash("Schedule saved.", "success")
    return redirect(url_for("workspace_detail", workspace_id=workspace_id))


@app.route("/workspaces/<workspace_id>/schedule/run-now", methods=["POST"])
def workspace_schedule_run_now(workspace_id):
    workspaces = load_workspaces()
    ws = next((w for w in workspaces if w["id"] == workspace_id), None)
    if not ws:
        flash("Workspace not found.", "danger")
        return redirect(url_for("index"))
    if not ws.get("youtube_channels"):
        flash("Add at least one YouTube channel URL before running a scan.", "warning")
        return redirect(url_for("workspace_detail", workspace_id=workspace_id))

    job_id = sched_module.run_now(workspace_id)
    if job_id:
        flash("Scan complete — redirecting to job logs.", "success")
        return redirect(url_for("job_detail", job_id=job_id))
    flash("Scan ran but no new matching videos found (or scan already in progress).", "info")
    return redirect(url_for("workspace_detail", workspace_id=workspace_id))


@app.route("/workspaces/<workspace_id>/schedule/disable", methods=["POST"])
def workspace_schedule_disable(workspace_id):
    workspaces = load_workspaces()
    ws = next((w for w in workspaces if w["id"] == workspace_id), None)
    if not ws:
        flash("Workspace not found.", "danger")
        return redirect(url_for("index"))

    schedule = ws.get("schedule") or {}
    schedule["enabled"] = False
    ws["schedule"] = schedule
    save_workspaces(workspaces)
    flash("Schedule disabled.", "info")
    return redirect(url_for("workspace_detail", workspace_id=workspace_id))


# ── ROUTES: SETTINGS ──────────────────────────────────────────────────────────

@app.route("/settings")
def settings():
    workspaces_raw = WORKSPACES_FILE.read_text(encoding="utf-8")
    jobs_raw = JOBS_FILE.read_text(encoding="utf-8")
    return render_template("settings.html", workspaces_raw=workspaces_raw, jobs_raw=jobs_raw)


@app.route("/settings/save", methods=["POST"])
def settings_save():
    target = request.form.get("target", "")
    content = request.form.get("content", "")

    try:
        import json as _json
        parsed = _json.loads(content)
    except Exception as e:
        flash(f"Invalid JSON: {e}", "danger")
        return redirect(url_for("settings"))

    if target == "workspaces":
        save_workspaces(parsed)
        flash("workspaces.json saved.", "success")
    elif target == "jobs":
        save_jobs(parsed)
        flash("jobs.json saved.", "success")
    else:
        flash("Unknown target.", "danger")

    return redirect(url_for("settings"))


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  YouTube Bot Dashboard")
    print("  http://localhost:5007")
    print("=" * 50)
    sched_module.init_scheduler()
    app.run(debug=False,host="0.0.0.0", port=5007, use_reloader=False)
