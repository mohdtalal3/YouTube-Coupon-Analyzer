import json
import os
import subprocess
import yt_dlp
from dotenv import load_dotenv

load_dotenv()


def safe_filename(name: str, max_len: int = 60) -> str:
    return "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '(', ')')).strip()[:max_len]


def download_video(video_id: str, output_path: str, retries: int = 3) -> None:
    """Download video to a local file using yt-dlp (proxy-aware, with retries)."""
    proxy = os.getenv("YT_PROXY")
    ydl_opts = {
        "format": "bestvideo[height<=480][ext=mp4]/bestvideo[ext=mp4]",
        "outtmpl": output_path,
        "quiet": False,
        "no_warnings": True,
    }
    if proxy:
        ydl_opts["proxy"] = proxy

    url = f"https://www.youtube.com/watch?v={video_id}"
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            return
        except Exception as e:
            last_error = e
            print(f"  ⚠️  Download attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                print(f"  🔄 Retrying...")
    raise RuntimeError(f"Download failed after {retries} attempts: {last_error}")


def take_screenshots(products_file: str, video_id: str, output_folder: str = "screenshots") -> list[dict]:
    with open(products_file, "r", encoding="utf-8") as f:
        products = json.load(f)

    os.makedirs(output_folder, exist_ok=True)

    tmp_video = os.path.join(output_folder, f"_tmp_{video_id}.mp4")
    print(f"  Downloading video: {tmp_video}")
    try:
        download_video(video_id, tmp_video)
    except Exception as e:
        print(f"  ❌ Download failed: {e}")
        for p in products:
            p["screenshot_file"] = None
        return products

    try:
        for i, product in enumerate(products):
            start_time = product.get("start_time")
            name = product.get("name", f"product_{i+1}")

            if start_time is None:
                print(f"  [{i+1}] Skipping '{name}' — no start_time")
                product["screenshot_file"] = None
                continue

            filename = f"{i+1:02d}_{safe_filename(name)}.jpg"
            output_path = os.path.join(output_folder, filename)

            cmd = [
                "ffmpeg", "-ss", str(start_time), "-i", tmp_video,
                "-frames:v", "1", "-q:v", "2", "-y", output_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)

            if proc.returncode == 0:
                print(f"  [{i+1}] Screenshot saved: {output_path}")
                product["screenshot_file"] = output_path
            else:
                print(f"  [{i+1}] Failed for '{name}': {proc.stderr[-200:]}")
                product["screenshot_file"] = None
    finally:
        if os.path.exists(tmp_video):
            os.remove(tmp_video)
            print(f"  🗑️  Temp video deleted")

    print(f"  Screenshots saved in '{output_folder}/'")
    return products
