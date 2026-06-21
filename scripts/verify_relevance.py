#!/usr/bin/env python3
"""
Verify every auto_search item in data.json actually mentions 楊智鈞.
- YouTube videos: yt-dlp fetches title + full description + tags
- News articles: fetch page HTML and search for the name
Items that fail verification are removed (saved to removed_items.json for review).
Manual items are never touched.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import requests

DATA_FILE = Path(__file__).parent.parent / "data.json"
REMOVED_FILE = Path(__file__).parent.parent / "removed_items.json"

NAMES = ["楊智鈞", "俠醫", "富足診所"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}


def check_text(text):
    return any(n in text for n in NAMES)


def verify_youtube(url):
    """Return True if video title/description/tags mention the doctor. None = couldn't verify."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None  # video removed/private — can't verify
        info = json.loads(result.stdout)
        text = " ".join([
            info.get("title", "") or "",
            info.get("description", "") or "",
            " ".join(info.get("tags", []) or []),
            info.get("channel", "") or "",
        ])
        return check_text(text)
    except Exception:
        return None


def verify_article(url):
    """Return True if article page mentions the doctor. None = couldn't fetch."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        return check_text(resp.text)
    except Exception:
        return None


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    removed = []

    for section in ["tv_shows", "news_media", "health_media"]:
        items = data.get(section, [])
        kept = []
        for item in items:
            if item.get("source") != "auto_search":
                kept.append(item)  # manual entries always kept
                continue

            title = item.get("title", "")
            url = item.get("url", "")

            # Fast path: name in title
            if check_text(title):
                kept.append(item)
                print(f"  KEEP (title) [{section}] {title[:50]}")
                continue

            if "youtu" in url:
                ok = verify_youtube(url)
            else:
                ok = verify_article(url)

            if ok is True:
                kept.append(item)
                print(f"  KEEP (content) [{section}] {title[:50]}")
            elif ok is None:
                # Can't verify — remove to be safe (user wants strict cleanup)
                removed.append({**item, "_section": section, "_reason": "unverifiable"})
                print(f"  DROP (unverifiable) [{section}] {title[:50]}")
            else:
                removed.append({**item, "_section": section, "_reason": "not_relevant"})
                print(f"  DROP (not relevant) [{section}] {title[:50]}")

        data[section] = kept

    # Recalculate stats
    tv = len(data["tv_shows"])
    total = tv + len(data["news_media"]) + len(data["health_media"])
    data["stats"]["tv_episodes"]["count"] = tv
    data["stats"]["tv_episodes"]["display"] = f"{tv}+"
    data["stats"]["media_exposure"]["count"] = total
    data["stats"]["media_exposure"]["display"] = f"{total}+"

    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    REMOVED_FILE.write_text(json.dumps(removed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone. Removed {len(removed)} items. TV: {tv}, Total exposure: {total}")


if __name__ == "__main__":
    main()
