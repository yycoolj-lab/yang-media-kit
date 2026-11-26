#!/usr/bin/env python3
"""
Auto-update script for yang-media-kit.
Fetches Facebook followers, Google rating, new articles, and TV appearances.
Updates data.json — designed to run in GitHub Actions (no API keys needed).
"""

import json
import hashlib
import os
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

# The doctor's own channel: source of the view/subscriber stats, and never
# counted as a media appearance.
OWN_YOUTUBE_URL = "https://www.youtube.com/@yycoolj"
OWN_CHANNELS = ["俠醫的血液循環頻道", "富足診所"]
PODCAST_ITUNES_ID = "1710937955"
BOOK_TITLES = ["如果佛陀握著手術刀"]
# Brand channels: their videos are endorsements, listed by hand, not media appearances.
BRAND_CHANNELS = ["大研生醫", "Daiken", "iRESTORE"]

# Video ids already checked and rejected (so each run only inspects new ones)
YT_SEEN_FILE = Path(__file__).parent / "yt_seen.json"
MAX_YT_CHECKS = int(os.environ.get("MAX_YT_CHECKS", "150"))
MAX_BACKFILL = int(os.environ.get("MAX_BACKFILL", "25"))
MIN_VIDEO_SECONDS = 75  # skip Shorts-length clips cut from full episodes

OWN_CHANNEL_IDS = {"UCQwaTIPz3jlpunrDCoyDJpA"}

# Channels whose RSS feed is checked every run (channel id -> name). Channels of
# any appearance found later are added automatically from data.json.
WATCH_CHANNELS = {
    "UCndzUZnInvxnJVzFn2rRDrw": "醫師好辣",
    "UCet3uxeYnTk3n1acNCNlJgA": "東森綜合台",
    "UCAqzHNzLTC6Vm5UYE7xw_2A": "健康2.0",
    "UCH0CYJBlFqswdlDdbXDnB-Q": "全民星攻略",
    "UCLt4uN5WixTqZHqgD05Eoiw": "聚焦2.0",
    "UCUocTbetB7_xJkV2zXBLtCw": "健康早知道",
    "UCLw13zx2c-zcyDiFXhNrpNA": "祝你健康",
    "UCZYbpFxslzhODUMDwS2Xt1g": "空中醫語教室 Med on Air",
    "UCb4CEGKZuAW8emEuP9JCKEg": "哇賽心理學",
    "UCHdR0x9YE1O3k2V3y0n_bIg": "來!聊醫下",
    "UCSF7m0Ad3hkYdrqrlNXxqkg": "健康主播鄭凱云",
    "UCOc2A2GJ9-WhjZSUO3nVEgQ": "九八新聞台",
    "UCUaf4I5TYCExf9mWzv1cB_A": "飛碟聯播網",
    "UCZhgu7uf-971SGC8zkwGEHA": "早安健康",
    "UCTPCYXkmdoQDHjCrhCTHyVQ": "就是愛健康",
    "UCQmSXClLgPY6PrSM3_h1XtQ": "17LIVE",
    "UCdq8Q2SVIkZKoUaB7iRDPDg": "高點電視",
}

# Channel id -> show, for channels whose displayed name varies by language.
CHANNEL_ID_TO_SHOW = {
    "UCndzUZnInvxnJVzFn2rRDrw": "醫師好辣",
    "UCAqzHNzLTC6Vm5UYE7xw_2A": "健康2.0",
    "UCmgDmqjxbkqIXu4rNbrKodA": "健康2.0",
    "UCH0CYJBlFqswdlDdbXDnB-Q": "全民星攻略",
    "UCLt4uN5WixTqZHqgD05Eoiw": "聚焦2.0",
    "UCUocTbetB7_xJkV2zXBLtCw": "健康早知道",
    "UCLw13zx2c-zcyDiFXhNrpNA": "祝你健康",
    "UCZYbpFxslzhODUMDwS2Xt1g": "空中醫語教室",
    "UCb4CEGKZuAW8emEuP9JCKEg": "哇賽心觀點",
    "UCHdR0x9YE1O3k2V3y0n_bIg": "來！聊醫下",
    "UCOc2A2GJ9-WhjZSUO3nVEgQ": "廣播節目",
    "UCUaf4I5TYCExf9mWzv1cB_A": "廣播節目",
}

