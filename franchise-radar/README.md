# Franchise Radar

Daily scan of NZ franchise sources for prospecting triggers (new brands recruiting, openings, expansions, new listings). Feeds the "Franchise Lead Agent" in ChatGPT or Claude, which turns the signals into call cards.

**How it fits together**

1. GitHub Actions runs `radar.py` at 6am NZ time on weekdays.
2. The radar checks each source in `sources.yaml` and compares it with the last run (`state/seen.json`).
3. Anything new goes to `out/latest.md` (today's digest) and `out/triggers.csv` (running history). It is also emailed if the email secrets are set.
4. The lead agent reads `out/latest.md` first, then researches contacts and writes the call cards.

## Setup

1. Turn on Actions for the repo. Run "Franchise Radar" once by hand (Actions tab, Run workflow) to record a baseline.
2. Optional email digest: add repo secrets `SMTP_HOST` (`smtp.gmail.com`), `SMTP_USER` (Gmail address), `SMTP_PASS` (a Gmail app password) and `DIGEST_TO`.
3. Add target brands' store locator pages to `sources.yaml` (`mode: text`). A new line on a locator usually means a new store or owner, which is a strong local-marketing trigger.

## Run locally

```
pip install -r requirements.txt
python radar.py --baseline   # first time only
python radar.py
```

## Tuning

- Too noisy: add words to `NOISE_WORDS` in `radar.py`, or set `min_level: Warm` on a source.
- Missing signals: add a source. Any RSS feed, listing page or store locator works.
- Exclusions: `EXCLUDE_BRANDS` in `radar.py`.
