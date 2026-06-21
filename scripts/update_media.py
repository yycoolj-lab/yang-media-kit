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
    "震震有詞": {"network": "高點電視台", "channel_keywords": ["震震有詞"]},
    "醫次搞定": {"network": "", "channel_keywords": ["醫次搞定"]},
    "健康好生活": {"network": "年代", "channel_keywords": ["健康好生活"]},
    "命運好好玩": {"network": "JET", "channel_keywords": ["命運好好玩"]},
    "小明星大跟班": {"network": "中天", "channel_keywords": ["小明星大跟班"]},
    "醫師有話說": {"network": "", "channel_keywords": ["醫師有話說"]},
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
    "蘋果新聞網": ["appledaily.com"],
    "ELLE": ["elle.com"],
    "風傳媒": ["storm.mg"],
    "民視新聞": ["ftvnews.com.tw"],
    "華視新聞": ["news.cts.com.tw"],
    "鏡週刊": ["mirrormedia.mg"],
    "今周刊": ["businesstoday.com.tw"],
    "天下雜誌": ["cw.com.tw"],
    "商周": ["businessweekly.com.tw"],
}

HEALTH_MEDIA_DOMAINS = {
    "早安健康": {"domains": ["edh.tw"], "role": "專欄作者"},
    "康健雜誌": {"domains": ["commonhealth.com.tw"], "role": ""},
    "Heho健康": {"domains": ["heho.com.tw"], "role": ""},
    "良醫健康網": {"domains": ["health.businessweekly.com.tw"], "role": ""},
    "健康遠見": {"domains": ["health.gvm.com.tw"], "role": ""},
    "媽媽寶寶": {"domains": ["mombaby.com.tw"], "role": ""},
    "華人健康網": {"domains": ["top1health.com"], "role": ""},
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


def _add_url_variants(urls, url):
    if "youtu.be/" in url:
        vid = url.split("youtu.be/")[-1].split("?")[0]
        urls.add(vid)
    elif "youtube.com/watch" in url:
        vid = url.split("v=")[-1].split("&")[0]
        urls.add(vid)
    urls.add(url)


def get_existing_urls(data):
    urls = set()
    for section in ["tv_shows", "health_media", "news_media"]:
        for item in data.get(section, []):
            if item.get("url"):
                _add_url_variants(urls, item["url"])

    # Blocklist: items manually removed (irrelevant / same-name different person).
    # Never re-add these via auto search.
    removed_file = DATA_FILE.parent / "removed_items.json"
    if removed_file.exists():
        try:
            for item in json.loads(removed_file.read_text(encoding="utf-8")):
                if item.get("url"):
                    _add_url_variants(urls, item["url"])
        except Exception:
            pass

    return urls


def get_existing_titles(data):
    """Get set of existing titles for fuzzy dedup."""
    titles = set()
    for section in ["tv_shows", "health_media", "news_media"]:
        for item in data.get(section, []):
            if item.get("title"):
                # Normalize: remove spaces and common punctuation
                t = item["title"].strip()
                titles.add(t)
                # Also add a simplified version
                titles.add(re.sub(r'[\s　！!？?。，,、：:；;（）()【】\[\]「」『』]', '', t))
    return titles


def format_follower_count(count):
    """Format follower count in Chinese style with one-decimal 萬 (e.g. 5.3萬)."""
    if count >= 10000:
        wan = count / 10000
        if wan == int(wan):
            return f"{int(wan)}萬"
        else:
            return f"{wan:.1f}萬"
    return f"{count:,}"


def today_str():
    return datetime.now(TW_TZ).strftime("%Y-%m-%d")


def is_duplicate_title(title, existing_titles):
    """Check if title already exists (fuzzy match)."""
    if title in existing_titles:
        return True
    simplified = re.sub(r'[\s　！!？?。，,、：:；;（）()【】\[\]「」『』]', '', title)
    if simplified in existing_titles:
        return True
    return False


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
    """Fetch Google Maps rating using Playwright (headless browser)."""
    print("[GOOGLE] Fetching Google rating via Playwright...")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                locale="zh-TW",
            )
            page = context.new_page()

            strategies = [
                f"https://www.google.com/search?q={urllib.parse.quote(GOOGLE_PLACE_SEARCH)}&hl=zh-TW",
                f"https://www.google.com/search?q={urllib.parse.quote(GOOGLE_PLACE_SEARCH + ' 評價')}&hl=zh-TW",
                f"https://www.google.com/search?q={urllib.parse.quote('富足診所 台中 評價')}&hl=zh-TW",
            ]

            all_patterns = [
                r'"ratingValue"\s*:\s*"?(\d\.?\d?)"?',
                r'(\d\.?\d?)\s*顆星',
                r'(\d\.?\d?)</span>\s*<span[^>]*>\s*\(\d',
                r'rating["\s:]+(\d\.?\d?)',
                r'(\d\.\d)\s*分',
                r'<span[^>]*>(\d\.\d)</span>[^<]*(?:\d{2,3})\s*則',
            ]

            for search_url in strategies:
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2000)
                    content = page.content()

                    for pattern in all_patterns:
                        for match in re.finditer(pattern, content):
                            try:
                                rating = float(match.group(1))
                                if 3.0 <= rating <= 5.0:
                                    old = data["stats"]["google_rating"].get("score", 0)
                                    data["stats"]["google_rating"]["score"] = rating
                                    print(f"[GOOGLE] Updated rating: {old} -> {rating}")
                                    browser.close()
                                    return True
                            except (ValueError, IndexError):
                                continue
                except Exception as e:
                    print(f"[GOOGLE] Error with strategy: {e}")

            browser.close()

    except ImportError:
        print("[GOOGLE] Playwright not installed, skipping")
        return False
    except Exception as e:
        print(f"[GOOGLE] Error: {e}")
        return False

    print("[GOOGLE] Could not extract rating from any source")
    return False


