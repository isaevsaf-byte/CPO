'use client';

import Link from 'next/link';
import { AUTHOR_NAME, BOOKING_URL, CASE_STUDY_URL, CONTACT_EMAIL, TELEGRAM_URL } from './links';
import { trackEvent } from './track';

// The next step for a reader who likes what they see. Kept to one offer and
// three ways to take it, placed right after the status answer, which is where
// a reader decides whether the board is worth their time.

export default function CtaBanner({ placement }: { placement: 'after-status' | 'footer' }) {
  const dark = placement === 'footer';
  return (
    <section
      aria-label="Get this board on your own suppliers"
      className={`rounded-xl border p-5 sm:p-6 ${
        dark ? 'border-gray-700 bg-gray-800 text-gray-100' : 'mb-8 border-blue-200 bg-blue-50 text-gray-900'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0 max-w-2xl">
          <h2 className={`text-lg font-bold ${dark ? 'text-white' : 'text-gray-900'}`}>
            Want this watching your own suppliers?
          </h2>
          <p className={`mt-1 text-sm leading-relaxed ${dark ? 'text-gray-300' : 'text-gray-700'}`}>
            The same board on your supplier list, with your spend and stock cover behind every alert, and a written brief
            every Monday. Built and run by{' '}
            <a
              href={CASE_STUDY_URL}
              target="_blank"
              rel="noopener noreferrer"
              className={`font-medium underline underline-offset-2 ${dark ? 'text-white' : 'text-blue-900'}`}
              onClick={() => trackEvent('author_click', { placement })}
            >
              {AUTHOR_NAME}
            </a>
            .
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={BOOKING_URL}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => trackEvent('cta_book_call', { placement })}
            className="rounded-lg bg-blue-900 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-800"
          >
            Book a 20-minute call
          </a>
          <Link
            href="/scan"
            onClick={() => trackEvent('cta_free_scan', { placement })}
            className={`rounded-lg border px-4 py-2 text-sm font-semibold ${
              dark ? 'border-gray-500 text-white hover:bg-gray-700' : 'border-blue-900 text-blue-900 hover:bg-blue-100'
            }`}
          >
            Free scan of 10 suppliers
          </Link>
          <a
            href={TELEGRAM_URL}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => trackEvent('cta_telegram', { placement })}
            className={`px-2 py-2 text-sm font-medium underline underline-offset-2 ${dark ? 'text-gray-200' : 'text-blue-900'}`}
          >
            Telegram
          </a>
        </div>
      </div>
      {dark && (
        <p className="mt-3 text-xs text-gray-400">
          Or write to <span className="select-all font-mono">{CONTACT_EMAIL}</span>
        </p>
      )}
    </section>
  );
}
