# Supply Chain Watchtower

**A demonstration build.** The supplier and peer lists are an illustrative sample and the
exposure tiers are assigned for the demo — they are not any company's own classification.
Every signal is drawn from public sources. The mechanics, scoring and analysis are real.

A zero-cost, sovereign intelligence engine using the "Flat Data" pattern. This dashboard aggregates intelligence from official government endpoints without requiring any infrastructure or database.

## Architecture

- **Harvester**: Python script runs via GitHub Actions every 6 hours
- **Database**: Git repository (JSON file)
- **View**: Next.js static dashboard
- **Cost**: $0 (GitHub Actions free tier)

## Data Sources

All free, no contracts. Keys where noted are optional — every source degrades
to a fallback rather than failing the harvest.

| Signal | Source | Key |
|---|---|---|
| Cyber threats | CISA Known Exploited Vulnerabilities (KEV) catalog | — |
| Sanctions | OFAC SDN list (`sanctionslistservice.ofac.treas.gov`) | — |
| Safety recalls | CPSC recall database, last 90 days | — |
| Macro FX | ECB euro reference rates | — |
| US & EU CPI / policy rates | FRED | `FRED_API_KEY` (shows "not connected" if unset) |
| Prices, market news | yfinance | — |
| Competitor filings | SEC EDGAR 8-K | — |
| Supplier & country news | Google News RSS | — |
| Country news tone | GDELT (experimental, `/geopolitical`) | — |

GDELT is asked for each country by name. The USA is the exception: `"USA"` is
rejected outright (GDELT will not match a quoted phrase under four characters)
and `"United States"` matches a corpus too large to return before timing out,
so the USA is read from the tone of US-published coverage (`sourcecountry:US`)
instead. That is a different measurement, and the page labels it as one.
| Executive summary | Claude (`claude-haiku-4-5`) | `ANTHROPIC_API_KEY` (skipped if unset) |
| Daily brief / alerts | Slack or Telegram | `SLACK_WEBHOOK_URL` or `TELEGRAM_BOT_TOKEN` (silent if unset) |

## What Changed Since You Last Looked

Each harvest diffs itself against the previous snapshot and appends whatever
moved to a rolling `change_log` inside the snapshot — risk-level moves, signals
appearing or clearing, competitor status shifts, macro trend reversals, and
outsized unexplained price moves on Critical/High exposure suppliers. The
dashboard leads with that feed, sliced by the reader's own last-visit time held
in `localStorage`.

Standing geographic exposure is deliberately excluded unless live news escalated
it that cycle: it is true every day, so logging it as a change would pin the
same entries at the top permanently.

## Setup

### Local Development

1. Install dependencies:
```bash
npm install && pip install -r requirements.txt
```

2. Run the harvester script manually:
```bash
python scripts/update_intel.py
```

Without `FRED_API_KEY` and `ANTHROPIC_API_KEY` in the environment the harvest
still completes, but CPI and policy rates read "not connected" and no executive
summary is generated — so a locally produced snapshot is poorer than the one
the workflow commits, and is not usually worth committing.

3. Run the tests:
```bash
pytest tests/ -q
```

4. Start the development server:
```bash
npm run dev
```

