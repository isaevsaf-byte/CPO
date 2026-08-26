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
| US CPI / Fed funds | FRED | `FRED_API_KEY` (falls back to static values) |
| Prices, market news | yfinance | — |
| Competitor filings | SEC EDGAR 8-K | — |
| Supplier & country news | Google News RSS | — |
| Country news tone | GDELT (experimental, `/geopolitical`) | — |
| Executive summary | Claude (`claude-haiku-4-5`) | `ANTHROPIC_API_KEY` (skipped if unset) |

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
still completes, but US CPI/rate fall back to static values and no executive
summary is generated — so a locally produced snapshot is poorer than the one
the workflow commits, and is not usually worth committing.

3. Start the development server:
```bash
npm run dev
```

4. Open [http://localhost:3000](http://localhost:3000)

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
  Google News instead of yfinance
- `location` — must match a key in `GEOPOLITICAL_RISK_MAP` to pick up a
  country risk floor
- `category` — should have an entry in `category_segments` in the same file

A malformed or empty file fails the harvest rather than producing an empty
watchlist, which would read as "nothing to worry about".

## Intelligence Logic

### Cyber "Panic" Score
- **RED**: Ransomware campaign use + added in last 48h
- **AMBER**: Any new vulnerability in last 7 days
- **GREEN**: No changes

### Competitor "Distress" Signal
- **RED**: Item 1.03 (Bankruptcy) or Item 4.02 (Non-Reliance)
- **AMBER**: Item 5.02 (Director Departure)
- **GREEN**: Routine filings

### Supply Chain "Shock" (Macro)
- **RED**: FX volatility > 1.5%
- **GREEN**: FX volatility < 0.5%

## Graceful Fallback

If any data source fails:
- The dashboard continues to work with the last known good data
- A timestamp badge shows data staleness
- Zero downtime, zero errors

## Notes

- SEC EDGAR requires a properly formatted User-Agent header
- Some endpoints may require API keys (configured in the script)
- The dashboard is fully static and can be deployed to any static host

## License

MIT