# ─── DIRECT SITE SEARCH (primary method) ─────────────
# Scrape each media outlet's own search page directly.
# This avoids Google blocking and is more reliable than Google Search scraping.

# Site search configurations: (name, url_template, parser_function, max_pages)
SITE_SEARCH_CONFIGS = [
    {
        "name": "自由時報",
        "url_template": "https://search.ltn.com.tw/list?keyword={kw}&type=all&page={page}",
        "parser": "_parse_ltn",
        "max_pages": 3,
    },
    {
        "name": "ETtoday",
        "url_template": "https://www.ettoday.net/news_search/doSearch.php?search_term_string={kw}&page={page}",
        "parser": "_parse_ettoday",
        "max_pages": 2,
    },
    {
        "name": "UDN",
        "url_template": "https://udn.com/search/word/2/{kw}",
        "parser": "_parse_udn",
        "max_pages": 1,
    },
    {
        "name": "Heho",
        "url_template": "https://heho.com.tw/?s={kw}",
        "parser": "_parse_heho",
        "max_pages": 1,
    },
]


def search_media_sites(data):
    """Search each media outlet's own site directly — no Google needed."""
    print("[SEARCH] Direct site search (primary method)...")
    existing_urls = get_existing_urls(data)
    existing_titles = get_existing_titles(data)
    new_items = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9",
    }

    for config in SITE_SEARCH_CONFIGS:
        site_name = config["name"]
        parser_name = config["parser"]
        parser_fn = globals().get(parser_name)
        if not parser_fn:
            print(f"  [{site_name}] Parser not found: {parser_name}")
            continue

        found = 0
        for page_num in range(1, config["max_pages"] + 1):
            import time
            kw_encoded = urllib.parse.quote("楊智鈞")
            url = config["url_template"].format(kw=kw_encoded, page=page_num)

            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    print(f"  [{site_name}] HTTP {resp.status_code}")
                    break

                items = parser_fn(resp.text, existing_urls, existing_titles, data)
                found += len(items)
                new_items.extend(items)

                if len(items) == 0 and page_num > 1:
                    break

            except Exception as e:
                print(f"  [{site_name}] Error page {page_num}: {e}")
                break

            time.sleep(1)

        print(f"  [{site_name}] -> {found} new")

    print(f"[SEARCH] Found {len(new_items)} new articles from direct site search")
    return new_items


