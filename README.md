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
to a fallback rather than failing the harvest, and says so in `source_health`
(see [Source health](#source-health)).

| Signal | Source | Key |
|---|---|---|
| Cyber threats | CISA Known Exploited Vulnerabilities (KEV) catalog — every KEV added in the last 7 days is screened | — |
| Sanctions | OFAC SDN list (`sanctionslistservice.ofac.treas.gov`) | — |
| Safety recalls | CPSC recall database, last 90 days | — |
| Macro FX | ECB euro reference rates | — |
| US & EU CPI / policy rates | FRED | `FRED_API_KEY` (shows "not connected" if unset; scrubbed from every log line and recorded error) |
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

A price move is logged once per trading session, keyed on the supplier and the
session the move belongs to (`price_as_of`), and dated by that session — "GPI
-5.4% on Fri 18 Sep". It used to be once per 24 hours, which logged GPI's
Friday fall three times as yfinance kept serving it through the weekend.

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
still completes, but CPI and policy rates read "not connected", no executive
summary is generated, and `source_health` records both as `empty` — which
leaves the macro pillar `degraded`. A locally produced snapshot is poorer than
the one the workflow commits, and is not usually worth committing.

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
- `location` — the country of the **site that supplies the company**, not the legal
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

Currency pairs have their own rule, because a 2% floor no major pair clears on
an ordinary bad day made EUR/USD's -1.5% (5.6σ) read quiet and USD/CNY unable
to register at all (`classify_fx_move`):

- **severe**: ≥5σ and a move of at least 1.0%
- **notable**: ≥3σ and a move of at least 0.5%

Shares and the S&P 500 keep the rule above.

### A price reading says which session it is

Every price reading — supplier, peer, macro market — carries `price_as_of`,
the date of the trading session its latest close belongs to. yfinance does not
always serve the newest session, and a reading used to look the same whichever
day it came from:

- A move is described by its session when that is not today: "-5.4% on Fri 18
  Sep", not "-5.4% today". A Friday fall may keep a supplier flagged over the
  weekend — it is still the latest thing the market has said — but it is never
  described or logged as new.
- A reading from an *older* session than the one the previous snapshot showed
  is set aside, and the previous reading and its severity carry forward.
  Infineon's Monday -7.72% came back from a lagging copy of the series on the
  Wednesday and took the supplier pillar GREEN → AMBER → GREEN.
- A listed supplier whose price did not come back says so, rather than
  "Normal operations. No risk signals."

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
USD/CNY), on the same volatility yardstick — the S&P 500 on the share rule, the
two currency pairs on the FX rule above. One severe move is RED, one notable
move is AMBER, otherwise GREEN. The summary sentence follows from the z-score:
"within its normal range" only under 1σ, otherwise "about 1.9× its normal daily
move, below the level treated as unusual". Official statistics (CPI, policy
rate) come from FRED and carry the month they were observed; a region with no
live feed that still updates shows "not connected" rather than a stale number.

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

## Screening lists, exchange filings and leak-site claims

Three data modules in `scripts/`, not yet called by the harvester. All are
free and keyless, hold to their own time budget and never raise: each source
reports `ok`, `empty` (it answered but nothing could be read, which usually
means a layout change) or `failed`, with a reason. A supplier missing from
`hits` means "no match" only while its sources are `ok`.

