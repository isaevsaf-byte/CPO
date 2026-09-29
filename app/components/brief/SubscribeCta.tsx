import { CONTACT_EMAIL, TELEGRAM_HANDLE, TELEGRAM_URL, mailtoHref } from '../scan/contact';

// On screen, two buttons. On paper, one line with the same two routes.
export default function SubscribeCta() {
  return (
    <>
      <section className="print:hidden rounded-xl bg-gradient-to-r from-blue-900 via-blue-800 to-blue-900 text-white shadow-lg px-5 sm:px-8 py-6">
        <h2 className="text-xl sm:text-2xl font-bold">Get this every Monday</h2>
        <p className="mt-1.5 text-sm sm:text-base text-blue-100">
          A one-page brief like this at the start of each week, by email or on Telegram.
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <a
            href={mailtoHref('Weekly brief', 'Please send me the weekly brief every Monday.')}
            className="inline-flex items-center rounded-lg bg-white px-4 py-2 font-semibold text-blue-900 hover:bg-blue-50 transition-colors"
          >
            Subscribe by email
          </a>
          <a
            href={TELEGRAM_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center rounded-lg border border-white/50 px-4 py-2 font-semibold text-white hover:bg-white/10 transition-colors"
          >
            Ask on Telegram
          </a>
        </div>
      </section>
      <p className="hidden print:block text-xs text-gray-700 pt-2 border-t border-gray-300">
        Get this every Monday: email {CONTACT_EMAIL} with the subject &ldquo;Weekly brief&rdquo;, or message{' '}
        {TELEGRAM_HANDLE} on Telegram.
      </p>
    </>
  );
}
