import argparse
import glob
import json
import os
import threading
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── CONFIG ──────────────────────────────────
KIE_API_KEY = os.getenv("KIE_API_KEY", "")
INPUT_FOLDER = "screenshots"
OUTPUT_FOLDER = "generated"

# How many images to process in parallel.
# 32 is safe for any volume — I/O bound tasks don't need more threads.
# The rate limiter (18 req/10s) is the real bottleneck, not this number.
WORKERS = 32

UPLOAD_URL = "https://kieai.redpandaai.co/api/file-stream-upload"
CREATE_TASK_URL = "https://api.kie.ai/api/v1/jobs/createTask"
GET_TASK_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"

HEADERS_AUTH = {"Authorization": f"Bearer {KIE_API_KEY}"}

PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompt.txt")

def load_prompt(prompt_file: str = None, product: dict = None) -> str:
    """Load prompt from a given file path, or the default prompt.txt.
    
    If product is provided, fills placeholders:
      {name}, {category}, {price_line}, {description}
    """
    path = prompt_file or PROMPT_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except FileNotFoundError:
        raise FileNotFoundError(f"Prompt file not found at {path}. Please create it.")

    if product:
        name = product.get("name") or ""
        category = product.get("category") or ""
        price = product.get("price")
        if price is None or price == "":
            price_line = ""
        elif isinstance(price, str):
            price_line = price if price.startswith("$") else f"${price}"
        else:
            price_line = f"${price:.2f}"
        description = product.get("details") or product.get("description") or ""

        text = text.replace("{name}", name)
        text = text.replace("{category}", category)
        text = text.replace("{price_line}", price_line)
        text = text.replace("{description}", description)

    return text




def upload_image(file_path: str) -> str:
    """Upload a local image and return its public URL."""
    with open(file_path, "rb") as f:
        response = requests.post(
            UPLOAD_URL,
            headers=HEADERS_AUTH,
            files={"file": (os.path.basename(file_path), f, "image/jpeg")},
            data={"uploadPath": "screenshots"},
        )
    response.raise_for_status()
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"Upload failed: {data}")
    # Try common URL keys
    file_data = data["data"]
    url = file_data.get("fileUrl") or file_data.get("url") or file_data.get("downloadUrl")
    if not url:
        raise RuntimeError(f"Could not find URL in upload response: {file_data}")
    return url