# Searches that are not tied to one show (query, how many results)
GENERIC_YT_QUERIES = [
    ("楊智鈞醫師", 40),
    ("俠醫楊智鈞", 30),
    ("楊智鈞 心臟外科", 25),
    ("楊智鈞 靜脈曲張", 25),
    ("楊智鈞 專訪", 20),
    ("楊智鈞 富足診所", 20),
    ("如果佛陀握著手術刀", 20),
    ("feat.楊智鈞", 20),
]

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
    "健康早知道": {"network": "", "channel_keywords": ["健康早知道"]},
    "來！聊醫下": {"network": "", "channel_keywords": ["來!聊醫下", "來！聊醫下", "聊醫下"]},
    "哇賽心觀點": {"network": "哇賽心理學", "channel_keywords": ["哇賽心理學", "哇賽心觀點"]},    "空中醫語教室": {"network": "", "channel_keywords": ["空中醫語教室", "Med on Air"]},
    "廣播節目": {"network": "", "channel_keywords": ["九八新聞台", "飛碟聯播網", "中廣", "News98"]},
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

def fetch_facebook_followers_plugin():
    """Follower count from Facebook's Page Plugin, the widget meant to be embedded on
    other sites. It needs no login and is a plain HTTP request. Returns an int
    (rounded by Facebook to the nearest thousand) or None."""
    url = (
        "https://www.facebook.com/plugins/page.php?href="
        + urllib.parse.quote(FACEBOOK_PAGE_URL, safe="")
        + "&tabs&width=340&height=130&show_facepile=false&locale=zh_TW"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }
    html = requests.get(url, headers=headers, timeout=20).text
    m = re.search(r'"followerCountFormatted":"((?:[^"\\]|\\.)*)"', html)
    if not m:
        return None
    text = json.loads('"' + m.group(1) + '"').replace("\u00a0", " ").replace(",", "").strip()
    num = re.match(r'([\d.]+)\s*(萬|K|M|千)?', text)
    if not num:
        return None
    scale = {"萬": 10000, "K": 1000, "千": 1000, "M": 1000000}.get(num.group(2), 1)
    return int(round(float(num.group(1)) * scale))


def update_facebook_followers(data):
    """Read the Facebook page follower count: Page Plugin first, Playwright as backup."""
    print("[FB] Fetching follower count...")
    try:
        count = fetch_facebook_followers_plugin()
        old = data["stats"]["facebook_followers"].get("count", 0)
        if count and count > 1000 and count >= old * 0.9:
            # The plugin rounds to the nearest thousand. Keep a more precise stored
            # number unless the rounded one has actually moved past it.
            if format_follower_count(count) != format_follower_count(old):
                data["stats"]["facebook_followers"]["count"] = count
                data["stats"]["facebook_followers"]["display"] = format_follower_count(count)
                print(f"[FB] Updated via page plugin: {old} -> {count} ({format_follower_count(count)})")
                return True
            print(f"[FB] Page plugin: unchanged at {format_follower_count(old)}")
            return False
        print(f"[FB] Page plugin gave no usable number ({count}), trying browser...")
    except Exception as e:
        print(f"[FB] Page plugin error: {e}, trying browser...")

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

    news_terms = SEARCH_NAMES + BOOK_TITLES
    for name in news_terms:
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

                if not any(n in title for n in news_terms):
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

# ─── YOUTUBE: CHANNEL STATS / PODCAST COUNT ──────────

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "CONSENT=YES+1; SOCS=CAI",
}


def format_wan(count):
    """278415 -> '27.8萬'; small numbers stay as-is with thousands separators."""
    if count >= 10000:
        return f"{count / 10000:.1f}".rstrip("0").rstrip(".") + "萬"
    return f"{count:,}"


