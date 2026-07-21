import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from dotenv import load_dotenv
import requests

load_dotenv()

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def extract_handle(channel_url: str) -> str:
    """
    Convert:
        https://www.youtube.com/@3rdShiftCouponer
    into:
        @3rdShiftCouponer
    """
    channel_url = channel_url.strip()

    if channel_url.startswith("@"):
        return channel_url

    parsed = urlparse(channel_url)
    path = parsed.path.strip("/")

    if path.startswith("@"):
        return path.split("/")[0]

    raise ValueError(
        "The channel URL must use a YouTube handle, for example: "
        "https://www.youtube.com/@3rdShiftCouponer"
    )


def resolve_channel(channel_url: str) -> dict:
    """
    Resolve a YouTube handle URL into:
    - channel ID
    - channel name
    - uploads playlist ID
    """
    handle = extract_handle(channel_url)

    response = requests.get(
        CHANNELS_URL,
        params={
            "part": "id,snippet,contentDetails",
            "forHandle": handle,
            "key": YOUTUBE_API_KEY,
        },
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    items = data.get("items", [])

    if not items:
        raise ValueError(f"No channel found for {handle}")

    channel = items[0]

    return {
        "channel_id": channel["id"],
        "channel_name": channel["snippet"]["title"],
        "handle": channel["snippet"].get("customUrl"),
        "uploads_playlist_id": (
            channel["contentDetails"]
            ["relatedPlaylists"]
            ["uploads"]
        ),
    }


def get_previous_weekday_window(
    weekday_name: str,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """
    Example:

    Run time:
        Tuesday, July 21, 2026 at 15:00

    Selected weekday:
        Tuesday

    Result:
        start = Tuesday, July 14, 2026 at 00:00
        end   = Tuesday, July 21, 2026 at 15:00
    """
    weekday_name = weekday_name.lower()

    if weekday_name not in WEEKDAYS:
        raise ValueError(f"Invalid weekday: {weekday_name}")

    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    target_weekday = WEEKDAYS[weekday_name]

    days_since_target = (
        now.weekday() - target_weekday
    ) % 7

    current_target_date = now - timedelta(
        days=days_since_target
    )

    current_target_start = current_target_date.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    # When today is the selected weekday,
    # move back exactly one week.
    if days_since_target == 0:
        start_date = current_target_start - timedelta(days=7)
    else:
        start_date = current_target_start

    return start_date, now


def parse_youtube_date(value: str) -> datetime:
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def get_playlist_videos_between(
    uploads_playlist_id: str,
    start_date: datetime,
    end_date: datetime,
) -> list[dict]:
    """
    Read the uploads playlist until videos become older
    than the requested start date.
    """
    videos = []
    page_token = None

    while True:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": 50,
            "key": YOUTUBE_API_KEY,
        }

        if page_token:
            params["pageToken"] = page_token

        response = requests.get(
            PLAYLIST_ITEMS_URL,
            params=params,
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        reached_old_video = False

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})

            video_id = content_details.get("videoId")
            published_value = content_details.get(
                "videoPublishedAt"
            )

            if not video_id or not published_value:
                continue

            published_at = parse_youtube_date(
                published_value
            )

            # Playlist is normally newest first.
            if published_at < start_date:
                reached_old_video = True
                continue

            if start_date <= published_at <= end_date:
                videos.append(
                    {
                        "video_id": video_id,
                        "title": snippet.get("title"),
                        "description": snippet.get(
                            "description"
                        ),
                        "published_at": published_at.isoformat(),
                        "url": (
                            "https://www.youtube.com/"
                            f"watch?v={video_id}"
                        ),
                        "thumbnail": (
                            snippet
                            .get("thumbnails", {})
                            .get("high", {})
                            .get("url")
                        ),
                    }
                )

        if reached_old_video:
            break

        page_token = data.get("nextPageToken")

        if not page_token:
            break

    videos.sort(
        key=lambda video: video["published_at"]
    )

    return videos


def get_channel_videos_since_weekday(
    channel_url: str,
    weekday: str,
) -> dict:
    channel = resolve_channel(channel_url)

    start_date, end_date = get_previous_weekday_window(
        weekday
    )

    videos = get_playlist_videos_between(
        uploads_playlist_id=channel[
            "uploads_playlist_id"
        ],
        start_date=start_date,
        end_date=end_date,
    )

    return {
        "channel": channel,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "video_count": len(videos),
        "videos": videos,
    }


if __name__ == "__main__":
    result = get_channel_videos_since_weekday(
        channel_url=(
            "https://www.youtube.com/"
            "@3rdShiftCouponer"
        ),
        weekday="saturday",
    )

    print(
        f"Channel: {result['channel']['channel_name']}"
    )
    print(f"From: {result['start_date']}")
    print(f"To:   {result['end_date']}")
    print(f"Videos found: {result['video_count']}")

    for video in result["videos"]:
        print()
        print(video["published_at"])
        print(video["title"])
        print(video["url"])