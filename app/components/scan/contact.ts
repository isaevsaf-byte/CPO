// Where every call to action on the site points. One place, so a changed
// handle or booking link is a one-line edit.

export const CONTACT_EMAIL = 'saf@safarisaev.ai';
export const TELEGRAM_URL = 'https://t.me/SafarIsaev';
export const TELEGRAM_HANDLE = '@SafarIsaev';
export const BOOKING_URL = 'https://cal.com/safarisaev';

// encodeURIComponent rather than URLSearchParams: the latter writes spaces as
// "+", which several mail clients then show literally in the subject line.
// Line breaks go out as CRLF, which is what RFC 6068 asks for in a body.
export function mailtoHref(subject: string, body?: string): string {
  const parts = [`subject=${encodeURIComponent(subject)}`];
  if (body) {
    parts.push(`body=${encodeURIComponent(body.replace(/\r?\n/g, '\r\n'))}`);
  }
  return `mailto:${CONTACT_EMAIL}?${parts.join('&')}`;
}
