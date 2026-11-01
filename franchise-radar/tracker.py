"""Aditi's call tracker (a Google Sheet published as CSV) feeds results back into the radar.

Sheet columns (header names are matched loosely, Y/Yes/TRUE/x count as yes):
  Date | Brand | HQ or Local | Called | Meeting booked | Not interested | Stage | Value $/month | Notes
  (Stage and Value are optional; the weekly report uses them for pipeline totals.)

What the radar does with it:
  - brands marked "Not interested" are not suggested again for 180 days
  - brands with a meeting booked are left to her (no repeat cold-call suggestion)
  - business types that book meetings get a score boost, ones that never do get a small penalty
  - the brief opens with her last-7-days tally against the 30 calls / 10 meetings target

Set TRACKER_CSV_URL to the sheet's "Publish to web" CSV link. Without it, nothing changes.
"""

import csv
import datetime as dt
import io
import os
import re

import requests

YES = re.compile(r"^\s*(y|yes|true|x|1|booked|done)\b", re.I)


def _col(row, *names):
    for k, v in row.items():
        if k and any(n in k.lower() for n in names):
            return (v or "").strip()
    return ""


def _date(text):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def load():
    url = os.getenv("TRACKER_CSV_URL")
    if not url:
        return None
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
    except Exception:
        return None
    rows = []
    for row in csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))):
        brand = _col(row, "brand", "company")
        if not brand:
            continue
        rows.append({
            "date": _date(_col(row, "date")),
            "brand": brand,
            "called": bool(YES.match(_col(row, "called"))),
            "meeting": bool(YES.match(_col(row, "meeting"))),
            "not_interested": bool(YES.match(_col(row, "not interested", "no interest", "dead"))),
        })
    return rows


def summary(rows, today):
    week = [r for r in rows if r["date"] and 0 <= (today - r["date"]).days < 7]
    return sum(r["called"] for r in week), sum(r["meeting"] for r in week)


def blocked_brands(rows, today):
    """Brands not to suggest: not interested in the last 180 days, or a meeting already booked."""
    out = set()
    for r in rows:
        recent = r["date"] is None or (today - r["date"]).days < 180
        if (r["not_interested"] and recent) or r["meeting"]:
            out.add(r["brand"].lower())
    return out


def category_adjustments(rows, brands):
    """Score nudge per directory category, learned from which calls turned into meetings."""
    cat_of = {b["name"].lower(): b["category"] for b in brands.values()}
    calls, meets = {}, {}
    for r in rows:
        cat = cat_of.get(r["brand"].lower())
        if not cat or not r["called"]:
            continue
        calls[cat] = calls.get(cat, 0) + 1
        meets[cat] = meets.get(cat, 0) + r["meeting"]
    total_calls = sum(calls.values())
    if total_calls < 10:
        return {}  # not enough data to learn from yet
    overall = sum(meets.values()) / total_calls
    adj = {}
    for cat, n in calls.items():
        rate = meets[cat] / n
        if n >= 5 and overall and rate >= 1.5 * overall:
            adj[cat] = (10, f"{meets[cat]} meetings from {n} calls in this category")
        elif n >= 8 and meets[cat] == 0:
            adj[cat] = (-10, f"no meetings from {n} calls in this category yet")
    return adj
