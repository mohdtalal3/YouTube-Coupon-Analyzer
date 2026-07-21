#!/usr/bin/env python3
"""
Publish YouTube Analyzer Bot results to WordPress (retailshout.com)

Reads products.json + AI-generated images from a run_<video_id>/ directory
and publishes a product listing page using the same CSS classes and structure
as the other RetailShout publishers (HEB, Costco, CVS, etc.).

Each product shows: name, price, description, then the AI-generated image.

Usage:
  python publish_youtube.py --page-id 12345 --draft
  python publish_youtube.py --page-id 12345 --publish
  python publish_youtube.py --run-dir run_abc123 --page-id 12345 --publish
  python publish_youtube.py --video-url https://youtu.be/abc123 --page-id 12345 --publish
"""

import json
import os
import sys
import argparse
import requests
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Optional

# Add parent directory to path to import wordpress_publisher
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env from project root (WP_URL_RS, WP_USERNAME_RS, WP_PASSWORD_RS)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

from wordpress_publisher import WordPressPublisher

# Items shown before "Show More" button
ITEMS_PER_PAGE_LIMIT = 20


# ── HELPERS ───────────────────────────────────────────────────────────────────

def extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]
    return parsed.path.strip("/").split("/")[-1]


def find_latest_run_dir(base_dir: Path) -> Optional[Path]:
    dirs = sorted(
        [d for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return dirs[0] if dirs else None


def load_products(run_dir: Path) -> list:
    products_file = run_dir / "products.json"
    if not products_file.exists():
        print(f"❌ products.json not found in {run_dir}")
        return []
    with open(products_file, "r", encoding="utf-8") as f:
        return json.load(f)


def find_generated_image(product: dict, generated_dir: Path) -> Optional[Path]:
    """Find the AI-generated image for a product by matching its screenshot filename.
    Falls back to the raw screenshot if no generated image exists."""
    screenshot_path = product.get("screenshot_file")
    if not screenshot_path:
        return None

    filename = Path(screenshot_path).name

    # Try generated/ folder first
    if generated_dir.exists():
        candidate = generated_dir / filename
        if candidate.exists():
            return candidate
        # Fallback: match by stem
        stem = Path(filename).stem
        for f in generated_dir.iterdir():
            if f.stem == stem:
                return f

    # Final fallback: use the raw screenshot directly
    screenshot = Path(screenshot_path)
    if screenshot.exists():
        return screenshot

    return None


def upload_and_get_url(publisher: WordPressPublisher, img_path: Path, title: str) -> Optional[str]:
    """Upload image to WordPress and return its public source URL."""
    if not img_path or not img_path.exists():
        return None
    media_id = publisher.upload_image(img_path, title=title)
    if not media_id:
        return None
    try:
        resp = requests.get(
            f"{publisher.api_base}/media/{media_id}",
            auth=publisher.auth,
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("source_url")
    except Exception as e:
        print(f"  ⚠️ Could not resolve URL for media {media_id}: {e}")
    return None


# ── HTML BUILDERS ─────────────────────────────────────────────────────────────

def render_item_html(item_index: int, product: dict, img_url: Optional[str], defer_image: bool = False) -> str:
    """Render a single product using the same finds-item CSS classes as other publishers."""
    name = product.get("name", "").strip()
    price = product.get("price")
    details = product.get("details", "").strip()

    title = name if name else f"Item #{item_index}"
    title_safe = title.replace('"', '&quot;')

    html = f'    <div class="finds-item">'
    html += f'<div class="finds-item-header">'
    html += f'<span class="finds-item-title">{item_index}) {title}</span>'

    # Price inline in header
    if price is not None:
        formatted = f"${price:.2f}" if price != int(price) else f"${int(price)}"
        html += f' - <span class="finds-item-price">{formatted}</span>'

    html += '</div>'

    # Description / details with collapsible See more
    if details:
        desc_id = f"desc-{item_index}"
        btn_id  = f"btn-{item_index}"
        html += (
            f'<div id="{desc_id}" style="margin-top:8px;padding:0 10px;'
            f'max-height:4.5em;overflow:hidden;">{details}</div>'
            f'<button id="{btn_id}" '
            f'onclick="(function(b,d){{if(d.style.maxHeight===\'none\'){{d.style.maxHeight=\'4.5em\';b.textContent=\'See more ▼\';}}else{{d.style.maxHeight=\'none\';b.textContent=\'See less ▲\';}}}}'
            f'(document.getElementById(\'{btn_id}\'),document.getElementById(\'{desc_id}\')))" '
            f'style="display:none;background:none;border:none;color:#e63c3c;cursor:pointer;'
            f'font-size:0.85em;padding:2px 10px 6px;">See more ▼</button>'
            f'<script>'
            f'(function(){{var e=document.getElementById(\'{desc_id}\');'
            f'var b=document.getElementById(\'{btn_id}\');'
            f'if(e.scrollHeight>e.clientHeight+2){{b.style.display=\'inline-block\';}}}})();'
            f'</script>'
        )

    # AI-generated image
    if img_url:
        if defer_image:
            img_tag = f'<img data-src="{img_url}" alt="{title_safe}" style="max-width: 100%; height: auto;" class="lazy-load" />'
        else:
            img_tag = f'<img src="{img_url}" alt="{title_safe}" style="max-width: 100%; height: auto;" />'
        html += f'<div style="text-align: center; margin-top: 10px; padding: 10px;">{img_tag}</div>'

    html += '</div>'
    return html


def build_page_html(products: list, img_urls: dict, video_url: Optional[str]) -> str:
    """Build the full WordPress page HTML using retailshout theme CSS classes."""
    total = len(products)

    html = '<div id="top" class="aos-finds">\n'
    html += '  <h3 style="text-align: center;">Products from the Video</h3>\n\n'

    if video_url:
        html += (
            f'  <p style="text-align: center; font-style: italic;">'
            f'{total} products found — '
            f'<a href="{video_url}" target="_blank" rel="noopener noreferrer">Watch the full video</a>'
            f'</p>\n\n'
        )
    else:
        html += f'  <p style="text-align: center; font-style: italic;">{total} products found</p>\n\n'

    # Visible items
    html += '  <div class="aos-category-items">\n'

    visible = products[:ITEMS_PER_PAGE_LIMIT]
    hidden = products[ITEMS_PER_PAGE_LIMIT:]

    for i, product in enumerate(visible, start=1):
        idx = products.index(product)
        html += render_item_html(i, product, img_urls.get(idx), defer_image=False) + "\n"

    html += '  </div>\n'

    # Show More section for hidden items
    if hidden:
        html += '  <div class="aos-more" id="finds-more-all" style="display:none;">\n'
        html += '    <div class="aos-category-items">\n'
        for i, product in enumerate(hidden, start=len(visible) + 1):
            idx = products.index(product)
            html += render_item_html(i, product, img_urls.get(idx), defer_image=True) + "\n"
        html += '    </div>\n'
        html += '  </div>\n'
        html += (
            f'  <button class="aos-show-more" data-target="all" data-label="Products" aria-expanded="false">'
            f'Show more ({len(hidden)} more)</button>\n'
        )

    html += '</div>'
    return html


def render_coupon_html(item_index: int, deal: dict, img_url: Optional[str] = None, defer_image: bool = False) -> str:
    """Render a single coupon deal: H3 (name – price), image, description, strategy bullets."""
    name = deal.get("name", "").strip()
    price = str(deal.get("price") or "").strip()
    description = deal.get("description", "").strip()
    strategy = deal.get("strategy") or []

    title = name if name else f"Deal #{item_index}"
    title_safe = title.replace('"', '&quot;')
    price_part = f" \u2013 {price}" if price else ""

    html = f'    <div class="finds-item">'
    html += f'<h3 style="margin-bottom:6px;">{title}{price_part}</h3>'

    if img_url:
        if defer_image:
            img_tag = f'<img data-src="{img_url}" alt="{title_safe}" style="max-width: 100%; height: auto;" class="lazy-load" />'
        else:
            img_tag = f'<img src="{img_url}" alt="{title_safe}" style="max-width: 100%; height: auto;" />'
        html += f'<div style="text-align: center; margin-top: 10px; padding: 10px;">{img_tag}</div>'

    if description:
        html += '<p style="margin:8px 0 4px;font-weight:600;">Description:</p>'
        html += f'<p style="margin:4px 0 8px;">{description}</p>'

    if strategy:
        html += '<p style="margin:8px 0 4px;font-weight:600;">Strategy:</p>'
        html += '<ul style="margin:4px 0 8px;padding-left:20px;">'
        for step in strategy:
            html += f'<li style="margin-bottom:4px;">{step}</li>'
        html += '</ul>'

    html += '</div>'
    return html


def build_coupon_page_html(deals: list, img_urls: dict, video_url: Optional[str]) -> str:
    """Build the full WordPress page HTML for coupon deals."""
    total = len(deals)

    html = '<div id="top" class="aos-finds">\n'
    html += '  <h3 style="text-align: center;">Coupon Deals from the Video</h3>\n\n'

    if video_url:
        html += (
            f'  <p style="text-align: center; font-style: italic;">'
            f'{total} deals found \u2014 '
            f'<a href="{video_url}" target="_blank" rel="noopener noreferrer">Watch the full video</a>'
            f'</p>\n\n'
        )
    else:
        html += f'  <p style="text-align: center; font-style: italic;">{total} deals found</p>\n\n'

    html += '  <div class="aos-category-items">\n'

    visible = deals[:ITEMS_PER_PAGE_LIMIT]
    hidden = deals[ITEMS_PER_PAGE_LIMIT:]

    for i, deal in enumerate(visible, start=1):
        idx = deals.index(deal)
        html += render_coupon_html(i, deal, img_urls.get(idx), defer_image=False) + "\n"

    html += '  </div>\n'

    if hidden:
        html += '  <div class="aos-more" id="finds-more-all" style="display:none;">\n'
        html += '    <div class="aos-category-items">\n'
        for i, deal in enumerate(hidden, start=len(visible) + 1):
            idx = deals.index(deal)
            html += render_coupon_html(i, deal, img_urls.get(idx), defer_image=True) + "\n"
        html += '    </div>\n'
        html += '  </div>\n'
        html += (
            f'  <button class="aos-show-more" data-target="all" data-label="Deals" aria-expanded="false">'
            f'Show more ({len(hidden)} more)</button>\n'
        )

    html += '</div>'
    return html


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Publish YouTube Analyzer results to WordPress (retailshout.com)"
    )
    parser.add_argument("--page-id", type=int, required=True, help="WordPress page ID to update")
    parser.add_argument("--run-dir", help="Path to run directory (e.g. run_abc123)")
    parser.add_argument("--video-url", help="YouTube URL — used to locate run_<id>/ directory")
    parser.add_argument("--title", help="Override WordPress page title (optional)")
    parser.add_argument("--coupon", action="store_true", help="Publish coupon deals (no images; H3 + description + bullet strategy)")
    parser.add_argument("--publish-target", default="retailshout", choices=["retailshout","aos"], help="Which WordPress site to publish to")

    status_group = parser.add_mutually_exclusive_group()
    status_group.add_argument("--publish", action="store_true", help="Publish live")
    status_group.add_argument("--draft", action="store_true", help="Keep as draft (default)")

    args = parser.parse_args()

    bot_dir = Path(__file__).parent

    # ── Locate run directory ──
    if args.run_dir:
        run_dir = Path(args.run_dir) if Path(args.run_dir).is_absolute() else bot_dir / args.run_dir
    elif args.video_url:
        video_id = extract_video_id(args.video_url)
        run_dir = bot_dir / f"run_{video_id}"
    else:
        run_dir = find_latest_run_dir(bot_dir)
        if not run_dir:
            print("❌ No run directory found. Provide --run-dir or --video-url.")
            sys.exit(1)
        print(f"📂 Auto-detected run directory: {run_dir.name}")

    if not run_dir.exists():
        print(f"❌ Run directory not found: {run_dir}")
        sys.exit(1)

    generated_dir = run_dir / "generated"

    # ── Load products / deals ──
    products = load_products(run_dir)
    if not products:
        print("❌ No products found. Run the analyzer first.")
        sys.exit(1)
    label = "coupon deals" if args.coupon else "products"
    print(f"📋 Loaded {len(products)} {label} from {run_dir.name}")

    # ── WordPress credentials ──
    if args.publish_target == "aos":
        wp_url = os.environ.get("WP_URL")
        wp_username = os.environ.get("WP_USERNAME")
        wp_password = os.environ.get("WP_PASSWORD")
        site_name = "aisleofshame.com"
        cred_prefix = "WP_URL / WP_USERNAME / WP_PASSWORD"
    else:
        wp_url = os.environ.get("WP_URL_RS")
        wp_username = os.environ.get("WP_USERNAME_RS")
        wp_password = os.environ.get("WP_PASSWORD_RS")
        site_name = "retailshout.com"
        cred_prefix = "WP_URL_RS / WP_USERNAME_RS / WP_PASSWORD_RS"

    if not all([wp_url, wp_username, wp_password]):
        print(f"❌ Missing WordPress credentials for {site_name}!")
        print(f"   Set {cred_prefix} environment variables")
        sys.exit(1)

    print(f"  Publishing to: {site_name}")

    publisher = WordPressPublisher(wp_url, wp_username, wp_password)

    if not publisher.test_connection():
        sys.exit(1)

    # ── Determine video URL for header link ──
    video_url = args.video_url
    if not video_url:
        first_yt = next((p.get("video_url", "") or p.get("youtube_timestamp_url", "") for p in products if p.get("video_url") or p.get("youtube_timestamp_url")), "")
        video_url = first_yt or None

    # ── Coupon mode: upload images then build coupon HTML ──
    if args.coupon:
        print(f"\n📤 Uploading AI-generated images to WordPress...")
        img_urls: dict = {}

        for i, deal in enumerate(products):
            name = deal.get("name", f"deal_{i + 1}")
            img_path = find_generated_image(deal, generated_dir)

            if img_path:
                is_fallback = not (generated_dir.exists() and img_path.parent == generated_dir)
                img_label = "fallback" if is_fallback else "AI"
                url = upload_and_get_url(publisher, img_path, name)
                if url:
                    img_urls[i] = url
                    print(f"  [{i + 1}] Uploaded ({img_label}) '{name}'")
            else:
                print(f"  [{i + 1}] No image found for '{name}' — skipping")

        print(f"\n🎨 Generating coupon HTML content...")
        html = build_coupon_page_html(products, img_urls, video_url)
    else:
        # ── Upload AI-generated images ──
        print(f"\n📤 Uploading AI-generated images to WordPress...")
        img_urls: dict = {}

        for i, product in enumerate(products):
            name = product.get("name", f"product_{i + 1}")
            img_path = find_generated_image(product, generated_dir)

            if img_path:
                is_fallback = not (generated_dir.exists() and img_path.parent == generated_dir)
                img_label = "fallback" if is_fallback else "AI"
                url = upload_and_get_url(publisher, img_path, name)
                if url:
                    img_urls[i] = url
                    print(f"  [{i + 1}] Uploaded ({img_label}) '{name}'")
            else:
                print(f"  [{i + 1}] No image found for '{name}' — skipping")

        # ── Filter: drop products with no image ──
        before = len(products)
        products = [p for i, p in enumerate(products) if i in img_urls]
        img_urls = {new_i: img_urls[old_i] for new_i, old_i in enumerate(img_urls)}
        skipped = before - len(products)
        if skipped:
            print(f"\n  ⚠️  Skipped {skipped} product(s) with no image — {len(products)} remaining")

        if not products:
            print("❌ No products with images to publish. Exiting.")
            sys.exit(1)

        # ── Build HTML ──
        print(f"\n🎨 Generating HTML content...")
        html = build_page_html(products, img_urls, video_url)

    # ── Publish ──
    status = "publish" if args.publish else "draft"
    print(f"\n📤 Updating WordPress page {args.page_id} as {status.upper()}...")

    success = publisher.update_page(
        page_id=args.page_id,
        content=html,
        title=args.title or None,
        status=status,
        try_page_first=True,
        update_date=args.publish,
    )

    if success:
        print("\n" + "=" * 60)
        print("✅ PUBLISHED SUCCESSFULLY!")
        print("=" * 60)
        print(f"   Products : {len(products)}")
        print(f"   Images   : {len(img_urls)}")
        print(f"   Page ID  : {args.page_id}")
        print(f"   Status   : {status.upper()}")
        print("=" * 60)
    else:
        print("\n❌ Publishing failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
