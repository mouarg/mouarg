"""Franchise Radar: daily scan of NZ franchise sources for new prospecting triggers.

Checks every source in sources.yaml, compares with what it saw last run, and writes
a digest of anything new (out/digest-YYYY-MM-DD.md) plus a running CSV (out/triggers.csv).
Optionally emails the digest when SMTP_* environment variables are set.

Usage:
    python radar.py              # normal run
    python radar.py --baseline   # record current state only, no digest (first-time setup)
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

HERE = Path(__file__).parent
STATE_FILE = HERE / "state" / "seen.json"
OUT_DIR = HERE / "out"
UA = "Mozilla/5.0 (FranchiseRadar; personal prospecting research)"

# Words that suggest a business is growing or changing hands (= a reason to call).
HOT_WORDS = [
    "now open", "grand opening", "opening", "opens", "coming soon", "new store",
    "new location", "new franchisee", "new owner", "takes over", "expands", "expansion",
    "launches", "launch", "territor", "rebrand", "relocat", "first nz", "arrives in nz",
]
WARM_WORDS = ["franchise", "recruit", "growth", "award", "finalist", "acquire", "merger"]
LEVELS = {"Hot": 0, "Warm": 1, "Info": 2}
# Headlines that match growth words but aren't business news.
NOISE_WORDS = [
    "box office", "movie", "film", "rugby", "nrl", "cricket", "netball", "season",
    "regatta", "pokemon", "pokémon", "headset", "ratings", "game", "election", "court",
]
GENERIC_LINK_TEXT = {"read more", "view listing", "learn more", "more info", "view", "details"}

EXCLUDE_BRANDS = [
    "beds r us", "bedpost", "kitchen studio", "sushi sushi", "bakers delight", "poolwerx",
    "hire a hubby", "rodney wayne", "pizza club", "coffee news", "numberworks", "hell pizza",
    "liquorland",  # existing REACH customer (case study)
]


def fetch(url, render=False):
    if render:
        from playwright.sync_api import sync_playwright  # optional dependency

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=UA)
            page.goto(url, wait_until="networkidle", timeout=60000)
            html = page.content()
            browser.close()
            return html
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.text


def items_rss(xml, _src):
    soup = BeautifulSoup(xml, "xml")
    for it in soup.find_all(["item", "entry"]):
        title = it.title.get_text(strip=True) if it.title else ""
        link_tag = it.find("link")
        link = (link_tag.get("href") or link_tag.get_text(strip=True)) if link_tag else ""
        date_tag = it.find(["pubDate", "published", "updated"])
        date = date_tag.get_text(strip=True) if date_tag else ""
        yield {"title": title, "link": link, "date": date}


def items_links(html, src):
    soup = BeautifulSoup(html, "html.parser")
    inc = re.compile(src.get("include", "."))
    exc = re.compile(src["exclude"]) if src.get("exclude") else None
    seen_links = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(src["url"], a["href"]).split("?")[0]
        if href in seen_links or not inc.search(href) or (exc and exc.search(href)):
            continue
        seen_links.add(href)
        text = a.get_text(" ", strip=True)
        if text.lower() in GENERIC_LINK_TEXT or len(text) < 4:
            # Fall back to the brand name in the URL, e.g. /1642-katsubi -> Katsubi
            slug = href.rstrip("/").split("/")[-1]
            text = re.sub(r"^\d+-", "", slug).replace("-", " ").strip().title()
        yield {"title": text, "link": href, "date": ""}


def items_text(html, src):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    for line in soup.get_text("\n").splitlines():
        line = " ".join(line.split())
        if 4 <= len(line) <= 200:
            yield {"title": line, "link": src["url"], "date": ""}


PARSERS = {"rss": items_rss, "links": items_links, "text": items_text}


def key(item, mode):
    basis = item["link"] if mode == "links" else f"{item['title']}|{item['link']}"
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def score(title, src):
    t = title.lower()
    if any(b in t for b in EXCLUDE_BRANDS) or any(w in t for w in NOISE_WORDS):
        return None
    if src.get("require") and not re.search(src["require"], t):
        return None
    if src.get("level"):
        return src["level"]
    if any(w in t for w in HOT_WORDS):
        return "Hot"
    if any(w in t for w in WARM_WORDS):
        return "Warm"
    return "Info"


def send_email(subject, body):
    host, user, pwd, to = (os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "DIGEST_TO"))
    if not all([host, user, pwd, to]):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    with smtplib.SMTP_SSL(host, int(os.getenv("SMTP_PORT", "465"))) as s:
        s.login(user, pwd)
        s.send_message(msg)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="record current state, no digest")
    args = ap.parse_args()

    sources = yaml.safe_load((HERE / "sources.yaml").read_text())["sources"]
    seen = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    today = dt.date.today().isoformat()
    new, errors, reported = [], [], set()

    for src in sources:
        name = src["name"]
        first_time = name not in seen
        known = set(seen.get(name, []))
        try:
            raw = fetch(src["url"], src.get("render", False))
            items = list(PARSERS[src["mode"]](raw, src))
        except Exception as e:  # keep going if one site is down
            errors.append(f"{name}: {e}")
            continue
        current = {key(i, src["mode"]): i for i in items}
        # A new page/directory: just learn what's there today, report changes from tomorrow.
        if not (args.baseline or (first_time and src["mode"] in ("text", "links"))):
            for k, item in current.items():
                story = item["title"].lower().rsplit(" - ", 1)[0]  # same story, several feeds
                if k in known or story in reported:
                    continue
                level = score(item["title"], src)
                if level and LEVELS[level] <= LEVELS[src.get("min_level", "Info")]:
                    reported.add(story)
                    new.append({"found": today, "level": level, "source": name, **item})
        seen[name] = sorted(known | set(current))[-2000:]

    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen, indent=1))
    if args.baseline:
        print(f"Baseline recorded for {len(sources)} sources. Errors: {errors or 'none'}")
        return

    new.sort(key=lambda x: (LEVELS[x["level"]], x["source"]))

    OUT_DIR.mkdir(exist_ok=True)
    csv_path = OUT_DIR / "triggers.csv"
    fields = ["found", "level", "source", "title", "link", "date"]
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            w.writeheader()
        w.writerows(new)

    lines = [f"# Franchise Radar - {today}", "", f"{len(new)} new signals."]
    for level in ("Hot", "Warm", "Info"):
        group = [n for n in new if n["level"] == level]
        if group:
            lines += ["", f"## {level} ({len(group)})"]
            lines += [f"- {n['title']} ({n['source']}{', ' + n['date'] if n['date'] else ''}) {n['link']}" for n in group]
    if errors:
        lines += ["", "## Sources that failed", *[f"- {e}" for e in errors]]
    digest = "\n".join(lines) + "\n"
    (OUT_DIR / f"digest-{today}.md").write_text(digest)
    (OUT_DIR / "latest.md").write_text(digest)
    print(digest)

    if new and send_email(f"Franchise Radar {today}: {len(new)} new signals", digest):
        print("Digest emailed.")


if __name__ == "__main__":
    sys.exit(main())
