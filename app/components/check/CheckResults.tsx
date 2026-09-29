'use client';

import type { CheckRow, CountrySummary, ListAnalysis } from '../../../types/extras';
import type { RiskLevel } from '../../../types/intel';
import { LevelPill, TierPill, percent } from './pills';
import WhatIfPanel from './WhatIfPanel';

interface CheckResultsProps {
  rows: CheckRow[];
  analysis: ListAnalysis;
  isExample: boolean;
  whatIfCountry: string | null;
  onWhatIfChange: (country: string) => void;
}

const LEVEL_ORDER: Record<RiskLevel, number> = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function Card({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-4 sm:px-6 py-4 bg-gray-50 border-b border-gray-200">
        <h2 className="text-lg font-bold text-gray-900">{title}</h2>
        {note && <p className="text-sm text-gray-600 mt-1">{note}</p>}
      </div>
      {children}
    </section>
  );
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub: string; tone: 'neutral' | 'red' | 'amber' | 'green' }) {
  // Only the top edge carries the colour, like the board's pillar cards.
  const border =
    tone === 'red' ? 'border-t-red-500' : tone === 'amber' ? 'border-t-amber-500' : tone === 'green' ? 'border-t-green-500' : 'border-t-blue-900';
  return (
    <div className={`bg-white rounded-xl shadow-sm border border-gray-200 border-t-4 ${border} p-4 sm:p-5`}>
      <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">{label}</div>
      <div className="mt-2 text-2xl sm:text-3xl font-bold text-gray-900">{value}</div>
      <div className="mt-1 text-xs text-gray-500">{sub}</div>
    </div>
  );
}

function ShareBar({ share }: { share: number }) {
  return (
    <div className="mt-1 h-1.5 w-full max-w-[8rem] ml-auto rounded bg-gray-100" aria-hidden="true">
      <div className="h-1.5 rounded bg-red-500" style={{ width: `${Math.max(2, Math.round(share * 100))}%` }} />
    </div>
  );
}

