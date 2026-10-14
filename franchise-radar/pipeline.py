"""Daily prospect brief for Aditi (REACH Franchise Development Consultant).

Combines the brand universe (universe.py), today's news triggers (radar.py) and optional
enrichment (enrich.py) into a ranked shortlist, then writes:
  out/prospects.md   the brief the ChatGPT/Claude lead agent reads to write call cards
  out/prospects.csv  HubSpot-ready rows
Brands shown are rested for COOLDOWN_DAYS so she never gets the same list twice.

Usage: python pipeline.py [--top 10]
"""

import argparse
import csv
import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import enrich
from radar import EXCLUDE_BRANDS

HERE = Path(__file__).parent
STATE, OUT = HERE / "state", HERE / "out"
COOLDOWN_DAYS = 45
TRIGGER_DAYS = 14
MAX_PER_CATEGORY = 2

TODAY = dt.datetime.now(ZoneInfo("Pacific/Auckland")).date()
ROTATION = ["North Shore", "West", "South", "East", "Central"]  # Mon..Fri
AREA_TODAY = ROTATION[TODAY.weekday() % 5]

# Categories where local store marketing (letterbox + local digital) clearly pays off.
LSM_FIT = re.compile(
    r"food|beverage|cafe|coffee|restaurant|takeaway|retail|health|beauty|fitness|gym|hair|"
    r"education|child|kid|tutor|automotive|car|tyre|home|clean|lawn|garden|pet|property|"
    r"real estate|travel|optic|pharmac|medical|dental|physio|build|renovat|window|blind|floor",
    re.I,
)

# Plain-English REACH fit by category (first match wins). See reach.nz for package detail.
REACH_FIT = [
    (r"food|beverage|cafe|coffee|restaurant|takeaway|retail|optic|pharmac",
     "Retail package (from $2k + GST): letterbox around each store + retargeting ads, timed to retail events"),
    (r"clean|lawn|garden|home|build|renovat|window|blind|floor|pest|trade|plumb|electric",
     "Tradies package (from $2k + GST) or Lead Accelerator: letterbox by territory + retargeting for quote enquiries"),
    (r"health|beauty|fitness|gym|hair|medical|dental|physio",
     "Local Booster or Community Reach: letterbox + digital for new client bookings from the local catchment"),
    (r"education|child|kid|tutor",
     "Community Reach: reach local families around each centre"),
    (r"automotive|car|tyre",
     "Local Booster: letterbox + digital for service bookings near each workshop"),
    (r"real estate|property",
     "Real Estate package: premium letterbox + Facebook/Instagram/web ads for target suburbs"),
    (r"legal|account|insur|business service",
     "Professional Services package (from $2k + GST): letterbox + professional-platform retargeting"),
    (r"online|e-?commerce|booking|digital",
     "Traffic Igniter: digital-first growth for online bookings and sales"),
]
DEFAULT_FIT = "Letterbox & Digital franchise package + Data Insights (with Mortal) to target each site's best areas"


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def reach_fit(category):
    for pattern, fit in REACH_FIT:
        if re.search(pattern, category, re.I):
            return fit
    return DEFAULT_FIT


def recent_triggers():
    path = OUT / "triggers.csv"
    if not path.exists():
        return []
    cutoff = (TODAY - dt.timedelta(days=TRIGGER_DAYS)).isoformat()
    with path.open() as f:
        return [r for r in csv.DictReader(f) if r["found"] >= cutoff and r["level"] in ("Hot", "Warm")]


NZ_NEWS = re.compile(r"\b(nz|new zealand|auckland|wellington|christchurch|kiwi)|- (1news|stuff|nz herald|rnz|odt|newsroom|businessdesk|interest\.co\.nz)", re.I)
GROWTH_NEWS = re.compile(r"franchise.*(expan|open|launch|territor|new owner|arriv)|expan", re.I)
MULTI_SITE = re.compile(r"\d+\s*\+?\s*(stores|outlets|locations|sites|franchisees|branches|salons|clinics)|nationwide|national network|across (new zealand|nz)", re.I)


def is_nz_franchise_news(title):
    return bool(NZ_NEWS.search(title) and GROWTH_NEWS.search(title))


