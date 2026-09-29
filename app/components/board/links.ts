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
