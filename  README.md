# YouTube Product Analyzer

**GitHub:** https://github.com/mohdtalal3/youtube-product-analyzer.git

An AI-powered YouTube product analyzer that extracts products from shopping videos, captures timestamp screenshots, generates store-style product images, and publishes structured product listings to WordPress from a Flask dashboard.

**Search keywords:** YouTube product analyzer, YouTube product extractor, YouTube scraper, AI video analyzer, YouTube to WordPress automation, AI product listing generator, shopping video analyzer, YouTube haul analyzer, WordPress auto publisher.

---

## What It Does

1. **Fetch** — Takes one or more YouTube URLs (or searches by keyword via DataForSEO)
2. **Transcribe** — Pulls auto-generated subtitles with timestamps
3. **Extract** — Uses **Grok AI** to identify every product mentioned, including name, price, description, and the exact timestamp where it's discussed. Deduplicates across multiple videos in the same run
4. **Screenshot** — Captures a video frame at each product's timestamp using `yt-dlp` + `ffmpeg`
5. **Generate** — Sends each screenshot to **KIE AI** to produce a photorealistic in-store image (optional, per-workspace prompt)
6. **Publish** — Uploads everything to a **WordPress** page as live or draft

All of this can be triggered through a clean **Flask web dashboard** — runs in the background, multiple jobs at once.

---

## Search-Friendly Keywords

- YouTube product extractor
- YouTube product analyzer
- YouTube video analyzer
- YouTube scraper
- AI video analyzer
- WordPress auto publisher
- AI product listing generator
- YouTube to WordPress automation
- Retail video analysis
- Shopping video analyzer
- YouTube haul analyzer
- Product extraction from video
- AI image generation for products
- Flask dashboard for automation

---

## Features