def _add_article(title, href, date_str, existing_urls, existing_titles, data):
    """Helper: add a single article if not duplicate. Returns the item or None."""
    if not title or len(title) < 5:
        return None

    href = re.sub(r'[?&](utm_\w+|fbclid|gclid)=[^&]*', '', href)

    if href in existing_urls:
        return None
    if is_duplicate_title(title, existing_titles):
        return None

    outlet = classify_outlet(href, "")
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
        "date": date_str,
        "url": href,
        "source": "auto_search",
        "added_date": today_str(),
    }
    if role:
        new_item["outlet_role"] = role

    data[category].append(new_item)
    existing_urls.add(href)
    existing_titles.add(title)
    print(f"  [+] [{outlet}] {title[:60]}...")
    return new_item


def _parse_ltn(html, existing_urls, existing_titles, data):
    """Parse 自由時報 search results. Verify relevance by checking if article
    snippet/context mentions the doctor's name (LTN search can return broad matches)."""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for div in soup.find_all("div", class_="cont"):
        a = div.find("a", href=re.compile(r'ltn\.com\.tw/article'))
        if not a:
            continue
        href = a.get("href", "")
        tit = div.find(class_="tit")
        title = tit.get_text(strip=True) if tit else a.get_text(strip=True)

        # Relevance check: the search result context should mention the doctor
        context_text = div.get_text(" ", strip=True)
        if not any(n in context_text for n in SEARCH_NAMES + ["富足診所", "俠醫"]):
            continue

        date_str = ""
        time_el = div.find(class_="time")
        if time_el:
            raw = time_el.get_text(strip=True)
            dm = re.search(r'(\d{4})/(\d{2})/(\d{2})', raw)
            if dm:
                date_str = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"

        item = _add_article(title, href, date_str, existing_urls, existing_titles, data)
        if item:
            items.append(item)
    return items


def _parse_ettoday(html, existing_urls, existing_titles, data):
    """Parse ETtoday search results. ETtoday search already filters by keyword,
    so articles may not have the name in the title — verify via snippet."""
    soup = BeautifulSoup(html, "lxml")
    items = []
    # Collect all links with titles (skip image links with empty text)
    seen = set()
    candidates = []
    for a in soup.find_all("a", href=re.compile(r'ettoday\.net/news/')):
        href = a.get("href", "")
        if not href.startswith("http"):
            href = "https:" + href if href.startswith("//") else "https://www.ettoday.net" + href
        title = a.get_text(strip=True)
        if title and len(title) >= 8 and href not in seen:
            seen.add(href)
            candidates.append((title, href, a))

    for title, href, a_tag in candidates:
        # Verify relevance: check snippet/parent context for doctor's name
        parent = a_tag.find_parent(["div", "li", "td"])
        snippet = parent.get_text(" ", strip=True) if parent else title
        if not any(n in snippet for n in SEARCH_NAMES + ["俠醫", "富足診所"]):
            continue

        date_str = ""
        if parent:
            dm = re.search(r'(\d{4})[/-](\d{2})[/-](\d{2})', parent.get_text(" "))
            if dm:
                date_str = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"

        item = _add_article(title, href, date_str, existing_urls, existing_titles, data)
        if item:
            items.append(item)
    return items


def _parse_udn(html, existing_urls, existing_titles, data):
    """Parse UDN search results. Only take results with 'from=searchresult' to avoid
    unrelated sidebar/trending news."""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for a in soup.find_all("a", href=re.compile(r'udn\.com/news/story/.*searchresult')):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not title or len(title) < 8:
            continue
        # Clean tracking params
        href = re.sub(r'\?from=.*$', '', href)

        date_str = ""
        parent = a.find_parent(["div", "li"])
        if parent:
            dm = re.search(r'(\d{4})[/-](\d{2})[/-](\d{2})', parent.get_text(" "))
            if dm:
                date_str = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"

        item = _add_article(title, href, date_str, existing_urls, existing_titles, data)
        if item:
            items.append(item)
    return items