5. Open [http://localhost:3000](http://localhost:3000)

### GitHub Actions

The workflow is configured to run automatically every 6 hours. To trigger manually:

1. Go to Actions tab in GitHub
2. Select "Harvest Intelligence Data"
3. Click "Run workflow"

## Supplier Watchlist

The watchlist lives in `data/suppliers.json`, not in code. To add, remove or
re-tier a supplier, edit that file:

```json
{
  "name": "Acme Filters",
  "category": "Filter Materials",
  "bat_exposure": "High",
  "location": "Japan",
  "stock_ticker": "N/A"
}
```

- `bat_exposure` — `Critical` (tier 1), `High` (tier 2) or `Medium` (tier 3)
- `stock_ticker` — `"N/A"` for unlisted suppliers; those are scanned via
  Google News instead of yfinance. **Check the symbol resolves to the right
  company before adding it**: `GPI` on the NYSE is Group 1 Automotive, not
  Graphic Packaging (`GPK`), and `SAP` is SAP SE, not Sappi (`SAP.JO`). Both
  were live on this watchlist and fed another company's price and headlines
  into a supplier's risk score
- `location` — the country of the **site that supplies BAT**, not the legal
  headquarters: a strike, a power cut or a border closure hits the plant, not
  the registered office. Where the headquarters sits elsewhere, record it as
  `hq_country` so the fact isn't lost — Weener's plant is in Weener, Germany
  while the group HQ moved to Ede, Netherlands in 2016; Cerdia's acetate tow
  comes out of Freiburg, Germany while the company is registered in Basel; and
  CNT's nicotine is produced under contract by Siegfried AG in Switzerland,
  from a company headquartered in Heilbronn.
  Must match a country in `data/country_risk.json` to pick up a standing risk
  floor; a country absent from that file carries none, which is the correct
  state for stable countries
- `category` — should have an entry in both `category_segments` and
  `category_keywords` in the same file; a missing entry logs a warning and
  falls back rather than failing the harvest

Country risk floors live in `data/country_risk.json`:

```json
{ "Finland": { "level": "MEDIUM", "reason": "NATO frontline state, border with Russia" } }
```

A floor can only raise a supplier's risk, never lower it, and on its own it
never moves the pillar RAG score — only live news escalating above the floor
counts as something that happened. An unknown `level` or a missing `reason`
fails the harvest.

A malformed or empty file fails the harvest rather than producing an empty
watchlist, which would read as "nothing to worry about".

## Intelligence Logic

### Price moves are judged per company, not on a fixed percentage

A 3% fall is a normal Tuesday for Texas Instruments and a serious event for a
packaging name that rarely moves 1%. Every price signal — supplier, peer and
macro — is scored against that listing's own daily volatility over the last
three months (`classify_price_move`):

- **severe**: ≥3.5σ (or ≥7% where there isn't enough history to measure σ)
- **notable**: ≥2σ (or ≥4%), and never below a 2% floor
- **quiet**: anything else — not reported as risk

Thresholds relax by 25% for a supplier that was already flagged last cycle, so
a move hovering at the boundary holds its level instead of flipping every six
hours. A price move with no corroborating news never turns the board RED: it is
carried as `price_move_only` and named in the change feed as an unexplained
move, which is what it is.

### Cyber "Panic" Score
- **RED**: Ransomware campaign use + added in last 48h
- **AMBER**: Any new vulnerability in last 7 days
- **GREEN**: No changes

### Competitor "Distress" Signal
- **RED**: Item 1.03 (Bankruptcy) or Item 4.02 (Non-Reliance)
- **GREEN**: Routine filings

Item 5.02 (officer/director departure) is shown as context and never scored: a
planned retirement files the same item code as a scandal-driven exit, and the
filing does not say which.

### Macro pillar
Scored from how unusual each region's market move is (S&P 500, EUR/USD,
USD/CNY), on the same volatility yardstick — one severe move is RED, one
notable move is AMBER, otherwise GREEN. Official statistics (CPI, policy rate)
come from FRED and carry the month they were observed; a region with no live
feed that still updates shows "not connected" rather than a stale number.

### Where a supplier sits is not the same as what happened to it

Nine of the twenty-four suppliers are in countries carrying a standing risk
floor (China, Finland, India, South Korea, South Africa). Folded into one
number, that put a third of the watchlist at MEDIUM every day of the year,
wearing the same amber pill as a supplier with a CVE published against it that
morning. Two levels are therefore recorded per supplier:

- `risk_level` — the highest level from any layer, floor included
- `event_risk_level` — the level from what actually happened, floor excluded

The dashboard shows the event level, and the country floor appears beside it as
a grey 🌍 marker reading *standing exposure*. Counts, filters and the action
list all key off the event level, so a headline count and the rows its filter
reveals cannot disagree. Only the event level moves the pillar RAG score.

### Geopolitical escalations are held for 48 hours
Google News returns only the eight most recent matches for a country, and that
set turns over within hours — so an escalation dropped out of view long before
the situation behind it did, flipping suppliers up and back down again. A live
escalation is now held for `GEO_ESCALATION_STICKY_HOURS` after it was last
corroborated, and labelled as held.

## World signals

On 28 Sep 2026 the board said "All clear" while the Strait of Hormuz was
carrying 3.9 ships a day against 87.9 a year earlier, Brent stood at $114.89 and
the Rhine at Kaub read 0 cm. None of that is news about a named supplier, and
every sensor the board had was pointed at named suppliers.
`scripts/world_signals.py` reads those conditions from the bodies that measure
them. It is not yet called by `update_intel.py`.

| Signal | Source (all keyless) | Notable / severe |
|---|---|---|
| Daily transits: Hormuz, Bab el-Mandeb, Suez, Malacca, Taiwan Strait, Panama, Cape of Good Hope | IMF PortWatch | last 7 days of data vs the same days a year earlier: ≤ −30% / ≤ −60% |
| Rhine at Kaub and Maxau, Kaub 48-hour forecast | PEGELONLINE (WSV, licence DL-DE Zero 2.0) | Kaub below 80 / 40 cm; Maxau below 375 / 335 cm |
| Orange and red disaster alerts, last 14 days | GDACS | the alert on the event's latest episode: orange / red |
| Brent (daily) | FRED `DCOILBRENTEU` | a rise of 2σ / 3.5σ against three months of daily moves, or +25% / +50% on its one-year median |
| EU gas, US wood pulp PPI, aluminium (monthly) | FRED `PNGASEUUSDM`, `WPU0911`, `PALUMUSDM` | +20% / +40% on the same month a year earlier |

Which suppliers each signal reaches lives in `data/world_links.json`, edited by
hand like `suppliers.json`: commodities map to supplier categories (Brent to
filter materials, mechanical, EMS and batteries), chokepoints to supplier
countries (the Asia–Europe lanes to China, Japan, South Korea and India) or,
for Hormuz, to every category that follows the oil price, and the Rhine to
Cerdia, AMCOR and CNT by name. The tests check the file against the watchlist.

```python
collect_world_signals(suppliers: list[dict], now: datetime | None = None) -> dict
```

```json
{
  "fetched_at": "2026-09-29T09:22:20+00:00",
  "level": "quiet | notable | severe",
  "drivers": ["Strait of Hormuz: transits down 96% on a year earlier (week to 20 Sep)"],
  "commodities": [item], "chokepoints": [item], "rivers": [item], "hazards": [item],
  "sources": {"imf_portwatch": {"status": "ok | failed | empty", "detail": "..."}}
}
```

Every item carries `id`, `label`, `value`, `unit`, `as_of`, `baseline`,
`change_pct`, `severity`, a one-sentence `headline`, `affected_categories`,
`affected_suppliers`, `source` and `source_url`. Rivers add the forecast range,
Brent its daily move, hazards their event type, alert levels and countries.

Every source fails soft: one that is down, answers nonsense or has stopped
updating is reported under `sources` and contributes nothing, and the whole call
returns within 35 seconds. Things to know when reading it:

- **Nothing here is today's number.** PortWatch runs about a week behind, Brent
  reaches FRED a week late and the monthly series two months late. Every item
  says what date it describes.
- **A year-on-year comparison cannot see a disruption that was already running a
  year ago.** The Suez Canal averaged 41.9 transits a day in the week to 20 Sep
  2026 and 41.3 a year before, which reads as normal; the same week of 2023
  averaged 72.0.
- **A gauge reading is a height above an arbitrary zero, not a depth.** Kaub at
  4 cm still has about 1.2 m in the channel, so gauges carry the long-run mean
  as a reference but no percentage change.
- **Only rises in a price count as risk.** A sharp fall in Brent is relief for
  every buyer of acetate tow and plastics.

## GDELT event files

The GDELT DOC API answers GitHub's runners with HTTP 429: in the snapshot of
29 Sep 2026 03:05 UTC, nine of the eleven supplier countries had last been
refused, and Sweden's reading was thirteen days old. `scripts/gdelt_files.py`
builds the same per-country reading from GDELT's raw event files instead
(`data.gdeltproject.org/gdeltv2/`, a new file every 15 minutes, no rate limit).
It is not yet called by `update_intel.py`.

It reads the last 24 hours: measured on 29 Sep 2026, that is 96 files and 6.9 MB
zipped (43.6 MB unzipped, about 107,000 events), fetched in 1.4 seconds eight at
a time and 2.7 seconds one at a time. It gives up at 30 seconds or 40 MB, and
publishes nothing if it read less than three quarters of the window, so the
caller keeps the previous readings instead.

```python
fetch_gdelt_from_files(countries: list[str], relevance_for_country: dict[str, callable],
                       now=None) -> tuple[dict, dict]   # (geopolitical_intel, source_status)
```

Each country entry keeps the shape the `/geopolitical` page renders today, with
`query_mode: "events"` and `window: "1d"` (which the page already words as "last
24 hours"), plus `event_count`, `event_mix` (protest, sanctions/embargo, coerce,
assault, fight, mass violence) and `goldstein_avg`. What changes underneath:

- Events are placed by where GDELT geocodes the action (`ActionGeo_CountryCode`,
  FIPS 10-4: Switzerland is `SZ`, Austria `AU`, Australia `AS`).
- `article_count` counts distinct source articles; tone and Goldstein are
  averaged over events weighted by how many articles carried each one.
- The files carry no headlines, so each is read back out of the article URL's
  slug. A URL without a readable slug still counts; it just cannot be listed.
- Relevance is the caller's `relevance(title, url) -> int`: only articles
  scoring above 0 are listed, the top five by relevance, then most negative tone.

`source_status` reports `status` (`ok | failed | empty`), a `detail` line, the
files expected, read, missing and failed, bytes, seconds, the window actually
covered, and a per-country status (`ok | empty | unmapped | failed`).

## Graceful Fallback

If any data source fails:
- The dashboard continues to work with the last known good data
- A timestamp badge shows data staleness
- Zero downtime, zero errors

## Getting the brief where the reader already is

A dashboard only works if someone opens it. `scripts/send_digest.py` pushes the
same change feed the front page leads with to Slack or Telegram, and the
harvest workflow calls it in two modes:

- **alert** — after every harvest, but only fires on a real escalation
  (a confirmed CRITICAL/HIGH signal, or the overall status going RED).
  Otherwise silent, so an alert keeps meaning something.
- **daily** — one brief on the harvest that lands in the European morning,
  whether or not anything moved. A quiet day gets one short line; that is the
  point.

Both are opt-in. With none of these set, the script prints what it would have
sent and exits 0:

| Secret / variable | Purpose |
|---|---|
| `SLACK_WEBHOOK_URL` | Slack incoming webhook |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Telegram delivery |
| `DASHBOARD_URL` (repo *variable*) | Link included in the message |

Try it locally without sending anything:

```bash
python scripts/send_digest.py --mode daily --dry-run
```

## Tests and CI

`tests/` covers the rules that decide what the board shows — keyword matching,
price-move classification, macro scoring, the split between event and
structural risk, and what does and does not reach the change feed or the daily
brief. No network, no yfinance required.

```bash
pip install -r requirements-dev.txt && pytest tests/ -q
```

`.github/workflows/ci.yml` runs those tests, validates the hand-maintained
`suppliers.json` / `country_risk.json`, type-checks and builds the frontend on
every push and pull request. It skips the harvester's own data-only commits.

## Notes

- SEC EDGAR requires a properly formatted User-Agent header
- Some endpoints may require API keys (configured in the script)
- The dashboard is fully static and can be deployed to any static host

## License

MIT

