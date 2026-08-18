import json
import os
import sys

from curl_cffi import requests
from dotenv import load_dotenv

load_dotenv()


_API_URL = (
    "https://services.publix.com/search/api/search/storeproductssavings/"
)

_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://www.publix.com",
    "publixstore": "9999",
    "referer": "https://www.publix.com/",
    "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "x-src": "WEB_SEARCH_20240506",
}

_QUERY = """query GetStoreProductsSavingsSearchResultAsync($keyword: String, $skip: Int!, $take: Int!, $facetOverrideStr: String, $facets: String, $sortOrder: String, $ispu: Boolean, $categoryID: String, $minMatch: Int!, $boostVarIndex: Int!, $wildcardSearch: Boolean!, $isPreviewSite: Boolean!, $segmentVarIndex: Int!, $getOrderHistory: Boolean!, $filterQuery: String, $reorderItemCodes: [Int!], $intents: [String!], $searchRetryIndex: Int!, $intentVarIndex: Int!, $boostBuryQuery: String, $source: String, $elevatedProducts: [KeyValuePairOfStringAndStringInput!], $couponId: String, $forceElevation: Boolean, $searchVariation: [KeyValuePairOfStringAndStringInput!], $userCoupon: String) {
  storeProductsSavingsSearchResult(
    keyword: $keyword
    skip: $skip
    take: $take
    facetOverrideStr: $facetOverrideStr
    facets: $facets
    sortOrder: $sortOrder
    ispu: $ispu
    categoryID: $categoryID
    minMatch: $minMatch
    boostVarIndex: $boostVarIndex
    wildcardSearch: $wildcardSearch
    isPreviewSite: $isPreviewSite
    segmentVarIndex: $segmentVarIndex
    getOrderHistory: $getOrderHistory
    filterQuery: $filterQuery
    reorderItemCodes: $reorderItemCodes
    intents: $intents
    boostBuryQuery: $boostBuryQuery
    searchRetryIndex: $searchRetryIndex
    intentVarIndex: $intentVarIndex
    source: $source
    elevatedProducts: $elevatedProducts
    couponId: $couponId
    forceElevation: $forceElevation
    searchVariation: $searchVariation
    userCoupon: $userCoupon
  ) {
    storeProducts {
      baseProductId
      itemCode
      title
      sizeDescription
      priceLine
      hasCoupon
      titleBrand
      imageUrls {
        large {
          a
        }
        small {
          a
        }
      }
    }
    totalCount
  }
}
"""

_MAX_RETRIES = 3


class PublixSearcher:
    """Search Publix website for product data and images.

    Interface mirrors HEBSearcher so it can be used as a drop-in source.
    """

    def __init__(self, proxy: str | None = None):
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    def warmup(self):
        pass

    def search(self, product_name: str) -> dict | None:
        """Search for a product and return a normalised dict or None if not found.

        Returned keys: name, price, image_url, product_url, description, brand, size
        """
        payload = {
            "operationName": "GetStoreProductsSavingsSearchResultAsync",
            "variables": {
                "take": 48,
                "skip": 0,
                "sortOrder": "score desc",
                "ispu": False,
                "keyword": product_name,
                "facets": "",
                "minMatch": -41,
                "boostVarIndex": 1,
                "wildcardSearch": False,
                "isPreviewSite": False,
                "getOrderHistory": False,
                "filterQuery": "",
                "reorderItemCodes": None,
                "boostBuryQuery": "",
                "elevatedProducts": [],
                "forceElevation": False,
                "searchRetryIndex": 1,
                "source": "WEB_SEARCH",
                "searchVariation": [
                    {"key": "configurable_add_to_cart", "value": "true"},
                    {"key": "boost_field", "value": "A"},
                ],
                "segmentVarIndex": 1,
                "intents": [],
                "userCoupon": None,
                "intentVarIndex": 1,
            },
            "query": _QUERY,
        }

        params = {
            "keyword": product_name,
            "storeNumber": "9999",
            "cat": "undefined",
            "source": "WEB_SEARCH",
        }

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    _API_URL,
                    params=params,
                    json=payload,
                    headers=_HEADERS,
                    proxies=self.proxies,
                    timeout=30,
                    impersonate="chrome131",
                )
                resp.raise_for_status()
                data = resp.json()

                result = data.get("data", {}).get("storeProductsSavingsSearchResult") or {}
                products = result.get("storeProducts", [])

                if not products:
                    return None

                product = products[0]

                images = product.get("imageUrls") or {}
                image_url = (
                    (images.get("large") or {}).get("a")
                    or (images.get("small") or {}).get("a")
                    or ""
                )

                return {
                    "name":        product.get("title") or product_name,
                    "price":       product.get("priceLine") or "",
                    "image_url":   image_url,
                    "product_url": f"https://www.publix.com/pd/{product.get('baseProductId', '')}" if product.get("baseProductId") else "",
                    "description": "",
                    "brand":       product.get("titleBrand") or "",
                    "size":        product.get("sizeDescription") or "",
                }

            except Exception as e:
                print(f"  [publix] Attempt {attempt}/{_MAX_RETRIES} failed: {e}")
                if attempt == _MAX_RETRIES:
                    return None
        return None


if __name__ == "__main__":
    term = sys.argv[1] if len(sys.argv) > 1 else "Persil"
    proxy = os.environ.get("STATIC_PROXY")
    searcher = PublixSearcher(proxy=proxy)
    result = searcher.search(term)

    if result is None:
        print("Product not found.")
    else:
        print("\n── Product ─────────────────────────────────")
        print(f"Name : {result['name']}")
        print(f"Price: {result['price']}")
        print(f"Image: {result['image_url']}")
        print(f"URL  : {result['product_url']}")
        print("────────────────────────────────────────────")

        with open("publix_response.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print("Saved to publix_response.json")
