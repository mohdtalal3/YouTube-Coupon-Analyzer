# pip install curl-cffi beautifulsoup4

from curl_cffi import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from dotenv import load_dotenv
import json
import os

load_dotenv()

_PROXY_COUNTRY = "UnitedStates"

_SCRAPPEY_API_KEY = os.getenv("SCRAPPEY_API_KEY")
_SCRAPPEY_URL = f"https://publisher.scrappey.com/api/v1?key={_SCRAPPEY_API_KEY}"

_MAX_RETRIES = 3
_TOP_N = 50
_DOMAIN = "foodlion.com"


class FoodLionSearcher:
    """Search Bing Images (scoped to foodlion.com) for a product image.

    Interface mirrors HEBSearcher/MeijerSearcher so it can be used as a drop-in source.
    Only image_url + name (title) come from the search; description/brand/size are
    not available from Bing image search results.
    """

    def __init__(self, proxy: str | None = None):
        # Requests are proxied through Scrappey, which handles the exit IP
        # itself, so the raw `proxy` string (e.g. STATIC_PROXY) is unused here.
        self.proxy_country = _PROXY_COUNTRY

    def warmup(self):
        pass

    @staticmethod
    def _is_foodlion_url(url: str) -> bool:
        return "foodlion" in url.lower()

    def _search_keyword(self, keyword: str, product_name: str, fallback_first: bool = False) -> dict | None:
        """Run one Bing image search (via Scrappey) and return the first
        result (among the top _TOP_N) whose product page (purl) is on
        foodlion.com.
        """
        url = f"https://www.bing.com/images/search?q={quote(keyword)}&form=VNXTR&first=1"
        payload = {
            "cmd": "request.get",
            "url": url,
            "proxyCountry": self.proxy_country,
            "requestType": "request",
        }

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(_SCRAPPEY_URL, json=payload, timeout=60)
                resp.raise_for_status()

                data = resp.json()
                html = data.get("solution", {}).get("response", "")
                if not html:
                    print(f"  [foodlion] Scrappey returned no response for {keyword!r}: {data.get('data')}")
                    return None

                soup = BeautifulSoup(html, "html.parser")
                tags = soup.select("li[data-idx] a.iusc")[:_TOP_N]

                first_result = None
                for tag in tags:
                    m_data = tag.get("m")
                    if not m_data:
                        continue
                    try:
                        data = json.loads(m_data)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    img_url = data.get("murl", "")
                    purl = data.get("purl", "")
                    if not img_url:
                        continue

                    result = {
                        "name":        data.get("t", product_name),
                        "price":       "",
                        "image_url":   img_url,
                        "product_url": purl,
                        "description": "",
                        "brand":       "",
                        "size":        "",
                    }

                    if first_result is None:
                        first_result = result

                    if self._is_foodlion_url(purl):
                        return result

                if fallback_first and first_result:
                    return first_result

                return None  # request succeeded, no foodlion.com match in top _TOP_N

            except Exception as e:
                print(f"  [foodlion] Attempt {attempt}/{_MAX_RETRIES} failed for {keyword!r}: {e}")
                if attempt == _MAX_RETRIES:
                    return None
        return None

    def search(self, product_name: str) -> dict | None:
        """Search for a product image and return a normalised dict or None if not found.

        Checks the top _TOP_N Bing image results for a foodlion.com product
        page. If none match, retries with a simpler "<name> foodlion" query
        (no site: operator) and checks the top _TOP_N again. If that also has
        no foodlion.com match, the keyword is skipped (returns None).

        Returned keys: name, price, image_url, product_url, description, brand, size
        """
        result = self._search_keyword(f"{product_name} site:foodlion.com", product_name)
        if result:
            return result

        result = self._search_keyword(f"{product_name} foodlion", product_name, fallback_first=True)
        if result:
            return result

        print(f"  [foodlion] Skipping {product_name!r} — no foodlion.com image in top {_TOP_N} results")
        return None


if __name__ == "__main__":
    searcher = FoodLionSearcher(proxy=os.getenv("STATIC_PROXY"))
    result = searcher.search("aveeno moisturizer")
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("No image found.")