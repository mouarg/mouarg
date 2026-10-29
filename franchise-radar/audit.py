"""Digital and local-presence audit: turns public data into sales talking points.

For each shortlisted brand:
  site_signals()  reads the brand website for Google tag, Google Ads, Meta Pixel and
                  online booking/ordering (are they set up to capture and retarget demand?)
  pagespeed()     Google PageSpeed Insights mobile performance score (free API)
  presence()      Google Business Profile gaps across their Auckland outlets
                  (missing phone/website/hours, weak ratings, thin reviews)
  drive_route()   real drive-time order for the day's walk-ins (Google Routes API)

Everything is cached and capped, and anything that fails is simply left out of the brief.
Keys: PAGESPEED_API_KEY (optional, falls back to GOOGLE_MAPS_API_KEY, then keyless),
GOOGLE_MAPS_API_KEY for Routes (the key must allow "Routes API").
"""

import itertools
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import requests

HERE = Path(__file__).parent
CACHE_FILE = HERE / "state" / "audit_cache.json"
USAGE_FILE = HERE / "state" / "audit_usage.json"
CACHE_DAYS = 30
UA = "Mozilla/5.0 (FranchiseRadar; personal prospecting research)"
DAILY_CAPS = {"pagespeed": 15, "routes_elements": 64}

TAGS = {
    "google_tag": re.compile(r"googletagmanager\.com|gtag\(|GTM-[A-Z0-9]+", re.I),
    "google_ads": re.compile(r"AW-\d{6,}|googleadservices\.com|google_conversion", re.I),
    "meta_pixel": re.compile(r"connect\.facebook\.net/[^\"']*fbevents|fbq\(", re.I),
    "tiktok_pixel": re.compile(r"analytics\.tiktok\.com", re.I),
    "online_booking": re.compile(r"book (now|online|an? appointment)|order online|online ordering|get a quote|request a quote", re.I),
}


def _load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def _save(path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))


def _cached(kind, key, fn):
    cache = _load(CACHE_FILE)
    hit = cache.get(kind, {}).get(key)
    if hit and time.time() - hit["ts"] < CACHE_DAYS * 86400:
        return hit["value"]
    value = fn()
    if value is not None:
        cache.setdefault(kind, {})[key] = {"ts": time.time(), "value": value}
        _save(CACHE_FILE, cache)
    return value


def _spend(kind, amount=1):
    """True if today's cap for this API allows `amount` more units, and records them."""
    today = time.strftime("%Y-%m-%d")
    usage = _load(USAGE_FILE)
    used = usage.get(today, {}).get(kind, 0)
    if used + amount > DAILY_CAPS[kind]:
        return False
    usage.setdefault(today, {})[kind] = used + amount
    _save(USAGE_FILE, {d: v for d, v in usage.items() if d >= time.strftime("%Y-%m-01")})
    return True


def brand_website(outlets):
    """Most common website domain across a brand's outlets, as a homepage URL."""
    hosts = Counter(urlparse(o["website"]).netloc.lower().removeprefix("www.")
                    for o in outlets if o.get("website"))
    if not hosts:
        return None
    host = hosts.most_common(1)[0][0]
    return f"https://{host}/" if host else None


def site_signals(url):
    def fetch():
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
        except Exception:
            return None
        return {name: bool(rx.search(r.text)) for name, rx in TAGS.items()}
    return _cached("site", url, fetch)


def pagespeed(url):
    def fetch():
        if not _spend("pagespeed"):
            return None
        key = os.getenv("PAGESPEED_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY")
        base = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
        params = {"url": url, "strategy": "mobile", "category": "performance"}
        for attempt in ([key] if key else []) + [None]:  # key first, then keyless
            try:
                r = requests.get(base, params=params | ({"key": attempt} if attempt else {}), timeout=90)
                if r.status_code in (400, 403) and attempt:
                    continue  # key not enabled for PageSpeed: try keyless
                r.raise_for_status()
                score = r.json()["lighthouseResult"]["categories"]["performance"]["score"]
                return round(score * 100) if score is not None else None
            except Exception:
                continue
        return None
    return _cached("pagespeed", url, fetch)


