import json
from pathlib import Path

import requests


_BASE_URL = (
    "https://www.heb.com/_next/data/"
    "718cc4690b17c6ae0b8e63fdd6c6f1f9e28eb98c/en/search.json"
)

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
    for preferred_size in ("LARGE", "MEDIUM", "SMALL"):
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
        params = {
            "filter": "savings:allsave|coupon",
            "q": product_name,
        }

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.get(
                    _BASE_URL,
                    params=params,
                    headers=_HEADERS,
                    proxies=self.proxies,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                items = _get_search_items(data)
                coupon_items = [p for p in items if p.get("showCouponFlag") is True]

                if not coupon_items:
                    return None

                product = coupon_items[0]

                return {
                    "name":        product.get("fullDisplayName") or product.get("displayName", product_name),
                    "price":       "",
                    "image_url":   _get_image(product) or "",
                    "product_url": f"https://www.heb.com{product.get('productPageURL', '')}" if product.get("productPageURL") else "",
                    "description": product.get("productDescription", ""),
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