def create_task(image_url: str, prompt_file: str = None, product: dict = None) -> str:
    """Submit image-to-image task and return task ID."""
    payload = {
        "model": "nano-banana-2",
        "input": {
            "prompt": load_prompt(prompt_file, product=product),
            "image_input": [image_url],
            "aspect_ratio": "auto",
        },
    }
    response = requests.post(
        CREATE_TASK_URL,
        headers={**HEADERS_AUTH, "Content-Type": "application/json"},
        json=payload,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 200:
        raise RuntimeError(f"Task creation failed: {data}")
    return data["data"]["taskId"]


def poll_task(task_id: str, timeout: int = 800) -> str:
    """Poll until task completes and return the result image URL."""
    start = time.time()
    interval = 15
    while time.time() - start < timeout:
        response = requests.get(
            GET_TASK_URL,
            headers=HEADERS_AUTH,
            params={"taskId": task_id},
        )
        response.raise_for_status()
        resp = response.json()
        app_code = resp.get("code")
        if app_code not in (200, None):
            raise RuntimeError(f"Poll error (code {app_code}): {resp.get('msg')}")
        data = resp.get("data", {})
        state = data.get("state")

        if state == "success":
            result = json.loads(data["resultJson"])
            return result["resultUrls"][0]
        elif state == "fail":
            raise RuntimeError(f"Task failed [{data.get('failCode', '')}]: {data.get('failMsg')}")

        print(f"  [{task_id}] state={state}, waiting {interval}s...")
        time.sleep(interval)
        interval = min(interval + 5, 30)

    raise TimeoutError(f"Task {task_id} timed out after {timeout}s")


def download_image(url: str, output_path: str):
    """Download image from URL to local file."""
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(response.content)


# ── RATE LIMITER ─────────────────────────────
# Allows up to MAX_PER_WINDOW submissions per WINDOW_SECONDS
MAX_PER_WINDOW = 18
WINDOW_SECONDS = 10

class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.lock = threading.Lock()
        self.timestamps: list[float] = []

    def acquire(self):
        while True:
            with self.lock:
                now = time.monotonic()
                self.timestamps = [t for t in self.timestamps if now - t < self.period]
                if len(self.timestamps) < self.max_calls:
                    self.timestamps.append(now)
                    return
            time.sleep(0.2)

rate_limiter = RateLimiter(MAX_PER_WINDOW, WINDOW_SECONDS)


def generate_images(input_folder: str = "screenshots", output_folder: str = "generated", prompt_file: str = None, products: list = None) -> dict:
    """Process all images in input_folder and save generated images to output_folder.
    Returns a dict mapping input filename -> output path (or None on failure).

    If products is provided (list of product dicts with 'screenshot_file' key),
    prompt placeholders will be filled per image using the matching product.
    """
    # Build lookup: absolute screenshot path -> product
    screenshot_to_product: dict = {}
    if products:
        for p in products:
            sf = p.get("screenshot_file")
            if sf:
                screenshot_to_product[os.path.abspath(sf)] = p
    os.makedirs(output_folder, exist_ok=True)

    image_paths = sorted(
        p for p in glob.glob(os.path.join(input_folder, "**", "*"), recursive=True)
        if p.lower().endswith((".jpg", ".jpeg", ".png")) and os.path.isfile(p)
    )

    if not image_paths:
        print(f"  No images found in '{input_folder}/'")
        return {}

    print(f"  Generating {len(image_paths)} image(s) via KIE API...\n")
    total = len(image_paths)

    def process_image(i: int, filename: str):
        input_path = filename
        output_path = os.path.join(output_folder, os.path.basename(filename))
        label = f"  [{i}/{total}] {filename}"

        try:
            print(f"{label} — uploading...")
            image_url = upload_image(input_path)

            product = screenshot_to_product.get(os.path.abspath(input_path))
            if product:
                print(f"{label} — product: {product.get('name', '?')}")

            print(f"{label} — submitting task...")
            rate_limiter.acquire()
            task_id = create_task(image_url, prompt_file=prompt_file, product=product)

            print(f"{label} — task {task_id}, polling...")
            result_url = poll_task(task_id)

            print(f"{label} — downloading...")
            download_image(result_url, output_path)

            print(f"{label} — DONE → {output_path}")
            return filename, output_path

        except TimeoutError as e:
            print(f"{label} — SKIPPED (5-minute timeout exceeded): {e}")
            return filename, None
        except Exception as e:
            print(f"{label} — ERROR: {e}")
            return filename, None

    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(process_image, i, filename): filename
            for i, filename in enumerate(image_paths, 1)
        }
        for future in as_completed(futures):
            filename, output_path = future.result()
            results[filename] = output_path

    succeeded = sum(1 for v in results.values() if v)
    print(f"\n  Generated {succeeded}/{total} images successfully.")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate an image using KIE API")
    parser.add_argument("image", nargs="?", default=None, help="Path to the input image file")
    parser.add_argument("--prompt", default=None, help="Path to the prompt .txt file (defaults to prompt.txt)")
    parser.add_argument("--output", default=None, help="Output file path (defaults to generated/<input_filename>)")
    parser.add_argument("--test-prompt", action="store_true", help="Test placeholder substitution without calling the API")
    parser.add_argument("--name",        default="Mystery Snack Box (12 count)", help="Product name for --test-prompt")
    parser.add_argument("--category",    default="Snacks", help="Product category for --test-prompt")
    parser.add_argument("--price",       default="5.00", help="Product price for --test-prompt")
    parser.add_argument("--description", default="A delightful assortment of seasonal snacks perfect for sharing.", help="Product description for --test-prompt")
    args = parser.parse_args()

    # ── TEST MODE: print the filled prompt without calling the API ────────────
    if args.test_prompt:
        test_product = {
            "name":        args.name,
            "category":    args.category,
            "price":       float(args.price),
            "details":     args.description,
        }
        filled = load_prompt(args.prompt, product=test_product)
        print("=" * 60)
        print("FILLED PROMPT (placeholders substituted):")
        print("=" * 60)
        print(filled)
        print("=" * 60)
        raise SystemExit(0)

    # ── NORMAL MODE: generate one image ──────────────────────────────────────
    if not args.image:
        parser.error("'image' argument is required unless --test-prompt is used.")

    output_path = args.output or os.path.join("generated", os.path.basename(args.image))
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    print(f"Uploading {args.image}...")
    image_url = upload_image(args.image)

    print("Submitting task...")
    task_id = create_task(image_url, prompt_file=args.prompt)

    print(f"Task ID: {task_id} — polling...")
    result_url = poll_task(task_id)

    print(f"Downloading result to {output_path}...")
    download_image(result_url, output_path)
    print(f"Done → {output_path}")