def _parse_heho(html, existing_urls, existing_titles, data):
    """Parse Heho health search results."""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for a in soup.find_all("a", href=re.compile(r'heho\.com\.tw/')):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not title or len(title) < 8:
            continue
        if not any(n in title for n in SEARCH_NAMES):
            continue
        if '/archives/' not in href and '/article/' not in href:
            continue

        date_str = ""
        parent = a.find_parent(["div", "li", "article"])
        if parent:
            time_el = parent.find("time")
            if time_el:
                dt = time_el.get("datetime", "")
                if dt:
                    date_str = dt[:10]

        item = _add_article(title, href, date_str, existing_urls, existing_titles, data)
        if item:
            items.append(item)
    return items


# ─── GOOGLE NEWS RSS (supplementary) ────────────────

def search_google_news(data):
    """Search Google News RSS as a supplement to Google Search."""
    print("[NEWS] Google News RSS (supplementary)...")
    existing_urls = get_existing_urls(data)
    existing_titles = get_existing_titles(data)
    new_items = []

    for name in SEARCH_NAMES:
        rss_url = (
            f"https://news.google.com/rss/search?"
            f"q={urllib.parse.quote(name)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        )
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:30]:
                title = entry.get("title", "")
                link = entry.get("link", "")

                actual_url = resolve_google_news_url(link)
                if not actual_url:
                    actual_url = link

                if actual_url in existing_urls or link in existing_urls:
                    continue

                if not any(n in title for n in SEARCH_NAMES):
                    continue

                pub_date = ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    dt = datetime(*entry.published_parsed[:6])
                    pub_date = dt.strftime("%Y-%m-%d")

                source_name = ""
                if " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title = parts[0].strip()
                    source_name = parts[1].strip()

                if is_duplicate_title(title, existing_titles):
                    continue

                outlet = classify_outlet(actual_url, source_name)
                category = determine_category(outlet)

                new_item = {
                    "id": make_id(category[:2], outlet, title),
                    "outlet": outlet,
                    "title": title,
                    "date": pub_date,
                    "url": actual_url,
                    "source": "auto_search",
                    "added_date": today_str(),
                }

                data[category].append(new_item)
                existing_urls.add(actual_url)
                existing_titles.add(title)
                new_items.append(new_item)
                print(f"  [+] [{outlet}] {title[:60]}...")

        except Exception as e:
            print(f"[NEWS] RSS error for '{name}': {e}")

    print(f"[NEWS] Found {len(new_items)} new from RSS")
    return new_items


def resolve_google_news_url(google_url):
    """Try to resolve Google News redirect URL to the actual article URL."""
    try:
        resp = requests.head(google_url, allow_redirects=True, timeout=10)
        final_url = resp.url
        # Clean tracking params
        if "?" in final_url:
            base = final_url.split("?")[0]
            if any(ext in base for ext in [".html", ".htm", "/article/", "/news/", "/story/"]):
                return base
        return final_url
    except Exception:
        return None


def classify_outlet(url, source_name=""):
    """Match a URL or source name to a known outlet."""
    all_domains = {**NEWS_OUTLET_DOMAINS, **{k: v["domains"] for k, v in HEALTH_MEDIA_DOMAINS.items()}}
    for outlet_name, domains in all_domains.items():
        for domain in domains:
            if domain in url:
                return outlet_name

    if source_name:
        for outlet_name in list(NEWS_OUTLET_DOMAINS.keys()) + list(HEALTH_MEDIA_DOMAINS.keys()):
            if outlet_name.replace("／", "").replace("　", "") in source_name.replace(" ", ""):
                return outlet_name

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


# ─── YOUTUBE SEARCH (yt-dlp, improved) ───────────────

def search_youtube_shows(data):
    """Use yt-dlp to search YouTube for new TV show appearances."""
    print("[YT] Searching YouTube for new TV appearances...")
    existing_urls = get_existing_urls(data)
    existing_titles = get_existing_titles(data)
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

    # Strategy 1: Search by show name + doctor name
    for show_name, show_info in TV_SHOWS.items():
        for name in SEARCH_NAMES[:1]:
            query = f"{show_name} {name}"
            found = _yt_search(query, show_name, show_info, existing_urls, existing_titles, data, new_items, count=10)
            print(f"  [{show_name}] found {found} new")

    # Strategy 2: Generic search for doctor name on YouTube (catch unlisted shows)
    for name in SEARCH_NAMES:
        query = f"{name} 節目"
        _yt_search_generic(query, existing_urls, existing_titles, data, new_items, count=15)

    # Strategy 3: Search for doctor name + interview/專訪
    _yt_search_generic("楊智鈞 專訪", existing_urls, existing_titles, data, new_items, count=10)

    print(f"[YT] Found {len(new_items)} new TV appearances total")
    return new_items


