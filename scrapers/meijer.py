import copy
import json
import os
import sys
from curl_cffi import requests
from dotenv import load_dotenv

load_dotenv()

_GRAPHQL_URL = "https://digital.meijer.com/graphql/"
_MAX_RETRIES = 3

_HEADERS = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "connection": "keep-alive",
    "content-type": "application/json",
    "host": "digital.meijer.com",
    "origin": "https://www.meijer.com",
    "referer": "https://www.meijer.com/",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "x-meijer-gql-query": "search",
}

_PAYLOAD_TEMPLATE = {
    "query": """
    query ProductSearchQuery($args: SearchArgsInput!, $filters: [ProductFilterInput!], $sort: ProductSortInput, $context: ProductQueryContextInput!, $first: Int, $after: String, $storeId: Int!, $includeThirdParty: Boolean!, $userIdentity: UserIdentityInput) {
      productSearch(
        args: $args
        context: $context
        filters: $filters
        sort: $sort
        userIdentity: $userIdentity
      ) {
        __typename
        ... on RedirectResult {
          url
        }
        ... on ProductsResult {
          spaEnrichmentMetaData {
            criteoBeacons {
              onLoadBeacon
              onViewBeacon
              placementName
            }
          }
          itemConnection(first: $first, after: $after) {
            items {
              __typename
              ... on ProductExtended {
                productName
                isSponsored
                attribution {
                  onViewBeacons
                  onClickBeacons
                  onLoadBeacons
                  onBasketChangeBeacon
                  demandSources
                }
                eventType
                hasMPerks
                isAlcohol
                isBopas
                isBusinessActive
                isChokingHazard
                isFoodStampEligible
                isHomeDeliveryAvailable
                isPrimaryUpc
                isProductAgeRestricted
                mPerksOfferId
                productId
                thumbnailImage {
                  url
                }
                soldByUnit
                soldByUnitDescription
                unitOfMeasureQuantity
                upc
                upcTypeName
                updatedAt
                maxOrderQuantity
                isCurbsideEligible
                isPriceByWeight
                isThirdPartyShipping
                thirdPartyOffers @include(if: $includeThirdParty) {
                  itemPriceExcludingShipping
                  minShippingPrice
                  returnPolicyTitle
                  sellerId
                  sellerName
                  thirdPartyOfferId
                  availableStartDate
                  availableEndDate
                }
                storeSpecificProductDetails(storeId: $storeId) {
                  productRules {
                    isWicEligible
                  }
                  productStore {
                    isOnSale
                    isPriceDisplayable
                    savingsDescription
                    priceDescription
                    basePrice
                    basePricePerSoldByUnit
                    customerPrice
                    customerPricePerSoldByUnit
                    discountValuePerSoldByUnit
                    discountValue
                    soldByUnit
                    avgSoldByUnitsPerPricingUnit
                    avgPricingUnitsPerSoldByUnit
                    unitOfMeasureQuantity
                    percentageOff
                    dollarOff
                    ageLimit
                    buyQuantity
                    clearancePrice
                    clearancePricePerSoldByUnit
                    pricingUnit
                    promotionPrice
                    sellQuantity
                    depositValue
                    priceText
                  }
                  productStoreInventory {
                    stockStatus
                    ilcPrimary
                    ilcs
                  }
                  promotions {
                    displayText
                  }
                }
              }
            }
            totalCount
            pageInfo {
              hasNextPage
              endCursor
            }
          }
          sortOptions {
            sortBy
            displayName
            sortOrder
            status
          }
          filterOptions {
            displayName
            name
            selectionType
            hidden
            options {
              displayName
              value
              status
              count
            }
          }
          groupFilterOptions {
            displayName
            groupId
            count
            children {
              displayName
              groupId
              count
              parents {
                displayName
                groupId
                count
              }
            }
            parents {
              displayName
              groupId
              count
            }
          }
          resultCounts {
            embeddingsMatch
            tokenMatch
            totalCount
          }
          collection {
            displayName
          }
        }
      }
    }
    """,
    "variables": {
        "args": {
            "searchType": "SearchTerm",
            "searchValue": "Dial Body Wash",
            "includeThirdParty": False,
            "adUnitId": "9b0b23d1-8883-4b81-80de-b4f9c09079b5"
        },
        "storeId": 20,
        "context": {
            "storeId": 20,
            "constructorSessionId": 7,
            "userSegments": ["web"]
        },
        "includeThirdParty": False,
        "userIdentity": {
            "constructorClientId": "1a70b124-68e5-44ce-9619-d5327b697962"
        },
        "sort": {
            "by": "relevance",
            "order": "descending"
        },
        "filters": [],
        "first": 52
    }
}

class MeijerSearcher:
    """Search Meijer GraphQL API for product data and images.

    Interface mirrors AldiSearcher so it can be used as a drop-in source.
    """

    def __init__(self, proxy: str | None = None):
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    def warmup(self):
        pass  # No session warmup needed for GraphQL

    def search(self, product_name: str) -> dict | None:
        """Search for a product and return a normalised dict or None if not found.

        Returned keys: name, price, image_url, product_url, description, brand, size
        """
        payload = copy.deepcopy(_PAYLOAD_TEMPLATE)
        payload["variables"]["args"]["searchValue"] = product_name

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    _GRAPHQL_URL,
                    headers=_HEADERS,
                    json=payload,
                    proxies=self.proxies,
                    impersonate="chrome107",
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                items = data["data"]["productSearch"]["itemConnection"]["items"]
                if not items:
                    return None
                item = items[0]
                store = (item.get("storeSpecificProductDetails") or {}).get("productStore") or {}
                return {
                    "name":        item.get("productName", product_name),
                    "price":       store.get("priceText") or "",
                    "image_url":   (item.get("thumbnailImage") or {}).get("url") or "",
                    "product_url": f"https://www.meijer.com/shopping/product/{item.get('productId', '')}.html",
                    "description": "",
                    "brand":       "",
                    "size":        "",
                }
            except Exception as e:
                print(f"  [meijer] Attempt {attempt}/{_MAX_RETRIES} failed: {e}")
                if attempt == _MAX_RETRIES:
                    return None
        return None


if __name__ == "__main__":
    term = sys.argv[1] if len(sys.argv) > 1 else "Airwick"
    proxy = os.environ.get("STATIC_PROXY")
    searcher = MeijerSearcher(proxy=proxy)
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

        with open("meijer_response.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print("Saved to meijer_response.json")