import type { Metadata } from 'next';
import ScanForm from '../components/scan/ScanForm';
import { BOOKING_URL, CONTACT_EMAIL, TELEGRAM_HANDLE, TELEGRAM_URL } from '../components/scan/contact';

export const metadata: Metadata = {
  title: 'Free supplier scan — Supply Chain Watchtower',
  description:
    'Send up to ten suppliers and get a written brief within 48 hours: sanctions and export-control screening, recent news and filings, country exposure and single points of failure.',
  alternates: { canonical: '/scan' },
};

const COVERED = [
  'Sanctions and export-control screening',
  'Recent news and filings',
  'Country exposure',
  'Single points of failure',
];

export default function ScanPage() {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-gradient-to-r from-blue-900 via-blue-800 to-blue-900 text-white shadow-lg overflow-hidden">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6">
          <h1 className="text-2xl sm:text-3xl font-bold">Free supplier scan</h1>
          <p className="text-blue-100 mt-2 text-sm sm:text-base max-w-3xl">
            Send up to ten suppliers. Get a written brief on them within 48 hours.
          </p>
        </div>
      </header>

      {/* On a phone the order is: what you get, the form, then how the list is
          used. On a wide screen the two side cards stack to the right. */}
      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8 grid grid-cols-1 lg:grid-cols-3 lg:grid-rows-[auto_1fr] gap-6">
        <section className="lg:col-start-3 lg:row-start-1 bg-white rounded-xl border border-gray-200 border-t-4 border-t-blue-900 shadow-sm p-5 sm:p-6 self-start">
          <h2 className="text-lg font-bold text-gray-900">What you get</h2>
          <p className="mt-2 text-sm text-gray-600">
            A written brief within 48 hours, covering each supplier you send:
          </p>
          <ul className="mt-3 space-y-2">
            {COVERED.map((item) => (
              <li key={item} className="flex items-start gap-2 text-sm text-gray-800">
                <svg className="mt-0.5 h-4 w-4 shrink-0 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                </svg>
                {item}
              </li>
            ))}
          </ul>
          <p className="mt-4 text-sm text-gray-600">Free for up to ten suppliers.</p>
        </section>

        <div className="lg:col-span-2 lg:col-start-1 lg:row-start-1 lg:row-span-2">
          <ScanForm />
        </div>

        <div className="lg:col-start-3 lg:row-start-2 space-y-6 self-start">
          <section className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 sm:p-6">
            <h2 className="text-lg font-bold text-gray-900">How your list is used</h2>
            <ul className="mt-3 space-y-2 text-sm text-gray-700 leading-relaxed">
              <li>Your list is used only for this scan.</li>
              <li>
                This page stores nothing. Your request leaves as an ordinary email, sent by you from
                your own inbox.
              </li>
              <li>The scan is done by Safar Isaev, who built this board.</li>
            </ul>
          </section>

          <section className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 sm:p-6">
            <h2 className="text-lg font-bold text-gray-900">Prefer to talk first?</h2>
            <div className="mt-3 flex flex-col gap-2">
              <a
                href={BOOKING_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center rounded-lg bg-blue-900 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-800 transition-colors"
              >
                Book a call
              </a>
              <a
                href={TELEGRAM_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-800 hover:bg-gray-50 transition-colors"
              >
                Message {TELEGRAM_HANDLE} on Telegram
              </a>
            </div>
            <p className="mt-3 text-xs text-gray-500">
              Or email <a href={`mailto:${CONTACT_EMAIL}`} className="underline hover:text-gray-700">{CONTACT_EMAIL}</a>.
            </p>
          </section>
        </div>
      </main>
    </div>
  );
}
