#!/usr/bin/env python3
"""
Full end-to-end YouTube → WordPress pipeline.

Steps:
  1. Search YouTube for the newest N videos matching a keyword
  2. Fetch transcript for each video
  3. Extract products with AI (chained via previous_response_id — deduplication across videos)
  4. Take screenshots at product timestamps
  5. Generate AI store images (KIE AI)
  6. Publish aggregated products to WordPress

Usage:
  python run_full.py --keyword "costco" --max-videos 10 --page-id 12345 --publish
  python run_full.py --keyword "costco" --max-videos 5  --page-id 12345 --draft
  python run_full.py --keyword "sam's club" --max-videos 10 --page-id 99999 --publish
"""

import argparse
import csv
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from youtube_search import fetch_videos
from main import fetch_transcript, extract_video_id
from analyze import extract_coupons
from image_fetcher import fetch_coupon_images
from generate import generate_images


# ── HELPERS ───────────────────────────────────────────────────────────────────

def print_section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Full YouTube search → product extraction → WordPress publish pipeline"
    )
    parser.add_argument("--keyword",    default="costco",  help="YouTube search keyword (default: costco)")
    parser.add_argument("--max-videos", type=int, default=10, help="Number of videos to process (default: 10)")
    parser.add_argument("--urls",       nargs="+", help="Provide your own YouTube URLs instead of searching (space-separated)")
    parser.add_argument("--page-id",    type=int, default=None, help="WordPress page ID to publish to (omit to skip publishing)")
    parser.add_argument("--title",      help="Override WordPress page title (optional)")

    parser.add_argument("--no-publish", action="store_true", help="Skip WordPress publishing entirely (just save files locally)")
    parser.add_argument("--no-images",     action="store_true", help="Skip AI image generation step")
    parser.add_argument("--product-limit", type=int, default=None, help="Max products to keep per video after AI extraction (default: all)")
    parser.add_argument("--prompt-file",   help="Path to custom image prompt text file (overrides prompt.txt)")
    parser.add_argument("--source",        default=None, help="Source website to scrape full product details and images from (e.g. meijer). If omitted, images only are fetched from Meijer.")
    parser.add_argument("--publish-target", default="retailshout", choices=["retailshout","aos"], help="Which WordPress site to publish to (retailshout or aos)")

    status_group = parser.add_mutually_exclusive_group()
    status_group.add_argument("--publish", action="store_true", help="Publish live to WordPress")
    status_group.add_argument("--draft",   action="store_true", help="Save as draft in WordPress")

    args = parser.parse_args()

    bot_dir = Path(__file__).parent

    print_section(f"YouTube Coupon Pipeline")
    print(f"  Mode       : {'Custom URLs' if args.urls else 'YouTube Search'}")
    if not args.urls:
        print(f"  Keyword    : {args.keyword}")
        print(f"  Max Videos : {args.max_videos}")
    print(f"  Source     : {args.source.upper() if args.source else 'Meijer (images only)'}")
    print(f"  Page ID    : {args.page_id}")
    print(f"  Status     : {'PUBLISH (Live)' if args.publish else 'DRAFT'}")

    # ── STEP 1: Get videos (search OR custom URLs) ────────────────────────────
    if args.urls:
        print_section("Step 1/5 — Using provided URLs")
        videos = []
        for url in args.urls:
            vid = extract_video_id(url)
            videos.append({
                "youtube_url": url,
                "video_id": vid,
                "title": vid,
                "duration": "?",
            })
    else:
        print_section("Step 1/5 — Searching YouTube")
        videos = fetch_videos(keyword=args.keyword, max_videos=args.max_videos)

    if not videos:
        print("❌ No videos found. Exiting.")
        sys.exit(1)

    print(f"  Found {len(videos)} videos:")
    for i, v in enumerate(videos, 1):
        print(f"  [{i}] {v.get('title', v['video_id'])} ({v.get('duration', '?')}) — {v['youtube_url']}")

    # Run directory named after keyword/urls + first video id
    if args.urls:
        safe_keyword = "custom"
    else:
        safe_keyword = args.keyword.replace(" ", "_").replace("'", "")
    first_video_id = videos[0]["video_id"]
    runs_root = bot_dir / "runs"
    runs_root.mkdir(exist_ok=True)
    run_dir = runs_root / f"run_{safe_keyword}_{first_video_id}"
    if run_dir.exists():
        print(f"  🗑️  Existing run folder found — deleting for fresh start: {run_dir.name}")
        shutil.rmtree(run_dir)
    run_dir.mkdir()

    SCREENSHOTS_DIR   = run_dir / "screenshots"
    GENERATED_DIR     = run_dir / "generated"
    SOURCE_IMAGES_DIR = run_dir / "source_images"
    ALL_PRODUCTS_FILE = run_dir / "products.json"
    CSV_FILE          = run_dir / "products.csv"
    RESPONSE_ID_FILE  = run_dir / "last_response_id.txt"

    # Save video list for reference
    with open(run_dir / "videos.json", "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)

    # TEMP: exit here for testing — remove later
    print("\n🛑 TEMP BREAK: Videos fetched and saved. Exiting before transcription.\n")
    sys.exit(0)

    # ── STEP 2 + 3: Transcripts + AI Extraction (chained) ────────────────────
    print_section("Steps 2+3 — Transcripts & AI Product Extraction")

    all_products = []
    previous_response_id = None

    for idx, video in enumerate(videos, start=1):
        url       = video["youtube_url"]
        video_id  = video["video_id"]
        video_dir = run_dir / f"video_{idx:02d}_{video_id}"
        video_dir.mkdir(exist_ok=True)

        transcript_file = str(video_dir / "transcript.json")
        products_file   = str(video_dir / "products.json")

        print(f"\n[{idx}/{len(videos)}] {video['title']}")
        print(f"  URL: {url}")

        print(f"  [2] Fetching transcript...")
        transcript_success = False
        for attempt in range(1, 4):
            try:
                fetch_transcript(url, output_file=transcript_file)
                transcript_success = True
                break
            except Exception as e:
                if attempt < 3:
                    print(f"  ⚠️  Attempt {attempt}/3 failed: {e}")
                    print(f"  Retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    print(f"  ⚠️  Transcript failed after 3 attempts for {url}: {e} — skipping video")

        if not transcript_success:
            if video_dir.exists():
                shutil.rmtree(video_dir)
                print(f"  🗑️  Deleted {video_dir.name}")
            continue

        print(f"  [3] Extracting coupon deals (previous_response_id={previous_response_id})...")
        try:
            _, response_id = extract_coupons(
                transcript_file,
                video_id,
                output_file=products_file,
                previous_response_id=previous_response_id,
                limit=args.product_limit,
            )
            previous_response_id = response_id
        except Exception as e:
            print(f"  ⚠️  Coupon extraction failed for {url}: {e} — skipping video")
            continue

        with open(products_file, "r", encoding="utf-8") as f:
            video_products = json.load(f)

        for p in video_products:
            p["video_id"]  = video_id
            p["video_url"] = url
            p["video_title"] = video.get("title", "")

        all_products.extend(video_products)
        print(f"  → {len(video_products)} new coupon deals (running total: {len(all_products)})")

    if previous_response_id:
        RESPONSE_ID_FILE.write_text(previous_response_id)
        print(f"\n  Final response ID saved → {RESPONSE_ID_FILE.name}")

    if not all_products:
        print("❌ No products extracted from any video. Exiting.")
        sys.exit(1)

    with open(ALL_PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_products, f, ensure_ascii=False, indent=2)

    print(f"\n  Total unique products across all videos: {len(all_products)}")

    # ── STEP 3b: Fetch Images / Full Source Data ──────────────────────────────
    # Sources that provide FULL deal details (price, description, image) from website.
    # Details from YouTube are REPLACED by website data.
    _FULL_SCRAPE_SOURCES = {
        # "heb": ("scrapers.heb", "HEBSearcher"),
    }

    # Sources that provide IMAGE + DESCRIPTION from website.
    # Name, price, and strategy are kept from YouTube; image and description come from the source.
    _IMAGE_DESC_SOURCES = {
        "heb": ("scrapers.heb", "HEBSearcher"),
    }

    # Sources used for IMAGE ONLY — all deal details still come from YouTube.
    _IMAGE_ONLY_SOURCES = {
        "meijer": ("scrapers.meijer", "MeijerSearcher"),
    }

    _source = args.source or "meijer"
    _proxy  = os.getenv("STATIC_PROXY")
    SOURCE_IMAGES_DIR.mkdir(exist_ok=True)

    _all_known = {**_FULL_SCRAPE_SOURCES, **_IMAGE_DESC_SOURCES, **_IMAGE_ONLY_SOURCES}
    if _source not in _all_known:
        print(f"❌ Unknown source: {_source!r}. Known: {list(_all_known)}")
        sys.exit(1)

    if _source in _FULL_SCRAPE_SOURCES:
        # ── Full scrape: price, description & image all come from the website ─
        print_section(f"Step 3b — Full scrape from {_source.upper()} ({len(all_products)} deals, 5 workers)")

        _mod_name, _cls_name = _FULL_SCRAPE_SOURCES[_source]
        SourceSearcher = getattr(importlib.import_module(_mod_name), _cls_name)

        _thread_local = threading.local()

        def _get_searcher():
            if not hasattr(_thread_local, "searcher"):
                _thread_local.searcher = SourceSearcher(proxy=_proxy)
                _thread_local.searcher.warmup()
            return _thread_local.searcher

        def _scrape_one(product):
            name = product.get("name", "").strip()
            if not name:
                return product
            print(f"  → {name}")
            try:
                result = _get_searcher().search(name)
                if result:
                    product["name"]        = result["name"] or name
                    product["brand"]       = result.get("brand") or ""
                    product["source_url"]  = result.get("product_url") or ""
                    product["source_size"] = result.get("size") or ""
                    price_str = result.get("price")
                    if price_str:
                        try:
                            product["price"] = float(price_str.replace("$", "").replace(",", "").strip())
                        except ValueError:
                            product["price"] = price_str
                    if result.get("description"):
                        product["details"] = result["description"]
                    img_url = result.get("image_url")
                    if img_url:
                        safe = re.sub(r"[^\w\-]", "_", name)[:60]
                        img_path = SOURCE_IMAGES_DIR / f"{safe}.jpg"
                        try:
                            with urllib.request.urlopen(img_url, timeout=15) as resp:
                                with open(img_path, "wb") as fh:
                                    fh.write(resp.read())
                            product["screenshot_file"] = str(img_path)
                            print(f"    ✓ image saved")
                        except Exception as ie:
                            print(f"    ⚠️  image download failed: {ie}")
                else:
                    print(f"    ⚠️  no result found")
            except Exception as e:
                print(f"    ⚠️  scrape error: {e}")
            return product

        _limit = args.product_limit
        found_products = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_scrape_one, p): p for p in all_products}
            for fut in as_completed(futures):
                result = fut.result()
                if result.get("source_url"):
                    found_products.append(result)
                    if _limit and len(found_products) >= _limit:
                        for f in futures:
                            f.cancel()
                        print(f"  ✂️  Reached limit of {_limit} — stopping early")
                        break
        all_products = found_products
        print(f"\n  ✓ Scraped {len(all_products)} deals from {_source.upper()}" +
              (f" (limit={_limit})" if _limit else ""))

    elif _source in _IMAGE_DESC_SOURCES:
        # ── Image + Description: image and description from website, name/price/strategy from YouTube ─
        print_section(f"Step 3b — Fetching images + descriptions from {_source.upper()} (name/price from YouTube)")

        _mod_name, _cls_name = _IMAGE_DESC_SOURCES[_source]
        SourceSearcher = getattr(importlib.import_module(_mod_name), _cls_name)

        _thread_local = threading.local()

        def _get_searcher_desc():
            if not hasattr(_thread_local, "searcher"):
                _thread_local.searcher = SourceSearcher(proxy=_proxy)
                _thread_local.searcher.warmup()
            return _thread_local.searcher

        def _fetch_desc_one(product):
            name = product.get("name", "").strip()
            if not name:
                return product
            print(f"  → {name}")
            try:
                result = _get_searcher_desc().search(name)
                if result:
                    # Replace description and image from source, keep name/price/strategy from YouTube
                    if result.get("description"):
                        product["details"] = result["description"]
                    product["source_url"] = result.get("product_url") or ""
                    product["brand"] = result.get("brand") or ""
                    img_url = result.get("image_url")
                    if img_url:
                        safe = re.sub(r"[^\w\-]", "_", name)[:60]
                        img_path = SOURCE_IMAGES_DIR / f"{safe}.jpg"
                        try:
                            with urllib.request.urlopen(img_url, timeout=15) as resp:
                                with open(img_path, "wb") as fh:
                                    fh.write(resp.read())
                            product["screenshot_file"] = str(img_path)
                            print(f"    ✓ image + description saved")
                        except Exception as ie:
                            print(f"    ⚠️  image download failed: {ie}")
                    else:
                        print(f"    ⚠️  no image URL")
                else:
                    print(f"    ⚠️  no result found")
            except Exception as e:
                print(f"    ⚠️  fetch error: {e}")
            return product

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_fetch_desc_one, p): p for p in all_products}
            for fut in as_completed(futures):
                fut.result()

        found = sum(1 for d in all_products if d.get("screenshot_file"))
        print(f"\n  ✓ Images + descriptions fetched for {found}/{len(all_products)} deals")

    elif _source in _IMAGE_ONLY_SOURCES:
        # ── Image-only: details come from YouTube, images fetched from website ─
        print_section(f"Step 3b — Fetching images from {_source.upper()} (details from YouTube)")
        all_products = fetch_coupon_images(
            deals=all_products,
            output_dir=SOURCE_IMAGES_DIR,
            source=_source,
            proxy=_proxy,
        )
        found = sum(1 for d in all_products if d.get("screenshot_file"))
        print(f"\n  ✓ Images fetched for {found}/{len(all_products)} deals")

    with open(ALL_PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_products, f, ensure_ascii=False, indent=2)

    # ── STEP 4: Generate AI Images ──────────────────────────────────────────
    print_section("Step 4 — AI Image Generation")
    if args.no_images:
        print("  Skipped (--no-images flag set)")
        generated = {}
    else:
        generated = generate_images(
            input_folder=str(SOURCE_IMAGES_DIR),
            output_folder=str(GENERATED_DIR),
            prompt_file=args.prompt_file,
            products=all_products,
        )

    # ── STEP 5: Build CSV ─────────────────────────────────────────────────────
    print_section("Building CSV")
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "video_id", "video_url", "video_title",
            "name", "price", "description", "strategy",
        ])
        writer.writeheader()
        for deal in all_products:
            strategy_steps = deal.get("strategy") or []
            writer.writerow({
                "video_id":    deal.get("video_id", ""),
                "video_url":   deal.get("video_url", ""),
                "video_title": deal.get("video_title", ""),
                "name":        deal.get("name", ""),
                "price":       deal.get("price", ""),
                "description": deal.get("description", ""),
                "strategy":    " | ".join(strategy_steps),
            })
    print(f"  CSV saved → {CSV_FILE.name}")

    # ── STEP 6: Publish to WordPress ──────────────────────────────────────────
    if args.no_publish or args.page_id is None:
        print_section("Step 6 — WordPress Publishing SKIPPED")
        print("  Files saved locally. Run publish_youtube.py separately when ready.")
        print(f"  Example:")
        print(f"    python publish_youtube.py --run-dir {run_dir.name} --page-id <ID> --coupon --publish")
    else:
        print_section("Step 6 — Publishing to WordPress")

        publisher_script = bot_dir / "publish_youtube.py"
        if not publisher_script.exists():
            print(f"❌ publish_youtube.py not found. Skipping publish.")
            sys.exit(1)

        publisher_cmd = [
            sys.executable, str(publisher_script),
            "--page-id", str(args.page_id),
            "--run-dir", str(run_dir),
            "--coupon",
            "--publish-target", args.publish_target,
        ]
        if args.title:
            publisher_cmd.extend(["--title", args.title])
        if args.publish:
            publisher_cmd.append("--publish")
        else:
            publisher_cmd.append("--draft")

        result = subprocess.run(publisher_cmd, cwd=str(bot_dir))
        if result.returncode != 0:
            print("\n❌ Publishing failed.")
            sys.exit(1)

    # ── Done ──────────────────────────────────────────────────────────────────
    print_section("✅ PIPELINE COMPLETE")
    if not args.urls:
        print(f"  Keyword    : {args.keyword}")
    print(f"  Videos     : {len(videos)}")
    print(f"  Deals      : {len(all_products)}")
    print(f"  Source     : {args.source.upper() if args.source else 'Meijer (images only)'}")
    print(f"  Run Dir    : {run_dir.name}")
    print(f"  Page ID    : {args.page_id}")
    if args.no_publish or args.page_id is None:
        print(f"  Status     : LOCAL ONLY (not published)")
    else:
        print(f"  Status     : {'Published (Live)' if args.publish else 'Draft'}")
    print('=' * 60)


if __name__ == "__main__":
    main()
