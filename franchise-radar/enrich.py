"""Optional enrichment. Each part switches on only when its key is set.

GOOGLE_MAPS_API_KEY  Google Places API (New): Auckland outlets for a brand, with address,
                     phone, rating and review count. Used for walk-in clusters and the
                     "fewest reviews in the network" talking point.
HUBSPOT_TOKEN        HubSpot private app token (read-only companies scope): flags brands
                     already in REACH's CRM so Aditi doesn't cold-call an existing account.
"""

import json
import math
import os
import re
import time
from pathlib import Path

import requests

HERE = Path(__file__).parent
PLACES_CACHE = HERE / "state" / "places_cache.json"
CACHE_DAYS = 30
# Hard spending guard: Google calls allowed per day and per month (well inside the free tier).
MAX_CALLS_PER_DAY = 20
MAX_CALLS_PER_MONTH = 500
USAGE_FILE = HERE / "state" / "places_usage.json"

AUCKLAND_BOX = {"low": {"latitude": -37.30, "longitude": 174.40},
                "high": {"latitude": -36.55, "longitude": 175.20}}
PLACES_FIELDS = ",".join(f"places.{f}" for f in [
    "displayName", "formattedAddress", "location", "businessStatus", "googleMapsUri",
    "nationalPhoneNumber", "websiteUri", "rating", "userRatingCount",
])


def area_of(lat, lng):
    """Rough Auckland area from coordinates, used to rotate the road trip each weekday."""
    if lat > -36.83 and lng > 174.66:
        return "North Shore"
    if lng < 174.68:
        return "West"
    if lat < -36.95:
        return "South"
    if lng > 174.84:
        return "East"
    return "Central"


def km(a, b):
    """Straight-line distance in km between two outlets."""
    r = 6371
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _norm(text):
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("&", " and "))
    return " ".join(w for w in text.split() if w not in ("the", "nz", "ltd", "limited"))


def is_brand(brand, place_name):
    # "Blinds On Location" must not match any blinds shop; "The Coffee Club" must not match "The ..."
    return _norm(brand.split(" - ")[0]) in _norm(place_name)


def auckland_outlets(brand):
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    cache = json.loads(PLACES_CACHE.read_text()) if PLACES_CACHE.exists() else {}
    hit = cache.get(brand)
    if hit and time.time() - hit["ts"] < CACHE_DAYS * 86400:
        return hit["outlets"]

    today = time.strftime("%Y-%m-%d")
    usage = json.loads(USAGE_FILE.read_text()) if USAGE_FILE.exists() else {}
    month_total = sum(n for d, n in usage.items() if d[:7] == today[:7])
    if usage.get(today, 0) >= MAX_CALLS_PER_DAY or month_total >= MAX_CALLS_PER_MONTH:
        return None  # cap reached: skip enrichment rather than spend
    usage[today] = usage.get(today, 0) + 1
    USAGE_FILE.parent.mkdir(exist_ok=True)
    USAGE_FILE.write_text(json.dumps(usage, indent=1))

    r = requests.post(
        "https://places.googleapis.com/v1/places:searchText",
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": PLACES_FIELDS},
        json={"textQuery": f"{brand} Auckland", "regionCode": "NZ", "pageSize": 20,
              "locationRestriction": {"rectangle": AUCKLAND_BOX}},
        timeout=30,
    )
    r.raise_for_status()
    # Keep only results that are actually this brand: the place name must contain the brand name.
    outlets = []
    for p in r.json().get("places", []):
        name = p.get("displayName", {}).get("text", "")
        if not is_brand(brand, name) or p.get("businessStatus") not in (None, "OPERATIONAL"):
            continue
        loc = p.get("location", {})
        outlets.append({
            "name": name, "address": p.get("formattedAddress", ""),
            "phone": p.get("nationalPhoneNumber", ""), "website": p.get("websiteUri", ""),
            "rating": p.get("rating"), "reviews": p.get("userRatingCount", 0),
            "maps": p.get("googleMapsUri", ""), "lat": loc.get("latitude"), "lng": loc.get("longitude"),
            "area": area_of(loc.get("latitude", 0), loc.get("longitude", 0)),
        })
    cache[brand] = {"ts": time.time(), "outlets": outlets}
    PLACES_CACHE.parent.mkdir(exist_ok=True)
    PLACES_CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
    return outlets


def hubspot_status(brand):
    """Returns None if HubSpot isn't connected, '' if not found, or a short status string."""
    token = os.getenv("HUBSPOT_TOKEN")
    if not token:
        return None
    r = requests.post(
        "https://api.hubapi.com/crm/v3/objects/companies/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"filterGroups": [{"filters": [{"propertyName": "name", "operator": "CONTAINS_TOKEN", "value": brand}]}],
              "properties": ["name", "lifecyclestage", "notes_last_contacted", "hubspot_owner_id"], "limit": 3},
        timeout=30,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        return ""
    p = results[0]["properties"]
    stage = p.get("lifecyclestage") or "unknown stage"
    last = (p.get("notes_last_contacted") or "never")[:10]
    return f"{stage}, last contacted {last}"
