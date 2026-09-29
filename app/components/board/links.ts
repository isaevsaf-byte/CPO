// Where an interested reader goes next. The board used to be a dead end: a
// procurement lead who was sent the link had no way to find out who built it
// or how to get the same thing on their own supplier list.
export const BOOKING_URL = 'https://cal.com/safarisaev';
export const TELEGRAM_URL = 'https://t.me/SafarIsaev';
export const CONTACT_EMAIL = 'saf@safarisaev.ai';
export const CASE_STUDY_URL = 'https://safarisaev.ai/en/portfolio/cpowatchtower';
export const AUTHOR_NAME = 'Safar Isaev';

// Feed URLs come from third parties (news feeds, GDELT, recall and filing
// APIs) and React 18 does not refuse a `javascript:` href. Anything that is
// not plain http(s) is dropped rather than rendered as a link.
export function safeHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.toString() : undefined;
  } catch {
    return undefined;
  }
}
