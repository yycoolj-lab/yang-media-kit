#!/usr/bin/env python3
"""
Auto-update script for yang-media-kit.
Fetches Facebook followers, Google rating, new articles, and TV appearances.
Updates data.json — designed to run in GitHub Actions (no API keys needed).
"""

import json
import hashlib
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

# ─── CONFIG ──────────────────────────────────────────

DATA_FILE = Path(__file__).parent.parent / "data.json"
TW_TZ = timezone(timedelta(hours=8))

# Doctor's name variants for search
SEARCH_NAMES = ["楊智鈞", "俠醫楊智鈞"]

# Facebook page
FACEBOOK_PAGE_URL = "https://www.facebook.com/good.leg.clinic/"
FACEBOOK_PAGE_ID = "good.leg.clinic"

# Google Maps Place ID for rating
GOOGLE_PLACE_SEARCH = "富足診所"

# Known TV shows to search on YouTube
TV_SHOWS = {
    "醫師好辣": {"network": "東森", "channel_keywords": ["醫師好辣"]},
    "全民星攻略": {"network": "衛視中文台", "channel_keywords": ["全民星攻略"]},
    "健康2.0": {"network": "TVBS", "channel_keywords": ["健康2.0", "TVBS"]},
    "聚焦2.0": {"network": "年代", "channel_keywords": ["聚焦2.0", "聚焦"]},
    "祝你健康": {"network": "", "channel_keywords": ["祝你健康"]},
}

# Known news outlet domains for classification
NEWS_OUTLET_DOMAINS = {
    "自由時報": ["ltn.com.tw"],
    "聯合報／元氣網": ["udn.com"],
    "ETtoday": ["ettoday.net"],
    "TVBS 健康2.0": ["tvbs.com.tw"],
    "CTWANT／周刊王": ["ctwant.com"],
    "Yahoo 新聞": ["tw.news.yahoo.com", "yahoo.com"],
    "LINE TODAY": ["today.line.me"],
    "中時新聞網": ["chinatimes.com"],
    "三立新聞": ["setn.com"],
    "NOWnews": ["nownews.com"],
    "匯流新聞網": ["cnews.com.tw"],
}

HEALTH_MEDIA_DOMAINS = {
    "早安健康": {"domains": ["edh.tw"], "role": "專欄作者"},
    "康健雜誌": {"domains": ["commonhealth.com.tw"], "role": ""},
    "Heho健康": {"domains": ["heho.com.tw"], "role": ""},
}


# ─── UTILITIES ───────────────────────────────────────

def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "last_updated": None,
        "stats": {},
        "tv_shows": [],
        "health_media": [],
        "news_media": [],
    }


def save_data(data):
    data["last_updated"] = datetime.now(TW_TZ).isoformat()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def make_id(category, outlet, title):
    raw = f"{category}|{outlet}|{title}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"{category}-{h}"


def get_existing_urls(data):
    urls = set()
    for section in ["tv_shows", "health_media", "news_media"]:
        for item in data.get(section, []):
            if item.get("url"):
                # Normalize YouTube URLs
                url = item["url"]
                if "youtu.be/" in url:
                    vid = url.split("youtu.be/")[-1].split("?")[0]
                    urls.add(vid)
                elif "youtube.com/watch" in url:
                    vid = url.split("v=")[-1].split("&")[0]
                    urls.add(vid)
                urls.add(url)
    return urls


def format_follower_count(count):
    """Format follower count in Chinese style."""
    if count >= 10000:
        wan = count / 10000
        if wan == int(wan):
            return f"{int(wan)}萬+"
        else:
            return f"{wan:.1f}萬+"
    return f"{count:,}+"


def today_str():
    return datetime.now(TW_TZ).strftime("%Y-%m-%d")


# ─── FACEBOOK FOLLOWERS (Playwright) ─────────────────

