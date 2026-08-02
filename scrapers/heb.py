import json
import os
import re
from pathlib import Path
from typing import Optional

import requests as _scrappey_requests
from curl_cffi import requests
from dotenv import load_dotenv

load_dotenv()

_SCRAPPEY_API_KEY = os.getenv("SCRAPPEY_API_KEY", "")
_SCRAPPEY_URL = f"https://publisher.scrappey.com/api/v1?key={_SCRAPPEY_API_KEY}"

_HOMEPAGE_URL = "https://www.heb.com/"
_SEARCH_PATH_TEMPLATE = "https://www.heb.com/_next/data/{build_id}/en/search.json"

_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://www.heb.com/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "x-nextjs-data": "1",
}

_MAX_RETRIES = 3
_cached_build_id: str | None = None


def _fetch_build_id() -> str | None:
    """Fetch H-E-B homepage via Scrappey and extract the Next.js build ID."""
    global _cached_build_id
    if _cached_build_id:
        return _cached_build_id

    payload = {
        "cmd": "request.get",
        "url": _HOMEPAGE_URL,
        "proxyCountry": "UnitedStates",
        "automaticallySolveCaptchas": True
    }

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = _scrappey_requests.post(_SCRAPPEY_URL, json=payload, timeout=60)
            resp.raise_for_status()
            html = resp.json().get("solution", {}).get("response", "")
            if not html:
                print(f"  [heb] Attempt {attempt}/{_MAX_RETRIES}: No HTML returned from Scrappey")
                if attempt < _MAX_RETRIES:
                    continue
                return None

            match = re.search(r'["\']version["\']\s*:\s*["\']([a-fA-F0-9]{20,64})["\']', html, re.IGNORECASE)
            if match:
                _cached_build_id = match.group(1)
                print(f"  [heb] Fetched build ID: {_cached_build_id}")
                return _cached_build_id

            print(f"  [heb] Attempt {attempt}/{_MAX_RETRIES}: Could not find build ID in homepage HTML")
            if attempt < _MAX_RETRIES:
                continue
            return None
        except Exception as e:
            print(f"  [heb] Attempt {attempt}/{_MAX_RETRIES} failed: {e}")
            if attempt < _MAX_RETRIES:
                continue
            return None
    return None


def _get_search_items(data: dict) -> list:
    visual_components = (
        data.get("pageProps", {})
        .get("layout", {})
        .get("visualComponents", [])
    )
    for component in visual_components:
        if component.get("type") == "searchGridV2":
            return component.get("items", [])
    return []


def _get_image(product: dict) -> str | None:
    images = product.get("productImageUrls", [])
    for preferred_size in ("MEDIUM", "LARGE", "SMALL"):
        for image in images:
            if image.get("size") == preferred_size:
                return image.get("url")
    return images[0].get("url") if images else None


class HEBSearcher:
    """Search H-E-B website for product data and images.

    Interface mirrors MeijerSearcher so it can be used as a drop-in source.
    Only returns products with showCouponFlag=true.
    """

    def __init__(self, proxy: str | None = None):
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    def warmup(self):
        pass

    def search(self, product_name: str) -> dict | None:
        """Search for a product and return a normalised dict or None if not found.

        Returned keys: name, price, image_url, product_url, description, brand, size
        """
        build_id = _fetch_build_id()
        if not build_id:
            print("  [heb] Cannot search without build ID")
            return None

        search_url = _SEARCH_PATH_TEMPLATE.format(build_id=build_id)

        params = {
            "filter": "savings:allsave|coupon",
            "q": product_name,
        }

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.get(
                    search_url,
                    params=params,
                    headers=_HEADERS,
                    proxies=self.proxies,
                    timeout=30,
                    impersonate="chrome131",
                )
                resp.raise_for_status()
                data = resp.json()

                items = _get_search_items(data)
                coupon_items = [p for p in items if p.get("showCouponFlag") is True]

                if not coupon_items:
                    return None

                product = coupon_items[0]

                description = product.get("productDescription", "")
                if description:
                    description = re.sub(r'<[^>]+>', '', description)

                return {
                    "name":        product.get("fullDisplayName") or product.get("displayName", product_name),
                    "price":       "",
                    "image_url":   _get_image(product) or "",
                    "product_url": f"https://www.heb.com{product.get('productPageURL', '')}" if product.get("productPageURL") else "",
                    "description": description,
                    "brand":       "",
                    "size":        "",
                }

            except Exception as e:
                print(f"  [heb] Attempt {attempt}/{_MAX_RETRIES} failed: {e}")
                if attempt == _MAX_RETRIES:
                    return None
        return None


if __name__ == "__main__":
    searcher = HEBSearcher()
    result = searcher.search("H-E-B Deli Pimento Cheese Spread")
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("No coupon products found.")