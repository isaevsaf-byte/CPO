# Supply Chain Intelligence Dashboard

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
- `location` — must match a country in `data/country_risk.json` to pick up a
  standing risk floor; a country absent from that file carries none, which is
  the correct state for stable countries
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

### Geopolitical escalations are held for 48 hours
Google News returns only the eight most recent matches for a country, and that
set turns over within hours — so an escalation dropped out of view long before
the situation behind it did, flipping suppliers up and back down again. A live
escalation is now held for `GEO_ESCALATION_STICKY_HOURS` after it was last
corroborated, and labelled as held.

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

`tests/` covers the scoring rules that decide what the board shows — keyword
matching, price-move classification, macro scoring, and what does and does not
reach the change feed. No network, no yfinance required.

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

