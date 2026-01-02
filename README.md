# Supply Chain Intelligence Dashboard

A zero-cost, sovereign intelligence engine using the "Flat Data" pattern. This dashboard aggregates intelligence from official government endpoints without requiring any infrastructure or database.

## Architecture

- **Harvester**: Python script runs via GitHub Actions every 6 hours
- **Database**: Git repository (JSON file)
- **View**: Next.js static dashboard
- **Cost**: $0 (GitHub Actions free tier)

## Data Sources

1. **Cyber Threats**: CISA Known Exploited Vulnerabilities (KEV) Catalog
2. **Macro FX**: ECB EUR/USD Reference Rates
3. **Competitor Intelligence**: SEC EDGAR 8-K Filings
4. **Sanctions**: ITA Consolidated Screening List
5. **Safety Recalls**: CPSC Product Recalls Database

## Setup

### Local Development

1. Install dependencies:
```bash
npm install
```

2. Run the harvester script manually:
```bash
python scripts/update_intel.py
```

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