- **Flask dashboard** at `http://localhost:5001`
- **Workspaces** — separate configs per brand (ALDI, Costco, Sam's Club, etc.), each with its own default WordPress page ID and custom AI image prompt
- **Background jobs** — fire and forget; view live logs while the pipeline runs
- **AI image generation toggle** — enable/disable per run
- **Product limit** — cap how many products are extracted per video
- **Local-only mode** — save everything to disk without publishing
- **Publish or Draft** — choose WordPress status per run
- All run data saved under `runs/` — transcripts, products JSON, CSV, screenshots, generated images

---

## Project Structure

```
youtube-analyzer-bot/
├── app.py                  # Flask dashboard
├── run_full.py             # Main pipeline orchestrator
├── analyze.py              # Grok AI product extraction
├── generate.py             # KIE AI image generation
├── main.py                 # YouTube transcript fetching
├── screenshot.py           # yt-dlp + ffmpeg frame capture
├── publish_youtube.py      # WordPress publisher
├── wordpress_publisher.py  # WordPress REST API wrapper
├── youtube_search.py       # DataForSEO YouTube search (CLI only)
├── prompt.txt              # Default AI image generation prompt
├── COMMANDS.md             # Full CLI reference
│
├── templates/              # Flask HTML templates
│   ├── base.html
│   ├── index.html
│   ├── workspace_form.html
│   ├── workspace_detail.html
│   ├── jobs.html
│   └── job_detail.html
│
├── data/                   # Dashboard state (gitignored)
│   ├── workspaces.json
│   ├── jobs.json
│   └── logs/<job_id>.log
│
└── runs/                   # Pipeline output (gitignored)
    └── run_<keyword>_<video_id>/
        ├── videos.json
        ├── products.json       # All products aggregated
        ├── products.csv
        ├── screenshots/
        ├── generated/
        └── video_01_<id>/
            ├── transcript.json
            └── products.json
```

---

## Requirements

- Python 3.11+
- `yt-dlp` and `ffmpeg` installed and on `PATH`
- API keys (see Environment Variables below)

### Install Python dependencies

```bash
pip install flask openai httpx pydantic requests python-dotenv youtube_transcript_api
```

---

## Environment Variables

Create a `.env` file in the project root:

```env
# xAI (Grok) — product extraction
XAI_API_KEY=your_xai_key

# KIE AI — image generation
KIE_API_KEY=your_kie_key

# DataForSEO — YouTube search (CLI only)
DATAFORSEO_LOGIN=your_login
DATAFORSEO_PASSWORD=your_password

# YouTube proxy (optional)
YT_PROXY=http://user:pass@host:port

# WordPress site 1
WP_URL=https://yoursite.com
WP_USERNAME=your_wp_user
WP_PASSWORD=your_wp_app_password

# WordPress site 2 (optional second site)
WP_URL_RS=https://yoursite2.com
WP_USERNAME_RS=your_wp_user2
WP_PASSWORD_RS=your_wp_app_password2
```

> WordPress passwords should be [Application Passwords](https://make.wordpress.org/core/2020/11/05/application-passwords-integration-guide/) (Settings → Users → Application Passwords).

---

## Running the Dashboard

```bash
python3 app.py
```

Open **http://localhost:5001** in your browser.

### Dashboard workflow

1. **Create a Workspace** — give it a name (e.g. "ALDI"), set a default WordPress page ID, and optionally paste a custom AI image prompt
2. **Open the workspace** → paste YouTube URLs (one per line)
3. Configure options: page ID override, publish/draft, product limit, AI images on/off
4. Click **Run Bot** — the pipeline starts in the background
5. View live logs on the Job page; dashboard updates automatically

---

## CLI Usage

### Full pipeline — keyword search

```bash
python3 run_full.py --keyword "costco" --max-videos 10 --page-id 12345 --publish
```

### Full pipeline — custom URLs

```bash
python3 run_full.py \
  --urls \
    "https://www.youtube.com/watch?v=abc123" \
    "https://www.youtube.com/watch?v=def456" \
  --page-id 304402 \
  --publish
```

### Save locally only (no WordPress)

```bash
python3 run_full.py --urls "https://..." --no-publish
```

### Skip AI image generation

```bash
python3 run_full.py --urls "https://..." --page-id 12345 --publish --no-images
```

### Limit products per video

```bash
python3 run_full.py --urls "https://..." --page-id 12345 --publish --product-limit 5
```

### Use a custom image prompt file

```bash
python3 run_full.py --urls "https://..." --page-id 12345 --publish --prompt-file /path/to/my_prompt.txt
```

### Re-publish an existing run folder

```bash
python3 publish_youtube.py --run-dir runs/run_custom_abc123 --page-id 12345 --publish
```

### Full flag reference

| Flag | Default | Description |
|---|---|---|
| `--urls` | — | One or more YouTube URLs (skips search) |
| `--keyword` | `costco` | Search keyword (used if no `--urls`) |
| `--max-videos` | `10` | Number of search results to process |
| `--page-id` | — | WordPress page ID to publish to |
| `--publish` | — | Publish live to WordPress |
| `--draft` | — | Save as WordPress draft |
| `--no-publish` | — | Skip WordPress entirely |
| `--no-images` | — | Skip KIE AI image generation |
| `--product-limit` | all | Max products to keep per video |
| `--prompt-file` | `prompt.txt` | Custom image generation prompt |
| `--title` | — | Override WordPress page title |

---

## How Product Extraction Works

`analyze.py` sends each video's timestamped transcript to **Grok** (`grok-4.3`) with a structured prompt. It extracts:

- **Name** — full descriptive name with size/count/variant
- **Price** — exact price if stated, `null` otherwise
- **Details** — 30–40 word product description combining what the video says with background knowledge
- **Start time** — the earliest timestamp (seconds) where the product is first mentioned

When processing multiple videos in one run, each call passes the previous response ID so Grok automatically skips products already extracted from earlier videos in the same session.

---

## Workspaces

Workspaces store per-brand configuration:

| Field | Description |
|---|---|
| Name | Display name (e.g. ALDI, Costco) |
| Default Page ID | WordPress page ID used unless overridden at run time |
| Image Prompt | Custom text sent to KIE AI for every product image in this workspace. Falls back to `prompt.txt` if not set |

Workspace data is stored in `data/workspaces.json`.

---

## Output Files

Each pipeline run creates a folder under `runs/`:

| File | Contents |
|---|---|
| `videos.json` | Metadata for all videos processed |
| `products.json` | All extracted products (aggregated) |
| `products.csv` | Same data in CSV with image paths |
| `screenshots/` | Raw video frame captures (JPG) |
| `generated/` | AI-generated store images (JPG) |
| `video_XX_<id>/transcript.json` | Raw transcript segments |
| `video_XX_<id>/products.json` | Products from that video only |

---