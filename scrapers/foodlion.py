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
_TOP_N = 30
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

    @staticmethod
    def _is_wordpress_url(*urls: str) -> bool:
        return any("wp-content" in u.lower() or "wordpress" in u.lower() for u in urls if u)

    def _search_keyword(self, keyword: str, product_name: str) -> tuple[dict | None, dict | None]:
        """Run one Bing image search (via Scrappey).

        Returns a tuple ``(foodlion_result, fallback_result)`` where
        ``foodlion_result`` is the first hit (among top _TOP_N) whose product
        page (purl) contains "foodlion", and ``fallback_result`` is the first
        hit overall that isn't hosted on a wordpress site (used only when no
        foodlion match is found at all).
        """
        url = f"https://www.bing.com/images/search?q={quote(keyword)}&form=VNXTR&first=1"
        payload = {
            "cmd": "request.get",
            "url": url,
            "proxyCountry": self.proxy_country,
            #"requestType": "request",
        }

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(_SCRAPPEY_URL, json=payload, timeout=60)
                resp.raise_for_status()

                data = resp.json()
                html = data.get("solution", {}).get("response", "")
                if not html:
                    print(f"  [foodlion] Scrappey returned no response for {keyword!r}: {data.get('data')}")
                    return None, None

                soup = BeautifulSoup(html, "html.parser")
                tags = soup.select("li[data-idx] a.iusc")[:_TOP_N]

                fallback_result = None
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

                    if self._is_foodlion_url(purl):
                        return result, None

                    if fallback_result is None and not self._is_wordpress_url(img_url, purl):
                        fallback_result = result

                return None, fallback_result  # request succeeded, no foodlion.com match in top _TOP_N

            except Exception as e:
                print(f"  [foodlion] Attempt {attempt}/{_MAX_RETRIES} failed for {keyword!r}: {e}")
                if attempt == _MAX_RETRIES:
                    return None, None
        return None, None

    def _search_keyword_multi(self, keyword: str, product_name: str, tries: int = 1) -> tuple[dict | None, dict | None]:
        """Search the same keyword up to `tries` times since Bing/Scrappey
        results can vary between identical requests. Returns as soon as a
        foodlion match is found; otherwise keeps the first usable fallback
        result seen across all tries.
        """
        fallback_result = None
        for i in range(tries):
            foodlion_result, fallback = self._search_keyword(keyword, product_name)
            if foodlion_result:
                return foodlion_result, None
            if fallback_result is None and fallback:
                fallback_result = fallback
        return None, fallback_result

    def search(self, product_name: str) -> dict | None:
        """Search for a product image and return a normalised dict or None if not found.

        Checks the top _TOP_N Bing image results for a foodlion.com product
        page. If none match, retries with a simpler "<name> foodlion" query
        (no site: operator) and checks the top _TOP_N again. If that also has
        no foodlion.com match, the keyword is skipped (returns None).

        Returned keys: name, price, image_url, product_url, description, brand, size
        """
        result, fallback_1 = self._search_keyword_multi(f"{product_name} site:foodlion.com", product_name)
        if result:
            return result

        result, fallback_2 = self._search_keyword_multi(f"{product_name} foodlion", product_name)
        if result:
            return result

        fallback = fallback_2 
        if fallback:
            return fallback

        print(f"  [foodlion] Skipping {product_name!r} — no foodlion.com image in top {_TOP_N} results")
        return None


if __name__ == "__main__":
    searcher = FoodLionSearcher(proxy=os.getenv("STATIC_PROXY"))
    result = searcher.search("Febreze Fabric Refresher")
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("No image found.")