def update_facebook_followers(data):
    """Use Playwright headless browser to read Facebook page follower count."""
    print("[FB] Fetching follower count...")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="zh-TW",
            )
            page = context.new_page()
            page.goto(FACEBOOK_PAGE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            content = page.content()
            browser.close()

        # Try to extract follower count from page content
        # Facebook shows counts like "49,234位追蹤者" or "4.9萬位追蹤者"
        patterns = [
            r'([\d,]+)\s*位追蹤者',
            r'([\d.]+)\s*萬\s*位?追蹤者',
            r'([\d,]+)\s*followers',
            r'"follower_count":\s*(\d+)',
            r'([\d,]+)\s*人追蹤',
        ]

        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                raw = match.group(1)
                if '萬' in pattern:
                    count = int(float(raw) * 10000)
                else:
                    count = int(raw.replace(',', ''))

                if count > 1000:  # Sanity check
                    old = data["stats"]["facebook_followers"].get("count", 0)
                    data["stats"]["facebook_followers"]["count"] = count
                    data["stats"]["facebook_followers"]["display"] = format_follower_count(count)
                    print(f"[FB] Updated: {old} -> {count} ({data['stats']['facebook_followers']['display']})")
                    return True

        print("[FB] Could not extract follower count from page")
        return False

    except ImportError:
        print("[FB] Playwright not installed, skipping")
        return False
    except Exception as e:
        print(f"[FB] Error: {e}")
        return False


# ─── GOOGLE RATING (Scraping) ────────────────────────

def update_google_rating(data):
    """Fetch Google Maps rating for the clinic."""
    print("[GOOGLE] Fetching Google rating...")
    try:
        # Use Google Maps search to find the place and extract rating
        search_url = f"https://www.google.com/search?q={urllib.parse.quote(GOOGLE_PLACE_SEARCH)}+評價&hl=zh-TW"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-TW,zh;q=0.9",
        }
        resp = requests.get(search_url, headers=headers, timeout=15)
        resp.raise_for_status()

        # Look for rating pattern like "4.9" near "顆星" or rating indicators
        patterns = [
            r'(\d\.\d)\s*顆星',
            r'rating["\s:]+(\d\.\d)',
            r'(\d\.\d)</span>\s*<span[^>]*>\s*\(\d',
        ]

        for pattern in patterns:
            match = re.search(pattern, resp.text)
            if match:
                rating = float(match.group(1))
                if 1.0 <= rating <= 5.0:
                    old = data["stats"]["google_rating"].get("score", 0)
                    data["stats"]["google_rating"]["score"] = rating
                    print(f"[GOOGLE] Updated rating: {old} -> {rating}")
                    return True

        print("[GOOGLE] Could not extract rating from search results")
        return False

    except Exception as e:
        print(f"[GOOGLE] Error: {e}")
        return False


# ─── GOOGLE NEWS RSS ─────────────────────────────────

def search_google_news(data):
    """Search Google News RSS for new articles mentioning the doctor."""
    print("[NEWS] Searching Google News RSS...")
    existing_urls = get_existing_urls(data)
    new_items = []

    for name in SEARCH_NAMES:
        rss_url = (
            f"https://news.google.com/rss/search?"
            f"q={urllib.parse.quote(name)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        )
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:15]:  # Check latest 15 entries
                title = entry.get("title", "")
                link = entry.get("link", "")

                # Google News links redirect - try to get actual URL
                actual_url = resolve_google_news_url(link)
                if not actual_url:
                    actual_url = link

                # Skip if already exists
                if actual_url in existing_urls or link in existing_urls:
                    continue

                # Verify relevance: title must contain doctor name
                if not any(n in title for n in SEARCH_NAMES):
                    continue

                # Extract date
                pub_date = ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    dt = datetime(*entry.published_parsed[:6])
                    pub_date = dt.strftime("%Y-%m-%d")

                # Extract source from title (Google News format: "Title - Source")
                source_name = ""
                if " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title = parts[0].strip()
                    source_name = parts[1].strip()

                # Classify by outlet domain
                outlet = classify_outlet(actual_url, source_name)
                category = determine_category(outlet)

                role = ""
                if category == "health_media":
                    for hm_name, hm_info in HEALTH_MEDIA_DOMAINS.items():
                        if outlet == hm_name:
                            role = hm_info.get("role", "")
                            break

                new_item = {
                    "id": make_id(category[:2], outlet, title),
                    "outlet": outlet,
                    "title": title,
                    "date": pub_date,
                    "url": actual_url,
                    "source": "auto_search",
                    "added_date": today_str(),
                }
                if role:
                    new_item["outlet_role"] = role

                data[category].append(new_item)
                existing_urls.add(actual_url)
                new_items.append(new_item)
                print(f"  [+] [{outlet}] {title[:60]}...")

        except Exception as e:
            print(f"[NEWS] RSS parse error for '{name}': {e}")

    print(f"[NEWS] Found {len(new_items)} new articles")
    return new_items


def resolve_google_news_url(google_url):
    """Try to resolve Google News redirect URL to the actual article URL."""
    try:
        resp = requests.head(google_url, allow_redirects=True, timeout=10)
        final_url = resp.url
        # Clean tracking params
        if "?" in final_url:
            base = final_url.split("?")[0]
            # Keep the base URL if it looks like a real article URL
            if any(ext in base for ext in [".html", ".htm", "/article/", "/news/"]):
                return base
        return final_url
    except Exception:
        return None


