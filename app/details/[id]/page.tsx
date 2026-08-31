'use client';

import { useParams } from 'next/navigation';
import Link from 'next/link';
import intelData from '../../../data/intel_snapshot.json';
import type {
  IntelSnapshot,
  Supplier,
  PeerGroupItem,
  ChangeLogEntry,
} from '../../../types/intel';
import { getRiskColor, getExposureColor } from '../../../types/intel';

const intel = intelData as unknown as IntelSnapshot;

function parseSnapshotTime(isoString: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(isoString);
  return new Date(hasZone ? isoString : `${isoString}Z`);
}

function relativeAge(date: Date): string {
  const hours = (Date.now() - date.getTime()) / (1000 * 60 * 60);
  if (hours < 1) return 'just now';
  if (hours < 36) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <h2 className="text-lg font-bold text-gray-900 mb-4 border-b border-gray-200 pb-2">{title}</h2>
      {children}
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: React.ReactNode; note?: string }) {
  return (
    <div>
      <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-1">{label}</div>
      <div className="text-lg font-semibold text-gray-900">{value}</div>
      {note && <div className="text-xs text-gray-500 mt-0.5">{note}</div>}
    </div>
  );
}

export default function CompanyDetailPage() {
  const params = useParams();
  const id = params?.id as string;

  const normalize = (str: string): string =>
    str ? str.toLowerCase().trim().replace(/\s+/g, ' ') : '';

  const companyName = (() => {
    try {
      return decodeURIComponent(id || '');
    } catch {
      return id || '';
    }
  })();
  const normalizedSearchName = normalize(companyName);

  const peerGroup: PeerGroupItem[] = intel?.peer_group || [];
  let peer = peerGroup.find((p) => p.name && normalize(p.name) === normalizedSearchName);

  if (!peer) {
    const nameVariations: { [key: string]: string[] } = {
      'british american tobacco': ['bat', 'british american tobacco', 'british-american-tobacco'],
      'philip morris int.': ['pmi', 'philip morris international', 'philip morris'],
      'imperial brands': ['imperial', 'imperial brands plc'],
      'japan tobacco': ['jti', 'japan tobacco international'],
    };

    for (const [fullName, variations] of Object.entries(nameVariations)) {
      if (variations.some((v) => normalize(v) === normalizedSearchName)) {
        peer = peerGroup.find((p) => normalize(p.name) === normalize(fullName));
        if (peer) break;
      }
    }
  }

  const suppliers: Supplier[] = intel?.suppliers?.suppliers || [];
  const supplier = suppliers.find((s) => {
    const normalizedSupplierName = normalize(s.name || '');
    const normalizedSlug = (s.slug || '').toLowerCase().trim();
    const normalizedSearchSlug = normalizedSearchName.replace(/\s+/g, '-');
    return (
      normalizedSupplierName === normalizedSearchName ||
      normalizedSlug === normalizedSearchName ||
      normalizedSlug === normalizedSearchSlug
    );
  });

  const company = peer || supplier;

  if (!company) {
    return (
      <div className="min-h-screen bg-slate-50 p-8">
        <div className="max-w-4xl mx-auto">
          <div className="bg-white rounded-lg shadow p-8 text-center">
            <h1 className="text-2xl font-bold text-gray-900 mb-4">Company Not Found</h1>
            <p className="text-gray-600 mb-6">
              The requested company could not be found in our intelligence database.
            </p>
            <Link
              href="/"
              className="bg-blue-900 text-white px-6 py-2 rounded-lg hover:bg-blue-800 transition inline-block"
            >
              Back to Dashboard
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const entityName = company.name;
  const ticker = supplier ? supplier.stock_ticker : peer?.ticker;
  const hasTicker = !!ticker && ticker !== 'N/A';

  // Real, sourced headlines only — risk-matched items first (those carry a
  // keyword hit), then any other recent headline the harvester kept.
  const supplierHeadlines: string[] = supplier
    ? Array.from(
        new Set([
          ...(supplier.news_items ?? [])
            .map((item: { headline?: string }) => item?.headline)
            .filter((h): h is string => !!h),
          ...(supplier.google_news_headlines ?? []),
        ])
      ).slice(0, 5)
    : [];

  // Everything this entity did over the retained window, from the same change
  // log the dashboard's front page slices by visit time. A supplier page that
  // only shows the current state cannot answer "is this new, or has it been
  // like this for a fortnight" — which is the first question a CPO asks.
  const history: ChangeLogEntry[] = (intel?.change_log ?? [])
    .filter((entry) => entry.entity === entityName)
    .slice()
    .sort((a, b) => parseSnapshotTime(b.at).getTime() - parseSnapshotTime(a.at).getTime())
    .slice(0, 12);

  const googleNewsUrl = `https://www.google.com/search?q=${encodeURIComponent(entityName)}+supply+chain+news&tbm=nws`;
  const yahooFinanceUrl = hasTicker ? `https://finance.yahoo.com/quote/${ticker}` : null;
  const secFilingsUrl = `https://www.sec.gov/edgar/search/#/q=${encodeURIComponent(entityName)}`;

  const displayedRisk = (supplier?.event_risk_level ?? company.risk_level) as typeof company.risk_level;
  const move = supplier?.daily_change_pct ?? peer?.daily_change_pct ?? null;
  const sigma = supplier?.daily_sigma_pct ?? peer?.daily_sigma_pct ?? null;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center gap-4">
            <Link href="/" className="text-blue-900 hover:text-blue-700 font-semibold whitespace-nowrap">
              ← Back to Dashboard
            </Link>
            <div className="h-6 w-px bg-gray-300" />
            <div>
              <h1 className="text-3xl font-bold text-gray-900">{entityName}</h1>
              <div className="flex items-center gap-3 mt-2 flex-wrap">
                {hasTicker && <span className="text-sm text-gray-600 font-mono">{ticker}</span>}
                {/* The event level, matching the watchlist: a standing country
                    floor is shown separately under Active Signals rather than
                    colouring this pill. */}
                <span
                  className={`px-3 py-1 rounded-full text-xs font-bold border ${getRiskColor(displayedRisk)}`}
                >
                  Risk: {displayedRisk}
                </span>
                {supplier && (
                  <>
                    <span className="px-3 py-1 rounded-full text-xs font-semibold bg-blue-100 text-blue-800 border border-blue-300">
                      {supplier.category}
                    </span>
                    <span
                      className={`px-3 py-1 rounded-full text-xs font-bold border ${getExposureColor(supplier.bat_exposure)}`}
                    >
                      {supplier.bat_exposure} Exposure
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            <Section title="Primary Stats">
              <div className="grid grid-cols-2 gap-4">
                {supplier ? (
                  <>
                    <Stat label="Category" value={supplier.category} />
                    <Stat
                      label="Exposure tier"
                      value={supplier.bat_exposure}
                      note="Assigned for this demonstration"
                    />
                    <Stat
                      label="Location"
                      value={supplier.location || 'Unknown'}
                      note={
                        supplier.hq_country && supplier.hq_country !== supplier.location
                          ? `Supplying site. Headquarters: ${supplier.hq_country}`
                          : 'Supplying site'
                      }
                    />
                    <Stat label="Segment" value={supplier.segment || 'N/A'} />
                  </>
                ) : (
                  <>
                    <Stat label="Region" value={peer!.region} />
                    <Stat label="Sentiment" value={peer!.sentiment} />
                  </>
                )}
                {company.current_price != null && (
                  <Stat
                    label="Share Price"
                    value={company.current_price.toFixed(2)}
                    note={hasTicker ? `Last close · ${ticker}` : undefined}
                  />
                )}
                {move != null && (
                  <Stat
                    label="Daily Change"
                    value={
                      <span className={move < 0 ? 'text-red-600' : 'text-green-600'}>
                        {move > 0 ? '+' : ''}
                        {move.toFixed(2)}%
                      </span>
                    }
                    note={
                      sigma
                        ? `Normal daily range for this stock: ±${sigma.toFixed(1)}%`
                        : 'No volatility baseline available'
                    }
                  />
                )}
              </div>
            </Section>

            {/* Active signals — the evidence behind the risk level, which the
                dashboard row only summarises in one line. Sanctions, CVEs and
                recalls were previously collected by the harvester and then
                never rendered anywhere. */}
            {supplier && (
              <Section title="Active Signals">
                <div className="space-y-4">
                  {supplier.sanctions_hit && (
                    <div className="rounded border border-red-300 bg-red-50 p-4">
                      <div className="font-semibold text-red-900">🚫 Possible OFAC sanctions match</div>
                      <ul className="mt-2 space-y-1 text-sm text-red-800">
                        {(supplier.sanctions_matches ?? []).map((match, idx) => (
                          <li key={idx} className="font-mono">{match}</li>
                        ))}
                      </ul>
                      <p className="mt-2 text-xs text-red-700">
                        Automated name matching produces false positives. Confirm with compliance
                        before acting on this.
                      </p>
                    </div>
                  )}

                  {supplier.cyber_risk && (
                    <div className="rounded border border-orange-300 bg-orange-50 p-4">
                      <div className="font-semibold text-orange-900">
                        🔒 CISA Known Exploited Vulnerabilities
                      </div>
                      <ul className="mt-2 space-y-1 text-sm">
                        {(supplier.matching_vulnerabilities ?? []).map((vuln) => (
                          <li key={vuln.cveID}>
                            <a
                              href={`https://nvd.nist.gov/vuln/detail/${vuln.cveID}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="font-mono text-orange-900 hover:underline"
                            >
                              {vuln.cveID}
                            </a>
                            <span className="text-orange-800"> — {vuln.vulnerabilityName}</span>
                            {vuln.dateAdded && (
                              <span className="text-orange-700 text-xs"> (added {vuln.dateAdded})</span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {supplier.recall_risk && (
                    <div className="rounded border border-amber-300 bg-amber-50 p-4">
                      <div className="font-semibold text-amber-900">⚠️ CPSC safety recalls</div>
                      <ul className="mt-2 space-y-2 text-sm text-amber-900">
                        {(supplier.matching_recalls ?? []).map((recall) => (
                          <li key={recall.recallNumber}>
                            <a
                              href={recall.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="font-semibold hover:underline"
                            >
                              {recall.product} ({recall.recallNumber})
                            </a>
                            {recall.recallDate && (
                              <span className="text-xs text-amber-700"> · {recall.recallDate.slice(0, 10)}</span>
                            )}
                            {recall.description && (
                              <p className="text-xs text-amber-800 mt-0.5">{recall.description}</p>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {supplier.geopolitical_risk && (
                    <div className="rounded border border-orange-200 bg-orange-50/60 p-4">
                      <div className="font-semibold text-orange-900">
                        🌍 {supplier.location}: {supplier.geopolitical_risk.level}
                        {supplier.geopolitical_risk.baseline_only && (
                          <span className="ml-2 text-xs font-normal text-orange-700">
                            standing exposure, not a new development
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-orange-800 mt-1">{supplier.geopolitical_risk.reason}</p>
                      {supplier.geopolitical_risk.headlines?.length > 0 && (
                        <ul className="mt-2 space-y-1 text-xs text-orange-800 list-disc list-inside">
                          {supplier.geopolitical_risk.headlines.map((headline, idx) => (
                            <li key={idx}>{headline}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}

                  {supplier.price_move_only && (
                    <div className="rounded border border-gray-200 bg-gray-50 p-4">
                      <div className="font-semibold text-gray-900">📉 Unexplained price move</div>
                      <p className="text-sm text-gray-700 mt-1">
                        The risk level on this supplier currently reflects a share-price move with no
                        corroborating news, filing or event behind it. Treat the size of the move as
                        the fact, and the cause as unknown.
                      </p>
                    </div>
                  )}

                  {!supplier.sanctions_hit &&
                    !supplier.cyber_risk &&
                    !supplier.recall_risk &&
                    !supplier.geopolitical_risk &&
                    !supplier.price_move_only && (
                      <p className="text-sm text-gray-500">
                        No active signals. Nothing in the monitored sources has flagged this supplier.
                      </p>
                    )}
                </div>
              </Section>
            )}

            <Section title={supplier ? 'Recent Headlines' : 'Latest Headline'}>
              {peer && (
                <p className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
                  {peer.latest_headline || 'No headline available.'}
                </p>
              )}
              {supplier &&
                (supplierHeadlines.length > 0 ? (
                  <ul className="space-y-2">
                    {supplierHeadlines.map((headline, idx) => (
                      <li
                        key={idx}
                        className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200"
                      >
                        {headline}
                      </li>
                    ))}
                  </ul>
                ) : (
                  /* An empty feed is stated as empty. This block used to be
                     filled by templated prose ("on-time delivery metrics
                     above 98%") generated from the exposure tier. */
                  <p className="text-base text-gray-500 leading-relaxed bg-gray-50 p-4 rounded border border-dashed border-gray-300">
                    No news matched this supplier in the last 5 days. Silence here means nothing was
                    picked up &mdash; not that operations were verified. Use the search below to check
                    directly.
                  </p>
                ))}
              {supplier?.risk_analysis && (
                <div className="mt-4">
                  <div className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
                    Risk Analysis
                  </div>
                  <p className="text-base text-gray-700 leading-relaxed bg-gray-50 p-4 rounded border border-gray-200">
                    {supplier.risk_analysis}
                  </p>
                </div>
              )}
            </Section>

            <Section title="Deep Dive Actions">
              <div className="flex flex-wrap gap-3">
                <a
                  href={googleNewsUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 bg-blue-900 text-white px-4 py-2 rounded-lg hover:bg-blue-800 transition font-medium"
                >
                  🔎 Search Google News
                </a>
                {yahooFinanceUrl && (
                  <a
                    href={yahooFinanceUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 bg-green-700 text-white px-4 py-2 rounded-lg hover:bg-green-600 transition font-medium"
                  >
                    📈 Yahoo Finance Page
                  </a>
                )}
                <a
                  href={secFilingsUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 bg-gray-700 text-white px-4 py-2 rounded-lg hover:bg-gray-600 transition font-medium"
                >
                  📄 SEC Filings
                </a>
              </div>
            </Section>
          </div>

          <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <h3 className="text-base font-bold text-gray-900 mb-4">Current Signal</h3>
              <p className="text-sm text-gray-700 leading-relaxed">{company.last_signal}</p>
            </div>

            {/* Per-entity slice of the change log: how this supplier has moved
                over the retained window, not just where it stands today. */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
              <h3 className="text-base font-bold text-gray-900 mb-4">Recent Activity</h3>
              {history.length > 0 ? (
                <ul className="space-y-3">
                  {history.map((entry, idx) => (
                    <li key={idx} className="text-sm">
                      <div className="flex items-start gap-2">
                        <span
                          className={
                            entry.direction === 'up'
                              ? 'text-red-600'
                              : entry.direction === 'down'
                                ? 'text-green-600'
                                : 'text-gray-400'
                          }
                          aria-hidden="true"
                        >
                          {entry.direction === 'up' ? '▲' : entry.direction === 'down' ? '▼' : '•'}
                        </span>
                        <div className="min-w-0">
                          <div className="text-gray-900">{entry.headline}</div>
                          <div className="text-xs text-gray-400">
                            {relativeAge(parseSnapshotTime(entry.at))}
                          </div>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-gray-500">
                  Nothing has changed for this entity in the last three weeks.
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