| Module | Reads | Entry point |
|---|---|---|
| `screening.py` | US Consolidated Screening List (one ~17 MB CSV merging twelve lists: OFAC SDN, BIS Entity List, Military End User, Treasury's Chinese military companies and more); DHS UFLPA Entity List; Federal Register, last 30 days | `screen_suppliers(suppliers, name_terms, now=None)` |
| `filings_asia.py` | HKEXnews (Smoore 06969, BYD Electronic 00285) and CNINFO (EVE Energy 300014, Porton 300363), last 14 days | `fetch_asia_filings(now=None)` |
| `ransom.py` | RansomLook `/api/recent` and `/api/search`, last 30 days | `fetch_ransom_claims(name_terms, now=None)` |

`name_terms` is `{name: supplier_search_terms(name)}` from `update_intel.py`,
which the modules cannot import because it loads the watchlist at import.
`scripts/name_matching.py` applies the harvester's rules to it:

- **Whole words, after folding punctuation to spaces.** "SPORTON International
  Inc." is not Porton, "PESA Bydgoszcz" is not BYD and "Amcore" is not Amcor,
  while a leak post titled "jabil.com" is Jabil and the DHS page's
  `Xinjiang&nbsp;Goens` is "Xinjiang Goens".
- **No term under five characters on its own.** On the screening list "GPI"
  is a Russian physics institute and "ITC" a Cypriot consultancy. A supplier
  left with no longer name is listed under `unscreened` (today, Fuji).
- **Companies only.** Screening-list people, vessels and aircraft are skipped:
  one SDN individual's alias ends in "Jabil".
- **Leak-site titles only.** RansomLook's search also matches descriptions; for
  "jabil" it returned an electronics distributor whose leak lists "sales
  orders to Jabil Defense".

### Legal names and parent companies

`data/supplier_parents.json` adds the legal names and parent companies that
`screening.py` checks as well, each with its sources and the date it was
checked. Huizhou BYD Electronic, for example, is screened as itself and
through BYD Precision Manufacture, BYD Electronic (International) (HKEX 0285)
and BYD Company. A hit through a parent carries `"via": "parent"` and the
parent's name, so a reviewer sees it is the group that is listed, not the
plant. Legal names also cover suppliers whose watchlist name is too short:
CNT is screened as CONTRAF-NICOTEX-TOBACCO. To use them on leak sites too,
pass `screening.expand_name_terms(name_terms)` to `fetch_ransom_claims`.

Checking the parents turned up two changes of ownership. Mativ sold SWM's
cigarette-paper business to Evergreen Hill Enterprise in 2023, so the watchlist
entry that read SWM (Mativ) with the `MATV` ticker had been following a
different company's share price since; it is now SWM International, unlisted,
and scanned by name. International Paper sold its stake in the IP Sun joint
venture in 2015; the entry keeps its name as the sample set has it.

### Federal Register topics

A plain full-text search is mostly noise ("section 301" is also a section of
the Food, Drug, and Cosmetic Act, and every Foreign-Trade Zone notice cites the
Section 301 duties), so each topic is limited to the agencies that act on it
and kept only when the title says what the document is about.
"Agency Information Collection Activities" paperwork is always dropped.

| `topic` | Agencies | Kept when the title names | `affected_categories` |
|---|---|---|---|
| `bis_entity_list` | BIS | the Entity List | EMS, Batteries, EE Component |
| `section_301` | USTR | a country where a watchlist supplier is, or "various economies" | the categories sourced from that country; `[]` for "various economies" |
| `section_232` | BIS, the President | a watchlist material: aluminium, steel, copper, polysilicon, semiconductors, critical minerals, lithium, graphite, timber, pulp | by material |
| `uflpa` | DHS | UFLPA or forced labor | Batteries, EMS, EE Component |
| `fda_ends` | FDA | ENDS, e-cigarettes, vaping, nicotine or tobacco products | EMS, Batteries, Nicotine, Oral Fleece |
| `adcvd` | ITA, ITC | cellulose acetate, acetate tow, cigarette or tipping paper, lithium-ion, lithium hexafluorophosphate, anode material, paperboard, cartonboard, folding cartons | by product |

### Exchange filing flags

| Severity | Flag |
|---|---|
| high | profit warning, trading halt, suspension, 停产 production halt, 停牌 trading suspended, 立案 regulator investigation, 预亏 expected loss |
| medium | inside information, 质押 share pledge, 诉讼 litigation, 业绩预告 earnings pre-announcement, 减持 shareholder selling |

For HKEX the headline category and the Chinese title are checked with the
English title: Smoore's 2026-07-03 notice of its controlling shareholder
selling down only says so in Chinese (減持) and in its category (Inside
Information). Chinese terms are matched in simplified (CNINFO) and traditional
(HKEX) characters, and a pledge being released (解除质押) is not a pledge.
The exchanges' internal ids (HKEX stockId, CNINFO orgId) are looked up on
every run, falling back to the ones found on 2026-09-29.

### Output

```text
screen_suppliers  -> {fetched_at, hits, notices, sources, unscreened, duration_seconds}
  hits[supplier]  -> [{list, entity, programs, country, source_url, listed_since,
                       matched_term, via: "name"|"parent", parent}]
  notices         -> [{title, date, url, agencies, topic, affected_categories, type, document_number}]
fetch_asia_filings -> {fetched_at, window_days, filings, listings, sources, duration_seconds}
  filings[supplier] -> [{title, date, url, flags, severity: "high"|"medium"|"none", exchange, stock_code}]
fetch_ransom_claims -> {fetched_at, hits, sources, unscreened, duration_seconds}
  hits[supplier]  -> [{group, title, date, url}]   # url: RansomLook's group page, never the leak site
```

Dates are `YYYY-MM-DD`; `fetched_at` carries its UTC offset. Try them by hand:

```bash
python - <<'PY'
import json, sys
sys.path.insert(0, "scripts")
import filings_asia
print(json.dumps(filings_asia.fetch_asia_filings(), ensure_ascii=False, indent=1))
PY
```


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

## Source health

A failing source degrades the harvest rather than stopping it, and it says so.
Each external source records what it delivered this cycle — `ok`, `failed` or
`empty` (answered, or not configured, but gave nothing usable) — with a line a
person can read, written to the snapshot as `source_health`:

```json
"source_health": {
  "yfinance_prices": {"status": "ok", "detail": "prices for 3/3 markets, 4/4 peers, 12/12 listed suppliers", "checked_at": "2026-09-29T08:28:20+00:00"},
  "fred": {"status": "empty", "detail": "FRED_API_KEY not set, so CPI and policy rates read not connected", "checked_at": "2026-09-29T08:27:10+00:00"}
}
```

The sources are `yfinance_prices`, `yfinance_news`, `google_news`, `fred`,
`cisa`, `cpsc`, `ofac`, `ecb`, `sec`, `gdelt` and `claude`. Each pillar's
`status` follows from its own sources — `success` only when every one of them
is `ok`, `degraded` otherwise:

| Pillar | Sources |
|---|---|
| macro | yfinance prices for the three markets, FRED, ECB |
| peers | yfinance prices and headlines for the peers, SEC EDGAR |
| suppliers | yfinance prices and headlines for listed suppliers, Google News, CISA, CPSC, OFAC |

GDELT and Claude feed no pillar. GDELT counts as `ok` when any country returned
a fresh reading — it is rotated and rate-limited by design — and `failed` only
when none did.

The harvest exits **2** when any source failed (or the older critical-error
rules fire); the workflow treats that as a partial success, commits the
snapshot and still runs the alert. Until this, every pillar said `success`
whatever happened: failed Google News searches logged at DEBUG, FRED failures
were not recorded, and an open yfinance circuit breaker returned empty readings
without a word.

## Getting the brief where the reader already is

A dashboard only works if someone opens it. `scripts/send_digest.py` pushes the
same change feed the front page leads with to Slack or Telegram, in two modes:

- **alert** — run by the harvest workflow after every harvest, but only fires
  on a real escalation (a confirmed CRITICAL/HIGH signal, or the overall status
  going RED). Otherwise silent, so an alert keeps meaning something.
- **daily** — one brief every morning from its own workflow,
  `.github/workflows/daily-brief.yml` (05:30 UTC, or run it by hand), whether
  or not anything moved. A quiet day gets one short line; that is the point.
  It used to ride on whichever harvest landed between 05 and 08 UTC, and
  scheduler drift meant none did after 1 Sep, so it never sent.

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
price-move classification (shares and FX), which session a price reading
belongs to, macro scoring, the split between event and structural risk, source
health and the exit code, and what does and does not reach the change feed or
the daily brief. No network, no yfinance required.

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

