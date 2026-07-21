import json
import os
import httpx
from typing import Optional
from openai import OpenAI
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

XAI_API_KEY = os.getenv("XAI_API_KEY")

# ── SCHEMA ───────────────────────────────────

class CouponDeal(BaseModel):
    name: str = Field(description="Full product name including size/variant, e.g. 'Dial Body Wash (Men\\'s & Women\\'s)'")
    price: str = Field(description="Combined price string showing original and final price after ALL savings. Format: '$7.00 -> $3.00'. If only one price known, just that price e.g. '$3.50'.")
    description: str = Field(description="1-2 sentences describing the deal/promotion, e.g. 'Dial body washes are on promotion for 2 for $7.00.'")
    strategy: list[str] = Field(description="Step-by-step couponing strategy EXACTLY as described in the video, each step as a separate string")

class CouponDealList(BaseModel):
    deals: list[CouponDeal]


def extract_coupons(
    transcript_file: str,
    video_id: str,
    output_file: str = "coupons.json",
    previous_response_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> tuple[str, str]:
    """Extract coupon deals from a transcript.

    Returns (output_file, response_id) so the caller can chain calls.
    """
    with open(transcript_file, "r", encoding="utf-8") as f:
        segments = json.load(f)

    transcript_text = "\n".join(
        f"[{s['start']}s] {s['text']}" for s in segments
    )

    COUPON_SYSTEM_PROMPT = """You are an expert coupon deal extraction assistant analyzing a YouTube video transcript.

YOUR GOAL: Extract EVERY SINGLE coupon deal mentioned, shown, or referenced in the video. Do NOT skip any deal.

━━━ DEDUPLICATION RULES ━━━
- Do NOT extract any deal already extracted in a previous response in this session.
- Only return deals that are NEW and not yet in any previous response.

━━━ EXTRACTION RULES ━━━
- Extract ALL deals: main featured deals, quick mentions, combo deals
- If a deal is mentioned multiple times, extract it ONCE
- Read 3–4 segments BEFORE and AFTER to capture the full deal context (price, coupon steps, rebates)

━━━ FIELD RULES ━━━
- name: For a single product deal: Brand + product type (e.g. "Tide Liquid Detergent", "Downy Fabric Softener"). Do NOT include size, count, flavor, scent, or packaging details. Do NOT prepend store/retailer names.
  For a multi-product bundle or spend-and-save scenario (e.g. "Spend $40 on P&G items, get $10 off"): use a short descriptive deal name that captures the scenario (e.g. "P&G Spend $40 Get $10 Off"). If the host presents multiple scenarios for the same promotion, treat each scenario as a separate deal with a distinguishing suffix (e.g. "P&G Spend $40 Get $10 Off – Paper Goods", "P&G Spend $40 Get $10 Off – Cleaning").
- price: A single combined price string showing the original price and the final price after ALL savings (store coupons, digital coupons, Ibotta, cashback, etc.).
  Format: "$ORIGINAL → $FINAL" (e.g. "$7.00 → $3.00"). If only one price is known, just that price (e.g. "$3.50").
- description: 1–2 sentences describing the product or deal. For single products: describe what it is, available variants, flavors, scents, sizes, or count. For bundle deals: list EVERY participating product with its FULL name (Brand + product type). NEVER use a brand name alone.
  WRONG: "Includes Downy, Baby Dawn, Gain, Charmin, and Bounty."
  RIGHT: "Includes Downy Fabric Softener, Baby Dawn Dish Soap, Gain Dish Soap, Charmin Toilet Paper, and Bounty Paper Towels."
  Do NOT repeat price, deal mechanics, or coupon steps — those belong in strategy.
- strategy: A list of clear, actionable steps. EVERY product reference MUST include the full Brand + product type name. NEVER use a brand name alone.
  WRONG: "Grab Downy on sale for $12.99"
  RIGHT: "Grab Downy Fabric Softener on sale for $12.99"
  WRONG: "Grab Baby Dawn at $19"
  RIGHT: "Grab Baby Dawn Dish Soap at $19"
  Include: what to grab (full name), which coupon to clip, what to pay at register, any receipt submission (Ibotta, Fetch, etc.), and final savings. For bundle deals, include the math/total summary as the final step (e.g. "Total is $40.15, minus $10 P&G instant savings and $10.45 in coupons — pay $19.70 for everything."). You may rephrase for clarity and readability, but do NOT change, omit, or add any actual steps, amounts, or deal details.
"""

    USER_PROMPT = f"""VIDEO_ID: {video_id}

Transcript (format: [seconds] text):
{transcript_text}

REMINDER: Extract EVERY coupon deal that has NOT already been extracted from a previous video in this session. Check the full transcript carefully — deals are often spread across multiple segments."""

    client = OpenAI(
        api_key=XAI_API_KEY,
        base_url="https://api.x.ai/v1",
        timeout=httpx.Timeout(3600.0, connect=30.0),
    )

    request_kwargs = dict(
        model="grok-4.5",
        max_output_tokens=500000,
        reasoning={
            "effort": "medium"
        },
        store=True,
        text={
            "format": {
                "type": "json_schema",
                "name": "CouponDealList",
                "schema": CouponDealList.model_json_schema(),
                "strict": True,
            }
        },
    )

    if previous_response_id:
        request_kwargs["previous_response_id"] = previous_response_id
        request_kwargs["input"] = [{"role": "user", "content": USER_PROMPT}]
    else:
        request_kwargs["input"] = [
            {"role": "system", "content": COUPON_SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ]

    response_text = ""
    response_id = None
    with client.responses.stream(**request_kwargs) as stream:
        for event in stream:
            delta = getattr(event, "delta", None)
            if delta:
                print(delta, end="", flush=True)
        print()
        final = stream.get_final_response()
        response_text = final.output_text
        response_id = final.id

    data = json.loads(response_text)
    deals = [CouponDeal(**d).model_dump() for d in data["deals"]]

    if limit:
        deals = deals[:limit]

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(deals, f, ensure_ascii=False, indent=2)

    print(f"  Extracted {len(deals)} coupon deals → {output_file}")
    return output_file, response_id