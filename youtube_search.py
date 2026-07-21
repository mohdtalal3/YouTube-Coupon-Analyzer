import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

LOGIN = os.getenv("DATAFORSEO_LOGIN")
PASSWORD = os.getenv("DATAFORSEO_PASSWORD")

MAX_DURATION_MINUTES = 40
MAX_DURATION_SECONDS = MAX_DURATION_MINUTES * 60

DATAFORSEO_URL = "https://api.dataforseo.com/v3/serp/youtube/organic/live/advanced"


def fetch_videos(keyword: str = "costco", max_videos: int = 10) -> list[dict]:
    """Search YouTube via DataForSEO and return the newest non-shorts/non-live videos.

    Returns a list of dicts with keys:
      title, youtube_url, video_id, channel_name, channel_url,
      description, views, published, timestamp, duration, duration_seconds, thumbnail
    """
    payload = [{
        "keyword": keyword,
        "language_code": "en",
        "location_code": 2840,
        "device": "desktop",
        "os": "windows",
        "block_depth": 20,
        "search_param": "EgIIBQ%253D%253D"  # Upload Date filter
    }]

    response = requests.post(
        DATAFORSEO_URL,
        json=payload,
        auth=HTTPBasicAuth(LOGIN, PASSWORD),
    )
    response.raise_for_status()
    data = response.json()

    videos = []
    for item in data["tasks"][0]["result"][0]["items"]:
        if item.get("type") != "youtube_video":
            continue
        if item.get("is_shorts"):
            continue
        if item.get("is_live"):
            continue

        duration_seconds = item.get("duration_time_seconds", 0)
        if duration_seconds > MAX_DURATION_SECONDS:
            continue

        videos.append({
            "title": item.get("title"),
            "youtube_url": f"https://www.youtube.com/watch?v={item['video_id']}",
            "video_id": item.get("video_id"),
            "channel_name": item.get("channel_name"),
            "channel_url": item.get("channel_url"),
            "description": item.get("description"),
            "views": item.get("views_count"),
            "published": item.get("publication_date"),
            "timestamp": item.get("timestamp"),
            "duration": item.get("duration_time"),
            "duration_seconds": duration_seconds,
            "thumbnail": item.get("thumbnail_url"),
        })

    # newest first
    videos.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    return videos[:max_videos]


if __name__ == "__main__":
    videos = fetch_videos()
    for i, video in enumerate(videos, start=1):
        print(f"\n{'=' * 80}")
        print(f"#{i}")
        print(f"Title: {video['title']}")
        print(f"URL: {video['youtube_url']}")
        print(f"Published: {video['published']}")
        print(f"Duration: {video['duration']}")
        print(f"Views: {video['views']}")
        print(f"Channel: {video['channel_name']}")