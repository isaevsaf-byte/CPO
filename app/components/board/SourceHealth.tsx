import type { SourceHealth as SourceHealthData } from '../../../types/intel';

// Every feed fails soft, which kept the harvest alive and also hid outages: the
// supplier news layer returned nothing from 1 August to 28 September and the
// board read "healthy" throughout. A source that answered nothing is shown as
// such, so silence is never mistaken for a quiet day.

const LABELS: Record<string, string> = {
  yfinance_prices: 'Share prices',
  yfinance_news: 'Company news (Yahoo)',
  google_news: 'News search (Google News)',
  fred: 'Inflation and rates (FRED)',
  cisa: 'Exploited vulnerabilities (CISA)',
  cpsc: 'Product recalls (CPSC)',
  ofac: 'Sanctions (OFAC)',
  ecb: 'Euro reference rate (ECB)',
  sec: 'Competitor filings (SEC)',
  gdelt: 'Country news tone (GDELT)',
  claude: 'Written summary (Claude)',
  world_signals: 'Chokepoints, Rhine, hazards, inputs',
  screening: 'Export-control screening',
  asia_filings: 'Hong Kong and Shenzhen filings',
  ransom: 'Ransomware leak sites',
};

export function sourceCounts(health?: SourceHealthData) {
  const entries = Object.values(health ?? {});
  return {
    total: entries.length,
    ok: entries.filter((e) => e.status === 'ok').length,
    failed: entries.filter((e) => e.status === 'failed').length,
    empty: entries.filter((e) => e.status === 'empty').length,
  };
}

export function SourceHealthBadge({ health }: { health?: SourceHealthData }) {
  const { total, ok, failed } = sourceCounts(health);
  if (total === 0) return null;
  const tone = failed > 0 ? 'bg-amber-400' : ok === total ? 'bg-green-400' : 'bg-yellow-300';
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-blue-100" title="How many data sources answered on the last harvest">
      <span className={`inline-block h-2 w-2 rounded-full ${tone}`} aria-hidden="true" />
      {ok}/{total} sources answered
    </span>
  );
}

export default function SourceHealthList({ health }: { health?: SourceHealthData }) {
  const entries = Object.entries(health ?? {});
  if (entries.length === 0) return null;
  const order = { failed: 0, empty: 1, ok: 2 } as const;
  entries.sort((a, b) => order[a[1].status] - order[b[1].status]);
  return (
    <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200">
      {entries.map(([key, value]) => (
        <li key={key} className="flex items-start gap-3 px-3 py-2 text-sm">
          <span
            className={`mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full ${
              value.status === 'ok' ? 'bg-green-500' : value.status === 'empty' ? 'bg-yellow-400' : 'bg-red-500'
            }`}
            aria-hidden="true"
          />
          <div className="min-w-0">
            <div className="font-medium text-gray-900">
              {LABELS[key] ?? key}
              <span className="ml-2 text-xs font-normal text-gray-500">
                {value.status === 'ok' ? 'answered' : value.status === 'empty' ? 'answered with nothing' : 'did not answer'}
              </span>
            </div>
            {value.detail && <div className="text-xs text-gray-600">{value.detail}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}