def update_youtube_stats(data):
    """Read total views / subscribers / video count from the channel's About page."""
    print("[YT-STATS] Fetching channel stats...")
    try:
        resp = requests.get(OWN_YOUTUBE_URL + "/about", headers=BROWSER_HEADERS, timeout=20)
        html = resp.text
        m = re.search(r'"viewCountText":"([\d,]+) views"', html)
        if not m:
            print("[YT-STATS] View count not found on page, keeping old value")
            return False
        views = int(m.group(1).replace(",", ""))
        yv = data["stats"].setdefault("youtube_views", {"url": OWN_YOUTUBE_URL})
        old = yv.get("count", 0)
        # Total views never go down by much; a collapse means we parsed the wrong thing.
        if views < old * 0.9:
            print(f"[YT-STATS] Suspicious value {views} (was {old}), keeping old value")
            return False
        yv["count"] = views
        yv["display"] = format_wan(views)
        s = re.search(r'"subscriberCountText":"([\d,.]+)(K|M)? subscribers"', html)
        if s:
            n = float(s.group(1).replace(",", ""))
            n *= {"K": 1000, "M": 1000000}.get(s.group(2), 1)
            yv["subscribers"] = int(n)
        v = re.search(r'"videoCountText":"([\d,]+) videos"', html)
        if v:
            yv["videos"] = int(v.group(1).replace(",", ""))
        print(f"[YT-STATS] views {old} -> {views} ({yv['display']}), subs {yv.get('subscribers')}, videos {yv.get('videos')}")
        return views != old
    except Exception as e:
        print(f"[YT-STATS] Error: {e}")
        return False


def update_podcast_count(data):
    """Episode count of the podcast, from Apple's public lookup API."""
    print("[PODCAST] Fetching episode count...")
    try:
        resp = requests.get(
            f"https://itunes.apple.com/lookup?id={PODCAST_ITUNES_ID}&country=tw", timeout=20
        )
        results = resp.json().get("results", [])
        if not results or not results[0].get("trackCount"):
            print("[PODCAST] No result, keeping old value")
            return False
        count = int(results[0]["trackCount"])
        pod = data["stats"].setdefault("podcast_episodes", {})
        old = pod.get("count", 0)
        if count < old:
            print(f"[PODCAST] Count went down ({old} -> {count}), keeping old value")
            return False
        pod["count"] = count
        pod["display"] = str(count)
        print(f"[PODCAST] episodes {old} -> {count}")
        return count != old
    except Exception as e:
        print(f"[PODCAST] Error: {e}")
        return False


# ─── YOUTUBE: TV / INTERVIEW DISCOVERY ───────────────