def presence(outlets):
    """Google Business Profile gaps across a brand's outlets."""
    n = len(outlets)
    if not n:
        return None
    rated = [o for o in outlets if o.get("rating")]
    reviews = sorted(o.get("reviews") or 0 for o in outlets)
    return {
        "outlets": n,
        "no_phone": sum(1 for o in outlets if not o.get("phone")),
        "no_website": sum(1 for o in outlets if not o.get("website")),
        "no_hours": sum(1 for o in outlets if o.get("has_hours") is False),
        "low_rating": sum(1 for o in rated if o["rating"] < 4.0),
        "thin_reviews": sum(1 for r in reviews if r < 20),
        "avg_rating": round(sum(o["rating"] for o in rated) / len(rated), 1) if rated else None,
        "median_reviews": reviews[n // 2],
        "min_reviews": reviews[0],
        "max_reviews": reviews[-1],
    }


def talking_points(signals, speed, gaps):
    """Plain-English findings, each tied to the REACH service that fixes it."""
    pts = []
    if gaps:
        n = gaps["outlets"]
        if n >= 3 and gaps["max_reviews"] >= 4 * max(gaps["min_reviews"], 1):
            pts.append(f"Uneven stores: Google reviews range from {gaps['min_reviews']} to {gaps['max_reviews']} "
                       f"across {n} Auckland sites. Some catchments are under-performing "
                       "-> Data Insights to rank catchments, then letterbox where it's weakest.")
        if gaps["low_rating"]:
            pts.append(f"{gaps['low_rating']} of {n} Auckland sites rate under 4.0 stars "
                       "-> new-customer campaigns plus a review/feedback QR on every flyer.")
        if gaps["thin_reviews"] and n >= 2:
            pts.append(f"{gaps['thin_reviews']} of {n} sites have under 20 Google reviews (low local visibility) "
                       "-> Local Booster / Community Reach to build local awareness.")
        missing = [f"{gaps[k]} without {label}" for k, label in
                   (("no_phone", "a phone number"), ("no_website", "a website link"), ("no_hours", "opening hours"))
                   if gaps[k]]
        if missing:
            pts.append("Google listings incomplete: " + ", ".join(missing)
                       + " -> quick win to raise in Consulting/strategy chat (fix before spending on ads).")
    if signals:
        if not signals["google_ads"]:
            pts.append("No Google Ads tracking found on their website -> likely not running Google Search ads: "
                       "Digital Marketing (Google Search) to catch people already searching locally.")
        if not signals["meta_pixel"]:
            pts.append("No Meta Pixel on their website -> they can't retarget site visitors on Facebook/Instagram: "
                       "Digital Marketing (Meta) + pixel set-up, so letterbox responders get followed up.")
        if not signals["google_tag"]:
            pts.append("No Google Analytics/Tag Manager found -> they can't measure campaign results: "
                       "pitch REACH's tracking (QR codes, dashboards) as the proof layer.")
        if signals["online_booking"]:
            pts.append("Website takes bookings/orders/quotes online -> campaigns can drive straight to a measurable action "
                       "(Lead Accelerator / Traffic Igniter style digital).")
    if speed is not None and speed < 50:
        pts.append(f"Website scores {speed}/100 for mobile speed (Google PageSpeed) -> slow landing page will waste ad spend; "
                   "raise as a Consulting point before running digital.")
    return pts


def drive_route(stops):
    """Best visiting order by real drive time (Google Routes API). None if unavailable."""
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    n = len(stops)
    if not key or n < 2 or n > 7 or not _spend("routes_elements", n * n):
        return None
    wp = [{"waypoint": {"location": {"latLng": {"latitude": s["lat"], "longitude": s["lng"]}}}} for s in stops]
    try:
        r = requests.post(
            "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix",
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "originIndex,destinationIndex,duration,condition"},
            json={"origins": wp, "destinations": wp, "travelMode": "DRIVE"},
            timeout=30,
        )
        r.raise_for_status()
        mins = {}
        for el in r.json():
            if el.get("condition") == "ROUTE_EXISTS" and "duration" in el:
                mins[(el["originIndex"], el["destinationIndex"])] = int(el["duration"].rstrip("s")) / 60
    except Exception:
        return None
    best = None
    for order in itertools.permutations(range(n)):
        legs = [mins.get((a, b)) for a, b in zip(order, order[1:])]
        if None in legs:
            continue
        total = sum(legs)
        if best is None or total < best[0]:
            best = (total, order, legs)
    if not best:
        return None
    _, order, legs = best
    return [{**stops[i], "drive_min": round(legs[k - 1]) if k else None} for k, i in enumerate(order)]