def classify_outlet(url, source_name=""):
    """Match a URL or source name to a known outlet."""
    # Check URL domain first
    for outlet_name, domains in {**NEWS_OUTLET_DOMAINS, **{k: v["domains"] for k, v in HEALTH_MEDIA_DOMAINS.items()}}.items():
        for domain in domains:
            if domain in url:
                return outlet_name

    # Check source name from Google News
    if source_name:
        for outlet_name in list(NEWS_OUTLET_DOMAINS.keys()) + list(HEALTH_MEDIA_DOMAINS.keys()):
            if outlet_name.replace("／", "").replace("　", "") in source_name.replace(" ", ""):
                return outlet_name

    # Unknown outlet - use source name or domain
    if source_name:
        return source_name
    try:
        domain = urllib.parse.urlparse(url).netloc
        return domain.replace("www.", "")
    except Exception:
        return "其他媒體"


def determine_category(outlet):
    """Determine which data section an outlet belongs to."""
    if outlet in HEALTH_MEDIA_DOMAINS:
        return "health_media"
    return "news_media"


# ─── YOUTUBE SEARCH (yt-dlp) ─────────────────────────

def search_youtube_shows(data):
    """Use yt-dlp to search YouTube for new TV show appearances."""
    print("[YT] Searching YouTube for new TV appearances...")
    existing_urls = get_existing_urls(data)
    new_items = []

    # Check if yt-dlp is available
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[YT] yt-dlp not installed, trying pip install...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"],
                         capture_output=True, check=True)
        except Exception:
            print("[YT] Could not install yt-dlp, skipping YouTube search")
            return new_items

    for show_name, show_info in TV_SHOWS.items():
        for name in SEARCH_NAMES[:1]:  # Just primary name to save time
            query = f"{show_name} {name}"
            try:
                result = subprocess.run(
                    [
                        "yt-dlp",
                        f"ytsearch5:{query}",
                        "--dump-json",
                        "--no-download",
                        "--flat-playlist",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                for line in result.stdout.strip().split("\n"):
                    if not line:
                        continue
                    try:
                        video = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    video_id = video.get("id", "")
                    title = video.get("title", "")
                    url = f"https://youtu.be/{video_id}"

                    # Skip if already exists
                    if video_id in existing_urls or url in existing_urls:
                        continue

                    # Verify relevance
                    if not any(n in title for n in SEARCH_NAMES + [show_name]):
                        continue

                    # Extract upload date
                    upload_date = video.get("upload_date", "")
                    if upload_date and len(upload_date) == 8:
                        pub_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
                    else:
                        pub_date = ""

                    new_item = {
                        "id": make_id("tv", show_name, title),
                        "show": show_name,
                        "show_network": show_info["network"],
                        "title": title,
                        "date": pub_date,
                        "url": url,
                        "source": "auto_search",
                        "added_date": today_str(),
                    }

                    data["tv_shows"].append(new_item)
                    existing_urls.add(video_id)
                    existing_urls.add(url)
                    new_items.append(new_item)
                    print(f"  [+] [{show_name}] {title[:60]}...")

            except subprocess.TimeoutExpired:
                print(f"[YT] Timeout searching for '{query}'")
            except Exception as e:
                print(f"[YT] Error searching for '{query}': {e}")

    print(f"[YT] Found {len(new_items)} new TV appearances")
    return new_items


# ─── RECALCULATE STATS ───────────────────────────────

def recalculate_stats(data):
    """Update computed stat counts based on actual data."""
    tv_count = len(data.get("tv_shows", []))
    news_count = len(data.get("news_media", [])) + len(data.get("health_media", []))

    data["stats"]["tv_episodes"]["count"] = tv_count
    data["stats"]["tv_episodes"]["display"] = f"{tv_count}+"
    data["stats"]["media_exposure"]["count"] = tv_count + news_count
    data["stats"]["media_exposure"]["display"] = f"{tv_count + news_count}+"
    print(f"[STATS] TV episodes: {tv_count}+, Media exposure: {tv_count + news_count}+")


# ─── MAIN ────────────────────────────────────────────

def main():
    print(f"{'='*60}")
    print(f"[START] Media Kit Update - {datetime.now(TW_TZ).isoformat()}")
    print(f"{'='*60}")

    data = load_data()
    changes = False

    # 1. Facebook followers
    if update_facebook_followers(data):
        changes = True

    # 2. Google rating
    if update_google_rating(data):
        changes = True

    # 3. Google News search for articles
    news = search_google_news(data)
    if news:
        changes = True

    # 4. YouTube TV show search
    yt = search_youtube_shows(data)
    if yt:
        changes = True

    # 5. Recalculate stats
    recalculate_stats(data)

    # 6. Save
    save_data(data)
    if changes:
        print(f"\n[DONE] Data updated with new content.")
    else:
        print(f"\n[DONE] No new content found, timestamp updated.")

    return changes


if __name__ == "__main__":
    has_changes = main()
    # Exit code 0 regardless - let GitHub Actions check git diff
    sys.exit(0)