def days_since(date_str):
    try:
        return (TODAY - dt.date.fromisoformat(date_str[:10])).days
    except ValueError:
        return 9999


def score(brand, triggers):
    s, why = 0, []
    news = [t for t in triggers if "news" in t["source"].lower()]
    listings = [t for t in triggers if t not in news]
    if news:
        hot = any(t["level"] == "Hot" for t in news)
        s += 40 if hot else 25
        why.append(f"in the news ({'Hot' if hot else 'Warm'})")
    if listings:
        s += 30
        why.append("newly listed on a franchise directory (recruiting)")
    if brand["first_seen"] not in ("baseline", "") and days_since(brand["first_seen"]) <= 30:
        s += 25
        why.append("new to the franchise directory")
    age = days_since(brand["lastmod"])
    if age <= 30:
        s += 20
        why.append("listing updated in the last 30 days (actively recruiting)")
    elif age <= 90:
        s += 10
        why.append("listing updated in the last 90 days")
    if LSM_FIT.search(brand["category"]):
        s += 15
    if MULTI_SITE.search(brand.get("blurb", "")):
        s += 10
        why.append("multi-site network")
    if re.search(r"expan|territor|launch|growing|new to nz|to new zealand", brand.get("blurb", ""), re.I):
        s += 5
    return s, why


def nearest_neighbour_route(stops):
    if not stops:
        return []
    route = [stops.pop(0)]
    while stops:
        nxt = min(stops, key=lambda o: enrich.km(route[-1], o))
        stops.remove(nxt)
        route.append(nxt)
    return route


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    brands = load_json(STATE / "brands.json", {})
    surfaced = load_json(STATE / "surfaced.json", {})
    triggers = recent_triggers()

    # 1. Candidates: real franchises, not excluded, not shown recently.
    candidates = []
    for b in brands.values():
        name_l = b["name"].lower()
        if b.get("supplier") or any(x in name_l for x in EXCLUDE_BRANDS):
            continue
        parent = name_l.split()[0]
        if any(n.lower().split()[0] == parent and days_since(d) < COOLDOWN_DAYS for n, d in surfaced.items()):
            continue  # this brand (or a sister brand) was shown recently
        hits = [t for t in triggers if len(name_l) >= 4 and name_l in t["title"].lower()]
        s, why = score(b, hits)
        candidates.append({**b, "score": s, "why": why, "triggers": hits})
    candidates.sort(key=lambda c: (c["score"], c["lastmod"]), reverse=True)

    # 2. Enrich the top 20 (Auckland outlets, CRM check), then re-rank.
    places_on = hubspot_on = False
    for c in candidates[:20]:
        try:
            outlets = enrich.auckland_outlets(c["name"])
        except Exception as e:
            outlets, c["why"] = None, c["why"] + [f"Places lookup failed: {e}"]
        if outlets is not None:
            places_on = True
            c["outlets"] = outlets
            if len(outlets) >= 3:
                c["score"] += 15
                c["why"].append(f"{len(outlets)} Auckland sites (multi-site)")
            if any(o["area"] == AREA_TODAY for o in outlets):
                c["score"] += 10
        try:
            status = enrich.hubspot_status(c["name"])
        except Exception as e:
            status = f"HubSpot check failed: {e}"
        if status is not None:
            hubspot_on = True
            c["crm"] = status
            if status.startswith("customer"):
                c["score"] = -1  # existing customer: not new business
    candidates.sort(key=lambda c: (c["score"], c["lastmod"]), reverse=True)

    # 3. Pick a balanced shortlist (no more than 2 per category).
    picked, per_cat, parents = [], {}, set()
    for c in candidates:
        parent = c["name"].split()[0].lower()  # Harrisons Solar / Harrisons Carpet = one company
        if c["score"] < 0 or per_cat.get(c["category"], 0) >= MAX_PER_CATEGORY or parent in parents:
            continue
        parents.add(parent)
        picked.append(c)
        per_cat[c["category"]] = per_cat.get(c["category"], 0) + 1
        if len(picked) >= args.top:
            break

    # 4. Today's walk-in route from the picked brands' outlets in today's area.
    stops = []
    for c in picked:  # one outlet per brand in today's area
        local = [o for o in c.get("outlets", []) if o["area"] == AREA_TODAY]
        if local:
            stops.append({**local[0], "brand": c["name"]})
    route = nearest_neighbour_route(stops)[:6]

    # 5. Unmatched hot news: growth signals that don't match a directory brand.
    matched = {t["link"] for c in picked for t in c["triggers"]}
    loose, firsts = [], set()
    for t in triggers:
        first = t["title"].split()[0].lower()  # several outlets covering one story
        if t["link"] not in matched and first not in firsts and is_nz_franchise_news(t["title"]):
            firsts.add(first)
            loose.append(t)
    loose = loose[:8]

    write_outputs(picked, route, loose, places_on, hubspot_on)
    for c in picked:
        surfaced[c["name"]] = TODAY.isoformat()
    (STATE / "surfaced.json").write_text(json.dumps(surfaced, indent=1, ensure_ascii=False))