def _yt_search(query, show_name, show_info, existing_urls, existing_titles, data, new_items, count=10):
    """Search YouTube for a specific show."""
    found = 0
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                f"ytsearch{count}:{query}",
                "--dump-json",
                "--no-download",
                "--flat-playlist",
            ],
            capture_output=True,
            text=True,
            timeout=60,
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

            if video_id in existing_urls or url in existing_urls:
                continue

            # Verify relevance: the DOCTOR'S NAME must appear in title or description.
            # (Matching only the show name lets other doctors' episodes through.)
            description = video.get("description", "") or ""
            text_to_check = title + " " + description
            if not any(n in text_to_check for n in SEARCH_NAMES):
                continue

            if is_duplicate_title(title, existing_titles):
                continue

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
            existing_titles.add(title)
            new_items.append(new_item)
            found += 1
            print(f"  [+] [{show_name}] {title[:60]}...")

    except subprocess.TimeoutExpired:
        print(f"[YT] Timeout searching for '{query}'")
    except Exception as e:
        print(f"[YT] Error searching for '{query}': {e}")

    return found


def _yt_search_generic(query, existing_urls, existing_titles, data, new_items, count=10):
    """Search YouTube generically — auto-detect which show it belongs to."""
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                f"ytsearch{count}:{query}",
                "--dump-json",
                "--no-download",
                "--flat-playlist",
            ],
            capture_output=True,
            text=True,
            timeout=60,
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
            channel = video.get("channel", "") or video.get("uploader", "") or ""
            url = f"https://youtu.be/{video_id}"

            if video_id in existing_urls or url in existing_urls:
                continue

            if not any(n in title for n in SEARCH_NAMES):
                continue

            if is_duplicate_title(title, existing_titles):
                continue

            # Try to detect which show this belongs to
            show_name = "網路直播/專訪"
            show_network = ""
            for sn, si in TV_SHOWS.items():
                if sn in title or sn in channel or any(kw in channel for kw in si["channel_keywords"]):
                    show_name = sn
                    show_network = si["network"]
                    break

            upload_date = video.get("upload_date", "")
            if upload_date and len(upload_date) == 8:
                pub_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
            else:
                pub_date = ""

            new_item = {
                "id": make_id("tv", show_name, title),
                "show": show_name,
                "show_network": show_network,
                "title": title,
                "date": pub_date,
                "url": url,
                "source": "auto_search",
                "added_date": today_str(),
            }

            data["tv_shows"].append(new_item)
            existing_urls.add(video_id)
            existing_urls.add(url)
            existing_titles.add(title)
            new_items.append(new_item)
            print(f"  [+] [{show_name}] {title[:60]}...")

    except subprocess.TimeoutExpired:
        print(f"[YT] Timeout for generic search '{query}'")
    except Exception as e:
        print(f"[YT] Error in generic search '{query}': {e}")


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

    # 3. Direct site search (primary — scrape each media outlet directly)
    site_news = search_media_sites(data)
    if site_news:
        changes = True

    # 4. Google News RSS (supplementary — catches things Google Search misses)
    news = search_google_news(data)
    if news:
        changes = True

    # 5. YouTube TV show search (expanded)
    yt = search_youtube_shows(data)
    if yt:
        changes = True

    # 6. Recalculate stats
    recalculate_stats(data)

    # 7. Save
    save_data(data)
    if changes:
        print(f"\n[DONE] Data updated with new content.")
    else:
        print(f"\n[DONE] No new content found, timestamp updated.")

    return changes


if __name__ == "__main__":
    has_changes = main()
    sys.exit(0)
