#!/usr/bin/env python3
"""
Coupon pipeline helpers.

Handles fetching product images from Meijer for coupon deals, replacing
the screenshot step that is used in the regular product pipeline.
"""

import importlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from curl_cffi import requests as cffi_requests

_SOURCE_MAP = {
    "meijer": ("scrapers.meijer", "MeijerSearcher"),
}


def fetch_coupon_images(
    deals: list[dict],
    output_dir: str | Path,
    source: str = "meijer",
    proxy: Optional[str] = None,
    max_workers: int = 5,
) -> list[dict]:
    """For each coupon deal, search the given source for the product image and download it.

    Attaches ``screenshot_file`` to each deal dict (same key used by the
    screenshot step so the rest of the pipeline — AI generation, publishing —
    works unchanged).

    Returns the updated list of deals.
    """
    if source not in _SOURCE_MAP:
        raise ValueError(f"Unknown source: {source!r}. Supported: {list(_SOURCE_MAP)}")
    mod_name, cls_name = _SOURCE_MAP[source]
    SearcherClass = getattr(importlib.import_module(mod_name), cls_name)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    searcher = SearcherClass(proxy=proxy)

    def _fetch_one(deal: dict) -> dict:
        name = deal.get("name", "").strip()
        if not name:
            return deal
        print(f"  [{source}] Searching: {name}")
        result = searcher.search(name)
        if not result:
            print(f"    ⚠️  Not found on {source}: {name}")
            return deal

        img_url = result.get("image_url", "")
        if not img_url:
            print(f"    ⚠️  No image URL for: {name}")
            return deal

        safe_name = re.sub(r"[^\w\-]", "_", name)[:60]
        ext = ".png" if img_url.endswith(".png") else ".jpg"
        img_path = output_dir / f"{safe_name}{ext}"
        proxies = {"http": proxy, "https": proxy} if proxy else None
        for attempt in range(1, 4):
            try:
                resp = cffi_requests.get(
                    img_url,
                    proxies=proxies,
                    impersonate="chrome107",
                    timeout=30,
                )
                resp.raise_for_status()
                with open(img_path, "wb") as fh:
                    fh.write(resp.content)
                deal["screenshot_file"] = str(img_path)
                print(f"    ✓ image saved → {img_path.name}")
                break
            except Exception as e:
                if attempt < 3:
                    print(f"    ⚠️  attempt {attempt} failed: {e} — retrying in 2s")
                    time.sleep(2)
                else:
                    print(f"    ⚠️  image download failed after 3 attempts: {e}")

        return deal

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, d): d for d in deals}
        results = []
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda d: deals.index(d))

    found = [d for d in results if d.get("screenshot_file")]
    skipped = len(results) - len(found)
    if skipped:
        print(f"\n  ⚠️  Dropped {skipped} deal(s) with no Meijer image")
    return found
