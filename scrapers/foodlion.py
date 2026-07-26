# pip install curl-cffi beautifulsoup4

from curl_cffi import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import json

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,/;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

_MAX_RETRIES = 3


class FoodLionSearcher:
    """Search Bing Images (scoped to foodlion.com) for a product image.

    Interface mirrors HEBSearcher/MeijerSearcher so it can be used as a drop-in source.
    Only image_url + name (title) come from the search; description/brand/size are
    not available from Bing image search results.
    """

    def __init__(self, proxy: str | None = None):
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    def warmup(self):
        pass

    def search(self, product_name: str) -> dict | None:
        """Search for a product image and return a normalised dict or None if not found.

        Returned keys: name, price, image_url, product_url, description, brand, size
        """
        keyword = f"{product_name} site:foodlion.com"
        url = f"https://www.bing.com/images/search?q={quote(keyword)}&form=HDRSC2"

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.get(
                    url,
                    headers=_HEADERS,
                    impersonate="chrome107",
                    proxies=self.proxies,
                    timeout=30,
                )
                resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "html.parser")
                tag = soup.select_one("li[data-idx='1'] a.iusc")
                if not tag:
                    return None

                data = json.loads(tag.get("m"))
                img_url = data.get("murl", "")
                title = data.get("t", product_name)

                if not img_url:
                    return None

                return {
                    "name":        title,
                    "price":       "",
                    "image_url":   img_url,
                    "product_url": data.get("purl", ""),
                    "description": "",
                    "brand":       "",
                    "size":        "",
                }

            except Exception as e:
                print(f"  [foodlion] Attempt {attempt}/{_MAX_RETRIES} failed: {e}")
                if attempt == _MAX_RETRIES:
                    return None
        return None


if __name__ == "__main__":
    searcher = FoodLionSearcher()
    result = searcher.search("Dannon Light & Fit yogurt")
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("No image found.")