def load_seen():
    if YT_SEEN_FILE.exists():
        try:
            return json.loads(YT_SEEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_seen(seen):
    YT_SEEN_FILE.write_text(
        json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )


def yt_flat_search(query, count):
    """Cheap search. Each result carries the title, channel, duration and the
    description snippet YouTube shows under a search hit. No per-video requests,
    so this works from cloud machines where single-video lookups are blocked."""
    try:
        result = subprocess.run(
            ["yt-dlp", f"ytsearch{count}:{query}", "--dump-json", "--no-download",
             "--flat-playlist", "--no-warnings", "--extractor-args", "youtube:lang=zh-TW"],
            capture_output=True, text=True, timeout=90,
        )
    except subprocess.TimeoutExpired:
        print(f"[YT] Timeout searching '{query}'")
        return []
    out = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            v = json.loads(line)
        except json.JSONDecodeError:
            continue
        if v.get("id") and len(v["id"]) == 11:
            out.append({
                "id": v["id"],
                "title": v.get("title", "") or "",
                "description": v.get("description", "") or "",
                "tags": "",
                "channel": v.get("channel", "") or v.get("uploader", "") or "",
                "channel_id": v.get("channel_id", "") or "",
                "date": "",
                "duration": v.get("duration") or 0,
            })
    return out


def yt_video_meta(video_id):
    """Full metadata for one video: title, description, channel, upload date, duration.
    Tries yt-dlp first, then the watch page HTML. Returns None if both fail."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", "--no-warnings",
             "--extractor-args", "youtube:lang=zh-TW", "--", video_id],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            info = json.loads(result.stdout.strip().split("\n")[0])
            ud = info.get("upload_date") or ""
            return {
                "title": info.get("title", "") or "",
                "description": info.get("description", "") or "",
                "tags": " ".join(info.get("tags") or []),
                "channel": info.get("channel", "") or info.get("uploader", "") or "",
                "date": f"{ud[:4]}-{ud[4:6]}-{ud[6:8]}" if len(ud) == 8 else "",
                "duration": info.get("duration") or 0,
            }
    except Exception:
        pass

    # Fallback: the watch page embeds the same fields as JSON.
    try:
        headers = dict(BROWSER_HEADERS, **{"Accept-Language": "zh-TW,zh;q=0.9"})
        html = requests.get(f"https://www.youtube.com/watch?v={video_id}",
                            headers=headers, timeout=20).text

        def js_str(key):
            m = re.search(r'"%s":"((?:[^"\\]|\\.)*)"' % key, html)
            if not m:
                return ""
            try:
                return json.loads('"' + m.group(1) + '"')
            except Exception:
                return m.group(1)

        title = js_str("title")
        channel = js_str("ownerChannelName") or js_str("author")
        if not title or not channel:
            return None
        dm = re.search(r'"(?:uploadDate|publishDate)":"(\d{4}-\d{2}-\d{2})', html)
        ls = re.search(r'"lengthSeconds":"(\d+)"', html)
        return {
            "title": title,
            "description": js_str("shortDescription"),
            "tags": "",
            "channel": channel,
            "date": dm.group(1) if dm else "",
            "duration": int(ls.group(1)) if ls else 0,
        }
    except Exception:
        return None


ROLE_WORDS = ["來賓", "醫師", "名醫", "主任", "院長", "俠醫", "feat", "Feat", "專訪", "邀請", "外科", "主治"]


def _simplify(text):
    return re.sub(r'[\s　！!？?。，,、：:；;（）()【】\[\]「」『』｜|#＃]', '', text)


def episode_key(show, title):
    """Identify the broadcast episode a video belongs to, so that a full episode,
    its parts and its re-uploads count once. None when the title carries no marker."""
    m = re.search(r'EP\s?\.?(\d{2,4})', title, re.I) or re.search(r'第\s?(\d{2,4})\s?集', title)
    if m:
        return f"{show}|ep{int(m.group(1))}"
    m = re.search(r'(20[12]\d)[./]?(\d{2})[./]?(\d{2})', title)
    if m:
        return f"{show}|d{m.group(1)}{m.group(2)}{m.group(3)}"
    return None


def names_doctor(meta, show_name, existing_simplified):
    """True when the video really features the doctor, not merely mentions the name.

    A name in the title counts. A name in the description counts only on a line
    that also carries a role word (來賓／醫師／主任…), which rules out people who
    share the name. A description line that just repeats the title of a video we
    already list is a link to that other video, so it does not count either.
    """
    if any(n in meta["title"] for n in SEARCH_NAMES):
        return True
    for line in meta["description"].split("\n"):
        if not any(n in line for n in SEARCH_NAMES):
            continue
        simple = _simplify(line)
        if any(len(t) >= 12 and t[:12] in simple for t in existing_simplified):
            continue
        if any(w in line for w in ROLE_WORDS):
            return True
    # Hashtag-only mention: trust it only on a known show's channel.
    if any(n in meta["tags"] for n in SEARCH_NAMES) and show_name != "網路直播/專訪":
        return True
    return False


def classify_show(channel, title, channel_id=""):
    """Decide which show a video belongs to from its title and real channel."""
    for name, info in TV_SHOWS.items():
        if name in title:
            return name, info["network"]
    if channel_id and channel_id in CHANNEL_ID_TO_SHOW:
        name = CHANNEL_ID_TO_SHOW[channel_id]
        return name, TV_SHOWS[name]["network"]
    for name, info in TV_SHOWS.items():
        if any(kw in channel for kw in info["channel_keywords"]):
            return name, info["network"]
    return "網路直播/專訪", ""


_feed_cache = {}


def yt_channel_feed(channel_id):
    """A channel's public RSS feed: its 15 newest videos with the full description
    and the exact publish date. Works without login from anywhere."""
    if not channel_id:
        return []
    if channel_id in _feed_cache:
        return _feed_cache[channel_id]
    entries = []
    try:
        resp = requests.get(
            f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", timeout=20
        )
        feed = feedparser.parse(resp.content)
        channel = feed.feed.get("title", "")
        for e in feed.entries:
            vid = e.get("yt_videoid", "")
            if not vid:
                continue
            entries.append({
                "id": vid,
                "title": e.get("title", "") or "",
                "description": e.get("summary", "") or "",
                "tags": "",
                "channel": channel,
                "channel_id": channel_id,
                "date": (e.get("published", "") or "")[:10],
                "duration": 0,
            })
    except Exception as ex:
        print(f"[YT] Feed error for {channel_id}: {ex}")
    _feed_cache[channel_id] = entries
    return entries


def search_youtube_shows(data):
    """Find new TV / interview appearances on YouTube.

    Two sources of candidates:
      a. the RSS feeds of every channel the doctor has appeared on (newest uploads,
         full description, exact date);
      b. keyword searches (title plus the description snippet of each hit).
    When single-video lookups are possible (they are on a home connection, usually
    not on cloud machines) each search hit is also checked against its full
    description, and rejected ids are remembered in yt_seen.json.
    """
    print("[YT] Searching YouTube for new appearances...")
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        have_ytdlp = True
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[YT] yt-dlp not available, only channel feeds will be checked")
        have_ytdlp = False

    existing_urls = get_existing_urls(data)
    existing_titles = get_existing_titles(data)
    seen = load_seen()
    new_items = []
    existing_simplified = {_simplify(i["title"]) for i in data.get("tv_shows", [])}
    existing_episodes = {episode_key(i["show"], i["title"]) for i in data.get("tv_shows", [])}
    existing_episodes.discard(None)

    def consider(meta, full_description):
        """Apply every rule to one video. Returns (added, verdict)."""
        vid = meta["id"]
        show_name, network = classify_show(meta["channel"], meta["title"], meta.get("channel_id", ""))
        ep_key = episode_key(show_name, meta["title"])
        simple_title = _simplify(meta["title"])

        if meta.get("channel_id") in OWN_CHANNEL_IDS or any(own in meta["channel"] for own in OWN_CHANNELS):
            verdict = "own_channel"
        elif any(b in meta["channel"] for b in BRAND_CHANNELS):
            verdict = "brand_channel"
        elif not names_doctor(meta, show_name, existing_simplified):
            # With only a snippet we cannot be sure the name is absent.
            verdict = "no_name" if full_description else "unconfirmed"
        elif meta["duration"] and meta["duration"] < MIN_VIDEO_SECONDS:
            verdict = "too_short"
        elif is_duplicate_title(meta["title"], existing_titles) or any(
                len(t) >= 18 and t[:18] == simple_title[:18] for t in existing_simplified):
            verdict = "duplicate_title"
        elif ep_key and ep_key in existing_episodes:
            verdict = "same_episode"
        else:
            verdict = None
        if verdict:
            return False, verdict

        date = meta["date"]
        if not date:
            # The channel feed has exact dates for recent uploads.
            for e in yt_channel_feed(meta.get("channel_id", "")):
                if e["id"] == vid:
                    date = e["date"]
                    break
        new_item = {
            "id": make_id("tv", show_name, meta["title"]),
            "show": show_name,
            "show_network": network,
            "title": meta["title"],
            "date": date,
            "url": f"https://youtu.be/{vid}",
            "channel": meta["channel"],
            "channel_id": meta.get("channel_id", ""),
            "source": "auto_search",
            "added_date": today_str(),
        }
        data["tv_shows"].append(new_item)
        existing_urls.update([vid, new_item["url"]])
        existing_titles.add(meta["title"])
        existing_simplified.add(simple_title)
        if ep_key:
            existing_episodes.add(ep_key)
        new_items.append(new_item)
        print(f"  [+] [{show_name}] {date or '----'} {meta['channel']} | {meta['title'][:50]}")
        return True, None

    # a. Channel feeds
    watch = dict(WATCH_CHANNELS)
    for item in data.get("tv_shows", []):
        if item.get("channel_id") and item["channel_id"] not in OWN_CHANNEL_IDS:
            watch.setdefault(item["channel_id"], item.get("channel", ""))
    feed_checked = 0
    for channel_id in watch:
        for entry in yt_channel_feed(channel_id):
            if entry["id"] in existing_urls or entry["id"] in seen:
                continue
            feed_checked += 1
            consider(entry, full_description=True)
    print(f"[YT] Feeds: {len(watch)} channels, {feed_checked} recent videos checked")

    # b. Keyword searches
    if have_ytdlp:
        queries = [(f"{show} 楊智鈞", 12) for show in TV_SHOWS]
        queries += [(q, n) for q, n in GENERIC_YT_QUERIES]
        candidates = {}
        for query, count in queries:
            for hit in yt_flat_search(query, count):
                vid = hit["id"]
                if vid in existing_urls or vid in seen or vid in candidates:
                    continue
                candidates[vid] = hit
        print(f"[YT] Search: {len(candidates)} unseen candidates from {len(queries)} searches")

        ordered = sorted(candidates.values(),
                         key=lambda h: not any(n in h["title"] for n in SEARCH_NAMES))
        lookups = failures = 0
        lookups_work = True
        for hit in ordered:
            vid = hit["id"]
            if vid in existing_urls:
                continue
            full = None
            if lookups_work and lookups < MAX_YT_CHECKS:
                lookups += 1
                full = yt_video_meta(vid)
                if full is None:
                    failures += 1
                    if failures >= 3 and failures == lookups:
                        print("[YT] Single-video lookups are blocked here; using search snippets only")
                        lookups_work = False
                else:
                    full["id"] = vid
                    full["channel_id"] = hit["channel_id"]
            if full is not None:
                added, verdict = consider(full, full_description=True)
                if verdict:
                    seen[vid] = verdict
            else:
                consider(hit, full_description=False)
        print(f"[YT] Search: {lookups} full lookups ({failures} failed)")

    save_seen(seen)
    print(f"[YT] Added {len(new_items)} new appearances")
    return new_items


# ─── BACKFILL MISSING DATES ──────────────────────────

def backfill_dates(data):
    """Fill in missing dates, a few per run: YouTube upload dates for TV items,
    and published dates for articles. Items without a date sink to the bottom
    of the newest-first lists."""
    filled = 0
    budget = MAX_BACKFILL
    blocked = 0
    for item in data.get("tv_shows", []):
        if budget <= 0:
            break
        if item.get("date") or item.get("date_checked"):
            continue
        m = re.search(r'(?:youtu\.be/|[?&]v=)([\w-]{11})', item.get("url", ""))
        if not m:
            continue
        budget -= 1
        meta = yt_video_meta(m.group(1))
        if meta is None:
            blocked += 1
            if blocked >= 3:
                break
            continue
        if meta and meta["date"]:
            item["date"] = meta["date"]
            item.setdefault("channel", meta["channel"])
            filled += 1
        elif meta:
            item["date_checked"] = today_str()

    for section in ("news_media", "health_media"):
        for item in data.get(section, []):
            if budget <= 0:
                break
            url = item.get("url", "")
            if item.get("date") or item.get("date_checked") or "news.google.com" in url:
                continue
            budget -= 1
            try:
                html = requests.get(url, headers=dict(BROWSER_HEADERS, **{"Accept-Language": "zh-TW"}),
                                    timeout=15).text
                m = (re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', html)
                     or re.search(r'published_time"\s+content="(\d{4}-\d{2}-\d{2})', html))
                if m:
                    item["date"] = m.group(1)
                    filled += 1
                else:
                    item["date_checked"] = today_str()
            except Exception:
                pass
    print(f"[DATES] Filled {filled} missing dates")
    return filled > 0


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

    # 6. Own-channel numbers (no login needed)
    if update_youtube_stats(data):
        changes = True
    if update_podcast_count(data):
        changes = True

    # 7. Fill in missing dates, a few per run
    if backfill_dates(data):
        changes = True

    # 8. Recalculate stats
    recalculate_stats(data)

    # 9. Save
    save_data(data)
    if changes:
        print(f"\n[DONE] Data updated with new content.")
    else:
        print(f"\n[DONE] No new content found, timestamp updated.")

    return changes


if __name__ == "__main__":
    has_changes = main()
    sys.exit(0)
