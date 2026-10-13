"""Brand universe: every franchise in the Westpac Directory of Franchising (franchise.co.nz).

Reads the site's sitemap for listing URLs and their last-updated dates, then fetches only
new or changed listings to get the brand's category and blurb. Saves state/brands.json.
A listing updated recently usually means the brand is actively recruiting or growing.
"""

import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).parent
BRANDS_FILE = HERE / "state" / "brands.json"
SITEMAP = "https://franchise.co.nz/sitemap.xml"
UA = "Mozilla/5.0 (FranchiseRadar; personal prospecting research)"

# Directory categories that are suppliers to franchises, not franchises Aditi can sell LSM to.
SUPPLIER_CATEGORIES = re.compile(r"bank|legal|lawyer|accountan|consult|adviser|advisor|service providers|financ", re.I)


def listing_urls():
    xml = requests.get(SITEMAP, headers={"User-Agent": UA}, timeout=60).text
    pairs = re.findall(r"<loc>([^<]*/franchise-directory/\d+[^<]*)</loc><lastmod>([^<]+)</lastmod>", xml)
    return {url.split("?")[0]: lastmod[:10] for url, lastmod in pairs}


def parse_listing(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "form"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    try:
        i = lines.index("Back to Westpac Directory of Franchising")
    except ValueError:
        return {}
    name, category = lines[i + 1], lines[i + 2]
    blurb = " ".join(l for l in lines[i + 3:i + 8] if not l.startswith(("Buy an NZ franchise", "Find Out More")))
    return {"name": name, "category": category, "blurb": blurb[:400]}


def refresh(max_fetch=400, delay=1.0):
    first_build = not BRANDS_FILE.exists()
    brands = {} if first_build else json.loads(BRANDS_FILE.read_text())
    fetched = 0
    for url, lastmod in sorted(listing_urls().items(), key=lambda kv: kv[1], reverse=True):
        known = brands.get(url)
        if known and known.get("lastmod") == lastmod:
            continue
        if fetched >= max_fetch:
            break
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            info = parse_listing(r.text)
        except Exception as e:
            print(f"skip {url}: {e}")
            continue
        fetched += 1
        if info:
            # "baseline" = was already listed when we started watching, so not news.
            first_seen = known["first_seen"] if known else ("baseline" if first_build else time.strftime("%Y-%m-%d"))
            brands[url] = {**info, "url": url, "lastmod": lastmod, "first_seen": first_seen,
                           "supplier": bool(SUPPLIER_CATEGORIES.search(info["category"]))}
        time.sleep(delay)  # be polite to the directory
    BRANDS_FILE.parent.mkdir(exist_ok=True)
    BRANDS_FILE.write_text(json.dumps(brands, indent=1, ensure_ascii=False))
    print(f"Brand universe: {len(brands)} listings ({fetched} fetched this run)")
    return brands


if __name__ == "__main__":
    refresh()
