# Franchise Radar

Daily prospect brief for a REACH Franchise Development Consultant. It finds NZ franchise brands worth calling today, ranks them, maps Auckland walk-ins, and hands a clean brief to the ChatGPT/Claude lead agent, which writes the call cards.

**Every weekday at 6am NZ (GitHub Actions):**

| Step | Script | What it does |
|---|---|---|
| 1 | `universe.py` | Keeps a list of every brand in the Westpac Directory of Franchising (~335). Flags brands that are new to the directory or recently updated their listing (= actively recruiting). |
| 2 | `radar.py` | Scans franchise news, directories and Google News for openings, expansions and new franchisees (`sources.yaml`). Writes `out/latest.md` and `out/triggers.csv`. |
| 3 | `pipeline.py` | Scores every brand (news trigger, recruiting activity, local-marketing fit, Auckland multi-site), drops exclusions, existing customers and anything shown in the last 45 days, and picks a balanced top 10. Writes `out/prospects.md` (the brief) and `out/prospects.csv` (HubSpot-ready). |
| opt | `enrich.py` | With keys set: Google Places finds each brand's Auckland outlets (address, phone, reviews) and builds a walk-in route for the day's area. HubSpot flags brands already in the CRM. |

The walk-in area rotates: Mon North Shore, Tue West, Wed South, Thu East, Fri Central.

The lead agent reads: `https://raw.githubusercontent.com/mouarg/mouarg/HEAD/franchise-radar/out/prospects.md`

## Setup

1. Actions tab, "Franchise Radar", Run workflow, to test.
2. Optional repo secrets (Settings, Secrets and variables, Actions):
   - `GOOGLE_MAPS_API_KEY`: Google Cloud key with **Places API (New)** enabled. Results are cached for 30 days, so about 20 lookups a day stays inside Google's free monthly allowance (set a budget alert anyway).
   - `HUBSPOT_TOKEN`: HubSpot private app token, read-only `crm.objects.companies.read`. **Needs REACH approval.**
   - `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `DIGEST_TO`: email the news digest (Gmail: `smtp.gmail.com` and an app password).
3. Add store locator pages for target brands to `sources.yaml` (`mode: text`) to catch new stores and owners.

## Tuning

- Exclusions: `EXCLUDE_BRANDS` in `radar.py`.
- Product mapping: `REACH_FIT` in `pipeline.py`.
- Cooldown, list size, per-category cap: constants at the top of `pipeline.py`.
- Noisy news: `NOISE_WORDS` in `radar.py`, or `min_level: Warm` on a source.

## Privacy

The repo is public, so the brief is too. It contains only public directory and news data, with no REACH-internal data. Keep HubSpot results out of a public repo: if you add `HUBSPOT_TOKEN`, make the repo private and deliver the brief by email instead.
