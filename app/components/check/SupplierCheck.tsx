'use client';

import { useDeferredValue, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import countryRiskData from '../../../data/country_risk.json';
import type { CountryRiskFile } from '../../../types/extras';
import { BOOKING_URL } from '../scan/contact';
import { MAX_SCAN_ROWS } from '../scan/compose';
import { stashScanPrefill } from '../scan/prefill';
import { analyseList, topForScan } from './analyseList';
import CheckResults from './CheckResults';
import { EXAMPLE_LIST } from './exampleList';
import { DELIMITER_LABEL, parseList } from './parseList';

const countryRisk = (countryRiskData as unknown as CountryRiskFile).countries;

const MAX_SKIPPED_SHOWN = 5;

// Everything on this page happens in this component's memory. No request is
// made with the list, nothing is written to storage, and closing the tab
// drops it. The one exception is the explicit "take my top ten" button,
// which hands ten names to /scan through sessionStorage in the same tab.
export default function SupplierCheck() {
  const router = useRouter();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState(EXAMPLE_LIST);
  const [whatIfChoice, setWhatIfChoice] = useState<string | null>(null);

  // Typing stays instant on a long paste; the analysis catches up a beat later.
  const deferredText = useDeferredValue(text);
  const parsed = useMemo(() => parseList(deferredText), [deferredText]);
  const analysis = useMemo(() => analyseList(parsed.rows, countryRisk), [parsed]);

  const inputIsExample = text === EXAMPLE_LIST;
  const resultsAreExample = deferredText === EXAMPLE_LIST;
  const hasRows = parsed.rows.length > 0;

  // Keep the visitor's pick while it still exists in the list; otherwise fall
  // back to the country carrying the most suppliers.
  const whatIfCountry =
    whatIfChoice && analysis.countries.some((country) => country.country === whatIfChoice)
      ? whatIfChoice
      : analysis.countries[0]?.country ?? null;

  const scanPicks = useMemo(() => topForScan(parsed.rows, MAX_SCAN_ROWS), [parsed]);

  const sendToScan = () => {
    stashScanPrefill(scanPicks);
    router.push('/scan');
  };

  const clearList = () => {
    setText('');
    inputRef.current?.focus();
  };

  const skippedShown = parsed.skipped.slice(0, MAX_SKIPPED_SHOWN);

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3 rounded-xl border border-green-300 bg-green-50 px-4 sm:px-5 py-4">
        <svg className="mt-0.5 h-5 w-5 shrink-0 text-green-700" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
        </svg>
        <p className="text-sm text-green-900 leading-relaxed">
          <span className="font-semibold">Nothing leaves your browser.</span> The analysis runs in
          this tab. Your list is not uploaded, not sent anywhere and not saved: close the tab and it
          is gone.
        </p>
      </div>

      <section className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="px-4 sm:px-6 py-4 bg-gray-50 border-b border-gray-200 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="supplier-list" className="text-lg font-bold text-gray-900">
              Your supplier list
            </label>
            {inputIsExample && (
              <span className="px-2 py-0.5 rounded bg-amber-200 text-amber-900 text-[11px] font-bold uppercase tracking-wider">
                Example · invented suppliers
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={clearList}
              disabled={text === ''}
              className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={() => setText(EXAMPLE_LIST)}
              disabled={inputIsExample}
              className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Load the example
            </button>
          </div>
        </div>

        <div className="px-4 sm:px-6 py-4 space-y-3">
          <p id="supplier-list-help" className="text-sm text-gray-600 leading-relaxed">
            One supplier per line: <span className="font-mono text-gray-800">name, country, category, tier</span>.
            The country is where the supplying site is, not the head office. Tier is optional:
            Critical, High, Medium or Low, or 1 to 3 (1 = Critical). Paste straight from Excel or
            Google Sheets, or from a CSV export; a header row is recognised and extra columns are
            ignored.
          </p>
          <textarea
            id="supplier-list"
            ref={inputRef}
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={12}
            wrap="off"
            spellCheck={false}
            autoCapitalize="off"
            autoComplete="off"
            autoCorrect="off"
            aria-describedby="supplier-list-help supplier-list-status"
            placeholder={'Acme Packaging, Poland, Printed Packaging, Critical\nBeta Cells, China, Batteries, High'}
            className="w-full rounded-lg border border-gray-300 bg-white p-3 font-mono text-sm leading-relaxed text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-800"
          />

          <div id="supplier-list-status" aria-live="polite" className="text-xs text-gray-600 space-y-1">
            {hasRows ? (
              <p>
                Read {parsed.rows.length} line{parsed.rows.length === 1 ? '' : 's'}:{' '}
                {analysis.supplierCount} supplier{analysis.supplierCount === 1 ? '' : 's'} in{' '}
                {analysis.countries.length} countr{analysis.countries.length === 1 ? 'y' : 'ies'}
                {parsed.header ? ' · header row recognised' : ''} · {DELIMITER_LABEL[parsed.delimiter]}
              </p>
            ) : (
              <p>{text.trim() === '' ? 'Paste or type a list to see the analysis.' : 'No supplier could be read yet. Each line needs at least a name and a country.'}</p>
            )}
            {parsed.skipped.length > 0 && (
              <p className="text-amber-800">
                Skipped {parsed.skipped.length} line{parsed.skipped.length === 1 ? '' : 's'}:{' '}
                {skippedShown.map((line) => `line ${line.line} (${line.reason})`).join(', ')}
                {parsed.skipped.length > skippedShown.length ? `, and ${parsed.skipped.length - skippedShown.length} more` : ''}.
              </p>
            )}
            {parsed.mergedDuplicates > 0 && (
              <p>
                {parsed.mergedDuplicates} repeated line{parsed.mergedDuplicates === 1 ? '' : 's'} merged
                (same supplier, country and category).
              </p>
            )}
            {parsed.unrecognisedTiers.length > 0 && (
              <p className="text-amber-800">
                Tier not recognised: {parsed.unrecognisedTiers.slice(0, 5).map((tier) => `“${tier}”`).join(', ')}. Those
                lines count as having no tier.
              </p>
            )}
            {parsed.rowsWithoutCategory > 0 && (
              <p>
                {parsed.rowsWithoutCategory} line{parsed.rowsWithoutCategory === 1 ? ' has' : 's have'} no category. They
                count per country but not per category.
              </p>
            )}
          </div>
        </div>
      </section>

      {hasRows && (
        <CheckResults
          rows={parsed.rows}
          analysis={analysis}
          isExample={resultsAreExample}
          whatIfCountry={whatIfCountry}
          onWhatIfChange={setWhatIfChoice}
        />
      )}

      <section className="rounded-xl bg-gradient-to-r from-blue-900 via-blue-800 to-blue-900 text-white shadow-lg px-5 sm:px-8 py-6 sm:py-8">
        <h2 className="text-xl sm:text-2xl font-bold">Want your real suppliers checked?</h2>
        <p className="mt-2 max-w-3xl text-sm sm:text-base text-blue-100 leading-relaxed">
          This page only knows what you pasted. The free scan looks each supplier up: sanctions and
          export-control screening, recent news and filings, country exposure and single points of
          failure, written up within 48 hours. Up to ten suppliers.
        </p>
        <div className="mt-5 flex flex-wrap gap-3">
          <Link
            href="/scan"
            className="inline-flex items-center rounded-lg bg-white px-4 py-2 font-semibold text-blue-900 hover:bg-blue-50 transition-colors"
          >
            Get a free supplier scan
          </Link>
          {hasRows && !inputIsExample && (
            <button
              type="button"
              onClick={sendToScan}
              className="inline-flex items-center rounded-lg border border-white/50 px-4 py-2 font-semibold text-white hover:bg-white/10 transition-colors"
            >
              Take my top {scanPicks.length} to the scan form
            </button>
          )}
          <a
            href={BOOKING_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center rounded-lg border border-white/50 px-4 py-2 font-semibold text-white hover:bg-white/10 transition-colors"
          >
            Book a call
          </a>
        </div>
        {hasRows && !inputIsExample && (
          <p className="mt-3 max-w-3xl text-xs text-blue-200 leading-relaxed">
            &ldquo;Take my top {scanPicks.length}&rdquo; opens the scan form in this tab with your{' '}
            {scanPicks.length === 1 ? 'supplier' : `${scanPicks.length} most critical suppliers`} filled in. Nothing is
            sent until you choose to send it.
          </p>
        )}
      </section>
    </div>
  );
}
