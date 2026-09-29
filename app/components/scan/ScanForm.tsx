'use client';

import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { ScanContact, ScanSupplierRow } from '../../../types/extras';
import { COUNTRY_SUGGESTIONS } from '../check/countries';
import CopyBox, { copyText } from './CopyBox';
import { MAX_SCAN_ROWS, filledRows, scanBody, scanSubject, validateScan } from './compose';
import type { ScanProblem } from './compose';
import { BOOKING_URL, CONTACT_EMAIL, TELEGRAM_HANDLE, TELEGRAM_URL, mailtoHref } from './contact';
import { takeScanPrefill } from './prefill';

const START_ROWS = 3;

type SentVia = 'email' | 'copy';

const inputBase =
  'w-full rounded-lg border bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-800';

function inputClass(invalid: boolean): string {
  return `${inputBase} ${invalid ? 'border-red-400 bg-red-50/40' : 'border-gray-300'}`;
}

// No backend: the request leaves as an email the visitor sends from their own
// mail app, or as text they copy into Telegram. The page itself sends nothing.
export default function ScanForm() {
  const lastId = useRef(START_ROWS);
  const [rows, setRows] = useState<ScanSupplierRow[]>(() =>
    Array.from({ length: START_ROWS }, (_unused, idx) => ({ id: idx + 1, name: '', country: '' })),
  );
  const [contact, setContact] = useState<ScanContact>({ name: '', company: '', email: '', note: '' });
  const [attempted, setAttempted] = useState(false);
  const [sent, setSent] = useState<{ via: SentVia; copied: boolean; count: number } | null>(null);
  const [prefilledCount, setPrefilledCount] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);

  // Suppliers handed over from /check. Read after mount: sessionStorage does
  // not exist while the page is prerendered.
  useEffect(() => {
    const prefill = takeScanPrefill();
    if (!prefill) return;
    setRows(prefill.map((row, idx) => ({ id: idx + 1, name: row.name, country: row.country })));
    lastId.current = prefill.length;
    setPrefilledCount(prefill.length);
  }, []);

  useEffect(() => {
    if (sent) panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [sent]);

  // Once a send has been tried, problems update as the visitor fixes them.
  const problems: ScanProblem[] = attempted ? validateScan(contact, rows) : [];
  const invalidRowIds = new Set(problems.filter((p) => p.rowId !== undefined).map((p) => p.rowId));
  const suppliersMissing = problems.some((p) => p.field === 'suppliers' && p.rowId === undefined);
  const hasProblem = (field: ScanProblem['field']) => problems.some((p) => p.field === field && p.rowId === undefined);

  const body = scanBody(contact, rows);
  const subject = scanSubject(contact.company);
  const supplierCount = filledRows(rows).length;

  const updateRow = (id: number, field: 'name' | 'country', value: string) =>
    setRows((current) => current.map((row) => (row.id === id ? { ...row, [field]: value } : row)));

  const addRow = () => {
    if (rows.length >= MAX_SCAN_ROWS) return;
    lastId.current += 1;
    const id = lastId.current;
    setRows((current) => [...current, { id, name: '', country: '' }]);
    // Focus the new row once it is on the page.
    window.setTimeout(() => document.getElementById(`scan-row-name-${id}`)?.focus(), 0);
  };

  const removeRow = (id: number) => setRows((current) => current.filter((row) => row.id !== id));

  const updateContact = (field: keyof ScanContact, value: string) =>
    setContact((current) => ({ ...current, [field]: value }));

  // Validates and, on failure, moves focus to the first thing to fix.
  const ready = (): boolean => {
    setAttempted(true);
    const found = validateScan(contact, rows);
    if (found.length === 0) return true;
    const first = found[0];
    const targetId =
      first.rowId !== undefined
        ? `scan-row-name-${first.rowId}`
        : first.field === 'suppliers'
          ? `scan-row-name-${rows[0]?.id}`
          : `scan-${first.field}`;
    document.getElementById(targetId)?.focus();
    return false;
  };

  const handleEmail = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!ready()) return;
    setSent((current) => ({ via: 'email', copied: false, count: (current?.count ?? 0) + 1 }));
    window.location.href = mailtoHref(subject, body);
  };

  const handleCopy = async () => {
    if (!ready()) return;
    const copied = await copyText(body);
    setSent((current) => ({ via: 'copy', copied, count: (current?.count ?? 0) + 1 }));
  };

  return (
    <form onSubmit={handleEmail} noValidate className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-4 sm:px-6 py-4 bg-gray-50 border-b border-gray-200">
        <h2 className="text-lg font-bold text-gray-900">Your suppliers</h2>
        <p className="text-sm text-gray-600 mt-1">
          Up to {MAX_SCAN_ROWS}. For each one, the country of the site that supplies you, not the
          head office: that is where a strike, a border closure or a sanction bites.
        </p>
      </div>

      <div className="px-4 sm:px-6 py-5 space-y-6">
        {prefilledCount > 0 && (
          <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900">
            Filled in from the list you checked: your {prefilledCount === 1 ? 'supplier' : `${prefilledCount} most critical suppliers`}.
            Change anything before you send.
          </div>
        )}

        <fieldset>
          <legend className="sr-only">Suppliers</legend>
          <div className="hidden sm:grid grid-cols-[1.5rem_1fr_1fr_2rem] gap-3 pb-2 text-xs font-semibold uppercase tracking-wider text-gray-500">
            <span />
            <span>Supplier</span>
            <span>Country of the supplying site</span>
            <span />
          </div>
          <ol className="space-y-3">
            {rows.map((row, idx) => {
              const rowInvalid = invalidRowIds.has(row.id) || (suppliersMissing && idx === 0);
              return (
                <li key={row.id} className="grid grid-cols-[1.5rem_1fr_2rem] sm:grid-cols-[1.5rem_1fr_1fr_2rem] gap-x-3 gap-y-2 items-center">
                  <span className="text-sm font-semibold text-gray-400 text-right">{idx + 1}</span>
                  <div className="min-w-0">
                    <label htmlFor={`scan-row-name-${row.id}`} className="sr-only">
                      Supplier {idx + 1} name
                    </label>
                    <input
                      id={`scan-row-name-${row.id}`}
                      type="text"
                      value={row.name}
                      onChange={(event) => updateRow(row.id, 'name', event.target.value)}
                      placeholder="Supplier name"
                      autoComplete="off"
                      aria-invalid={rowInvalid || undefined}
                      className={inputClass(rowInvalid)}
                    />
                  </div>
                  <div className="min-w-0 col-start-2 sm:col-start-auto row-start-2 sm:row-start-auto">
                    <label htmlFor={`scan-row-country-${row.id}`} className="sr-only">
                      Supplier {idx + 1} country of the supplying site
                    </label>
                    <input
                      id={`scan-row-country-${row.id}`}
                      type="text"
                      value={row.country}
                      onChange={(event) => updateRow(row.id, 'country', event.target.value)}
                      placeholder="Country of the supplying site"
                      list="scan-countries"
                      autoComplete="off"
                      className={inputClass(false)}
                    />
                  </div>
                  <div className="row-start-1 col-start-3 sm:col-start-auto sm:row-start-auto flex justify-center">
                    {rows.length > 1 && (
                      <button
                        type="button"
                        onClick={() => removeRow(row.id)}
                        aria-label={`Remove supplier ${idx + 1}`}
                        title="Remove this row"
                        className="rounded-full p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 transition-colors"
                      >
                        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
          <datalist id="scan-countries">
            {COUNTRY_SUGGESTIONS.map((country) => (
              <option key={country} value={country} />
            ))}
          </datalist>
          <div className="mt-3 pl-9 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={addRow}
              disabled={rows.length >= MAX_SCAN_ROWS}
              className="inline-flex items-center gap-1.5 rounded-lg border border-dashed border-gray-300 px-3 py-1.5 text-sm font-semibold text-blue-900 hover:bg-blue-50 disabled:cursor-not-allowed disabled:text-gray-400 disabled:hover:bg-transparent transition-colors"
            >
              <span aria-hidden="true">+</span> Add a supplier
            </button>
            <span className="text-xs text-gray-500">
              {rows.length >= MAX_SCAN_ROWS ? `${MAX_SCAN_ROWS} is the most a free scan covers.` : `${rows.length} of ${MAX_SCAN_ROWS} rows`}
            </span>
          </div>
        </fieldset>

        <div className="border-t border-gray-200 pt-5">
        <fieldset>
          <legend className="text-sm font-bold text-gray-900 mb-3">Where to send the brief</legend>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label htmlFor="scan-name" className="block text-xs font-semibold text-gray-600 mb-1">
                Your name
              </label>
              <input
                id="scan-name"
                type="text"
                value={contact.name}
                onChange={(event) => updateContact('name', event.target.value)}
                autoComplete="name"
                aria-invalid={hasProblem('name') || undefined}
                className={inputClass(hasProblem('name'))}
              />
            </div>
            <div>
              <label htmlFor="scan-company" className="block text-xs font-semibold text-gray-600 mb-1">
                Company
              </label>
              <input
                id="scan-company"
                type="text"
                value={contact.company}
                onChange={(event) => updateContact('company', event.target.value)}
                autoComplete="organization"
                aria-invalid={hasProblem('company') || undefined}
                className={inputClass(hasProblem('company'))}
              />
            </div>
            <div>
              <label htmlFor="scan-email" className="block text-xs font-semibold text-gray-600 mb-1">
                Work email
              </label>
              <input
                id="scan-email"
                type="email"
                value={contact.email}
                onChange={(event) => updateContact('email', event.target.value)}
                autoComplete="email"
                inputMode="email"
                aria-invalid={hasProblem('email') || undefined}
                className={inputClass(hasProblem('email'))}
              />
            </div>
          </div>
          <div className="mt-3">
            <label htmlFor="scan-note" className="block text-xs font-semibold text-gray-600 mb-1">
              Note <span className="font-normal text-gray-400">(optional)</span>
            </label>
            <textarea
              id="scan-note"
              value={contact.note}
              onChange={(event) => updateContact('note', event.target.value)}
              rows={3}
              placeholder="Anything that helps: the categories they cover, a supplier you are already worried about, a deadline."
              className={inputClass(false)}
            />
          </div>
        </fieldset>
        </div>

        {problems.length > 0 && (
          <div role="alert" className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900">
            <p className="font-semibold">Before this can go:</p>
            <ul className="mt-1 list-disc list-inside space-y-0.5">
              {problems.map((problem, idx) => (
                <li key={`${problem.field}-${problem.rowId ?? 'all'}-${idx}`}>{problem.message}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="border-t border-gray-200 pt-5">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="submit"
              className="inline-flex items-center gap-2 rounded-lg bg-blue-900 px-5 py-2.5 font-semibold text-white hover:bg-blue-800 transition-colors"
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
              Open email with my request
            </button>
            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex items-center rounded-lg border border-gray-300 bg-white px-4 py-2.5 font-semibold text-gray-800 hover:bg-gray-50 transition-colors"
            >
              Copy the request instead
            </button>
          </div>
          <p className="mt-3 text-xs text-gray-500 leading-relaxed">
            Nothing is sent from this page. The button opens a draft addressed to {CONTACT_EMAIL} in
            your own email app{supplierCount > 0 ? `, listing ${supplierCount === 1 ? 'your supplier' : `your ${supplierCount} suppliers`}` : ''}; you
            decide whether to send it.
          </p>
        </div>

        {sent && (
          <div ref={panelRef} className="rounded-xl border border-green-300 bg-green-50/60 p-4 sm:p-5 space-y-4">
            <div className="text-sm text-green-900 leading-relaxed space-y-1">
              {sent.via === 'email' ? (
                <>
                  <p className="font-semibold">Your email app should now be open with the request ready.</p>
                  <p>
                    Nothing goes until you press Send there. If no email opened, copy the text below
                    and send it to{' '}
                    <a href={mailtoHref(subject, body)} className="font-semibold underline">
                      {CONTACT_EMAIL}
                    </a>{' '}
                    with the subject &ldquo;{subject}&rdquo;, or message it on Telegram.
                  </p>
                </>
              ) : (
                <>
                  <p className="font-semibold">
                    {sent.copied ? 'The request is on your clipboard.' : 'Copy the request below.'}
                  </p>
                  <p>
                    Paste it into an email to{' '}
                    <a href={mailtoHref(subject, body)} className="font-semibold underline">
                      {CONTACT_EMAIL}
                    </a>{' '}
                    with the subject &ldquo;{subject}&rdquo;, or into a Telegram message to {TELEGRAM_HANDLE}.
                  </p>
                </>
              )}
            </div>

            <CopyBox key={sent.count} text={body} initiallyCopied={sent.copied} label="Your request" />

            <div className="flex flex-wrap gap-3">
              <a
                href={TELEGRAM_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-800 hover:bg-gray-50 transition-colors"
              >
                Send on Telegram
              </a>
              <a
                href={BOOKING_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-800 hover:bg-gray-50 transition-colors"
              >
                Book a call instead
              </a>
            </div>
          </div>
        )}
      </div>
    </form>
  );
}