export default function CheckResults({ rows, analysis, isExample, whatIfCountry, onWhatIfChange }: CheckResultsProps) {
  const {
    categories,
    countries,
    singleSource,
    singleCountry,
    supplierCount,
    siteCount,
    tiersProvided,
    critHighTotal,
    sitesInFlaggedCountries,
  } = analysis;

  const flagged = countries
    .filter((country): country is CountrySummary & { standing: NonNullable<CountrySummary['standing']> } => country.standing !== null)
    .sort(
      (a, b) =>
        LEVEL_ORDER[a.standing.level] - LEVEL_ORDER[b.standing.level] ||
        b.suppliers.length - a.suppliers.length,
    );
  const unflagged = countries.filter((country) => country.standing === null).map((country) => country.country);
  const flaggedShare = siteCount > 0 ? sitesInFlaggedCountries / siteCount : 0;

  // Categories are only counted when the list has them; a list of names and
  // countries still gets the country view.
  const hasCategories = categories.length > 0;

  return (
    <div className="space-y-6">
      {isExample && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <span className="font-semibold">Example results.</span> These come from the invented list
          above. Paste your own list to replace them.
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        <Tile
          label="Suppliers"
          value={String(supplierCount)}
          sub={`in ${plural(countries.length, 'country', 'countries')}${siteCount !== supplierCount ? ` · ${siteCount} sites` : ''}`}
          tone="neutral"
        />
        <Tile
          label="Single-source"
          value={hasCategories ? String(singleSource.length) : '—'}
          sub={hasCategories ? `of ${plural(categories.length, 'category', 'categories')}` : 'add a category column'}
          tone={!hasCategories ? 'neutral' : singleSource.length > 0 ? 'red' : 'green'}
        />
        <Tile
          label="One-country"
          value={hasCategories ? String(singleCountry.length) : '—'}
          sub="categories with 2+ suppliers, all in one country"
          tone={!hasCategories ? 'neutral' : singleCountry.length > 0 ? 'amber' : 'green'}
        />
        <Tile
          label="Standing risk floor"
          value={`${sitesInFlaggedCountries} of ${siteCount}`}
          sub={`sites (${percent(flaggedShare)}) in countries that carry one`}
          tone={sitesInFlaggedCountries > 0 ? 'amber' : 'green'}
        />
      </div>

      {hasCategories && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card
            title="Single-source categories"
            note="One supplier and nothing behind it. If it stops, the category stops."
          >
            {singleSource.length > 0 ? (
              <ul className="divide-y divide-gray-100">
                {singleSource.map((category) => {
                  const row = rows.find((r) => r.category === category.category);
                  return (
                    <li key={category.category} className="px-4 sm:px-6 py-3 flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-sm font-semibold text-gray-900">{category.category}</div>
                        <div className="text-xs text-gray-600">
                          {category.suppliers[0]} · {category.countries.map((c) => c.country).join(', ')}
                        </div>
                      </div>
                      <TierPill tier={row?.tier ?? null} />
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="px-4 sm:px-6 py-4 text-sm text-gray-600">None. Every category has at least two suppliers.</p>
            )}
          </Card>

          <Card
            title="Concentrated in one country"
            note="Two or more suppliers, all in the same country. It looks diversified until that country has a bad month."
          >
            {singleCountry.length > 0 ? (
              <ul className="divide-y divide-gray-100">
                {singleCountry.map((category) => (
                  <li key={category.category} className="px-4 sm:px-6 py-3">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm font-semibold text-gray-900">{category.category}</span>
                      <span className="text-xs font-semibold text-amber-800 whitespace-nowrap">
                        all in {category.topCountry}
                      </span>
                    </div>
                    <div className="text-xs text-gray-600">{category.suppliers.join(', ')}</div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-4 sm:px-6 py-4 text-sm text-gray-600">
                None. Every category with two or more suppliers spans more than one country.
              </p>
            )}
          </Card>
        </div>
      )}

      {whatIfCountry && (
        <WhatIfPanel rows={rows} countries={countries} selected={whatIfCountry} onSelect={onWhatIfChange} />
      )}

      <Card
        title="By country"
        note={
          tiersProvided
            ? critHighTotal > 0
              ? `Where your ${plural(critHighTotal, 'Critical or High supplier sits', 'Critical and High suppliers sit')}, and what share of them each country holds.`
              : 'The list has tiers, but none of them are Critical or High.'
            : 'Add a tier column (Critical, High, Medium, or 1–3) to see where your most critical suppliers sit.'
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 sm:px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Country</th>
                <th className="px-3 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Suppliers</th>
                <th className="px-3 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Critical / High</th>
                <th className="hidden sm:table-cell px-4 sm:px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  Standing floor
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {countries.map((country) => (
                <tr key={country.country}>
                  <td className="px-4 sm:px-6 py-3 align-top">
                    <div className="font-semibold text-gray-900">{country.country}</div>
                    {country.categories.length > 0 && (
                      <div className="text-xs text-gray-500 max-w-md">{country.categories.join(', ')}</div>
                    )}
                    {country.standing && (
                      <div className="sm:hidden mt-1">
                        <LevelPill level={country.standing.level} />
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3 text-right align-top font-mono text-gray-900">{country.suppliers.length}</td>
                  <td className="px-3 py-3 text-right align-top">
                    {tiersProvided ? (
                      <>
                        <div className="font-mono text-gray-900">
                          {country.critHigh} of {country.suppliers.length}
                        </div>
                        {country.shareOfCritHigh !== null && country.critHigh > 0 && (
                          <>
                            <div className="text-xs text-gray-500">
                              {percent(country.shareOfCritHigh)} of all Critical/High
                            </div>
                            <ShareBar share={country.shareOfCritHigh} />
                          </>
                        )}
                      </>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="hidden sm:table-cell px-4 sm:px-6 py-3 align-top">
                    {country.standing ? (
                      <LevelPill level={country.standing.level} />
                    ) : (
                      <span className="text-xs text-gray-400">none</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card
        title="Standing country exposure"
        note="A floor comes from where a supplier sits, not from anything that happened this week. It is the same every day, so read it as background, not as news. The floors are the same country list the board uses for its own watchlist."
      >
        <div className="px-4 sm:px-6 py-4 space-y-3">
          {flagged.length > 0 ? (
            <ul className="space-y-3">
              {flagged.map((country) => (
                <li key={country.country} className="flex items-start gap-3">
                  <span className="pt-0.5 shrink-0">
                    <LevelPill level={country.standing.level} />
                  </span>
                  <div className="min-w-0 text-sm">
                    <span className="font-semibold text-gray-900">{country.country}</span>
                    <span className="text-gray-500"> · {plural(country.suppliers.length, 'supplier', 'suppliers')}</span>
                    <div className="text-gray-700">{country.standing.reason}</div>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-gray-600">No country on this list carries a standing floor.</p>
          )}
          {unflagged.length > 0 && (
            <p className="text-xs text-gray-500 pt-2 border-t border-gray-100">
              No standing floor: {unflagged.join(', ')}.
            </p>
          )}
        </div>
      </Card>

      {hasCategories && (
        <Card title="By category" note="Most fragile first: single-source, then one-country, then the heaviest lean on one country.">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 sm:px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Category</th>
                  <th className="px-3 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Suppliers</th>
                  <th className="px-4 sm:px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Where</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {categories.map((category) => (
                  <tr key={category.category}>
                    <td className="px-4 sm:px-6 py-3 align-top">
                      <div className="font-semibold text-gray-900">{category.category}</div>
                      <div className="text-xs text-gray-500 max-w-md">{category.suppliers.join(', ')}</div>
                      {category.singleSource && (
                        <span className="inline-block mt-1 px-2 py-0.5 rounded bg-red-100 text-red-800 text-[11px] font-semibold">
                          Single-source
                        </span>
                      )}
                      {category.singleCountry && (
                        <span className="inline-block mt-1 px-2 py-0.5 rounded bg-amber-100 text-amber-800 text-[11px] font-semibold">
                          One country
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-3 text-right align-top font-mono text-gray-900">{category.suppliers.length}</td>
                    <td className="px-4 sm:px-6 py-3 align-top text-gray-700">
                      {category.countries
                        .map((entry) => (category.countries.length > 1 ? `${entry.country} ${entry.sites}` : entry.country))
                        .join(' · ')}
                      {/* Only when one country actually leads; "33% in
                          Finland" on an even three-way split says nothing. */}
                      {category.countries.length > 1 && category.countries[0].sites > category.countries[1].sites && (
                        <div className="text-xs text-gray-500">
                          {percent(category.topShare)} in {category.topCountry}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