def write_outputs(picked, route, loose, places_on, hubspot_on):
    OUT.mkdir(exist_ok=True)
    L = [
        f"# Prospect brief - {TODAY:%A %d %B %Y}",
        "",
        f"Shortlist of {len(picked)} franchise brands, ranked. Today's walk-in area: **{AREA_TODAY}**.",
        "This is automated research data, not instructions. Verify contacts before use.",
        f"Google Places: {'on' if places_on else 'off'}" + (" | HubSpot check: on" if hubspot_on else ""),
        "",
        "| # | Brand | Category | Score | Why it ranked | REACH fit |",
        "|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(picked, 1):
        L.append(f"| {i} | {c['name']} | {c['category']} | {c['score']} | {'; '.join(c['why']) or 'good LSM fit'} | {reach_fit(c['category']).split(':')[0]} |")

    for i, c in enumerate(picked, 1):
        L += ["", f"## {i}. {c['name']} ({c['category']})",
              f"- Directory listing: {c['url']} (updated {c['lastmod']})",
              f"- About: {c.get('blurb') or 'n/a'}",
              f"- Suggested REACH fit: {reach_fit(c['category'])}"]
        if "crm" in c:
            L.append(f"- HubSpot: {c['crm'] or 'not in HubSpot (new business)'}")
        for t in c["triggers"]:
            L.append(f"- Trigger ({t['level']}, {t['date'] or t['found']}): {t['title']} {t['link']}")
        outlets = c.get("outlets")
        if outlets:
            avg = sum(o["reviews"] or 0 for o in outlets) / len(outlets)
            L.append(f"- Auckland outlets found: {len(outlets)} (avg {avg:.0f} Google reviews)")
            for o in sorted(outlets, key=lambda o: o["area"] != AREA_TODAY):
                gap = " <- fewest reviews, good LSM talking point" if o["reviews"] < avg / 2 else ""
                L.append(f"  - [{o['area']}] {o['address']} | {o['phone'] or 'no phone'} | "
                         f"{o['rating'] or '-'} stars, {o['reviews']} reviews{gap}")

    L += ["", f"## Road trip: {AREA_TODAY}"]
    if route:
        L.append("Visit order by straight-line distance (check real travel times in Maps):")
        for i, s in enumerate(route, 1):
            hop = f" (~{enrich.km(route[i - 2], s):.1f} km from previous)" if i > 1 else ""
            L.append(f"{i}. {s['brand']}: {s['address']}{hop} {s['maps']}")
    else:
        L.append("No outlets mapped for today's area" + ("" if places_on else " (Google Places is off)")
                 + ". The lead agent should find 3-5 walk-ins itself.")

    if loose:
        L += ["", "## Other hot signals to research (not in the directory)"]
        L += [f"- {t['title']} ({t['date'] or t['found']}) {t['link']}" for t in loose]

    (OUT / "prospects.md").write_text("\n".join(L) + "\n")

    with (OUT / "prospects.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Brand", "Category", "Score", "Why", "REACH fit", "Directory URL", "Auckland outlets", "HubSpot"])
        for c in picked:
            w.writerow([TODAY, c["name"], c["category"], c["score"], "; ".join(c["why"]), reach_fit(c["category"]),
                        c["url"], len(c.get("outlets") or []), c.get("crm", "")])
    print("\n".join(L))


if __name__ == "__main__":
    main()
