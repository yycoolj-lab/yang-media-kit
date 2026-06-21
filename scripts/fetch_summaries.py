#!/usr/bin/env python3
"""
Fetch a 1-2 sentence summary (the publisher's own og:description meta tag) for
each news_media / health_media item and store it as item["summary"].

og:description is the snippet news sites write for social sharing — a ready-made
hook that tells readers what the article is about. Falls back to meta description,
then the first meaningful paragraph. Idempotent: skips items that already have a
non-empty summary unless --refresh is passed.
"""

import json
import re
import sys
import html as htmllib
from pathlib import Path

import requests

DATA_FILE = Path(__file__).parent.parent / "data.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}
MAX_LEN = 75  # keep it to a short hook


def clean(text):
    text = htmllib.unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    # Drop boilerplate site suffixes
    text = re.sub(r"\s*[-｜|–—]\s*(自由時報|自由健康網|ETtoday.*|聯合報.*|元氣網|udn\.com).*$", "", text)
    return text.strip()


def extract_meta(html, prop):
    # property="og:description" or name="description" — attr order varies
    patterns = [
        r'<meta[^>]+(?:property|name)=["\']' + re.escape(prop) + r'["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']' + re.escape(prop) + r'["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            return clean(m.group(1))
    return ""


def summarize(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return ""
        html = resp.text
        for prop in ("og:description", "description", "twitter:description"):
            desc = extract_meta(html, prop)
            # Reject aggregator boilerplate (Google News redirect pages, etc.)
            if "Comprehensive up-to-date news coverage" in desc or "aggregated from sources" in desc:
                continue
            if desc and len(desc) >= 12:
                if len(desc) > MAX_LEN:
                    desc = desc[:MAX_LEN].rstrip("，,。、 ") + "…"
                return desc
    except Exception as e:
        print(f"    ! {e}")
    return ""


def main():
    refresh = "--refresh" in sys.argv
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    filled = skipped = failed = 0

    for section in ("news_media", "health_media"):
        for item in data.get(section, []):
            url = item.get("url", "")
            if not url or not url.startswith("http"):
                continue
            if item.get("summary") and not refresh:
                skipped += 1
                continue
            summary = summarize(url)
            if summary:
                item["summary"] = summary
                filled += 1
                print(f"  [{section}] {item['title'][:30]} → {summary[:50]}")
            else:
                failed += 1
                print(f"  [{section}] {item['title'][:30]} → (no summary)")

    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone. filled={filled}, skipped(existing)={skipped}, no_summary={failed}")


if __name__ == "__main__":
    main()
