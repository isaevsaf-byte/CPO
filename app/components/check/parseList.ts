import type { CheckRow, CheckTier, ListDelimiter, ParsedList, SkippedLine } from '../../../types/extras';
import { normaliseCountry } from './countries';

// Reads whatever a procurement team is likely to paste: cells copied out of
// Excel or Google Sheets (tab-separated), a CSV export (comma, or semicolon in
// most of Europe), or lines typed by hand as "name, country, category, tier".
// Runs on every keystroke, entirely in the browser.

const DELIMITER_CHAR: Record<ListDelimiter, string> = {
  tab: '\t',
  comma: ',',
  semicolon: ';',
  pipe: '|',
};

export const DELIMITER_LABEL: Record<ListDelimiter, string> = {
  tab: 'tab-separated',
  comma: 'comma-separated',
  semicolon: 'semicolon-separated',
  pipe: 'pipe-separated',
};

export const TIER_RANK: Record<CheckTier, number> = { Critical: 0, High: 1, Medium: 2, Low: 3 };

type Role = 'name' | 'country' | 'category' | 'tier';
type RoleColumns = Partial<Record<Role, number>>;

interface RawRecord {
  cells: string[];
  line: number;
  text: string;
}

function isComment(line: string): boolean {
  return line.trimStart().startsWith('#');
}

function countOutsideQuotes(line: string, ch: string): number {
  let inQuotes = false;
  let count = 0;
  for (const c of line) {
    if (c === '"') inQuotes = !inQuotes;
    else if (c === ch && !inQuotes) count += 1;
  }
  return count;
}

// A paste from a spreadsheet always carries tabs. Otherwise the separator that
// appears on the most lines wins, so a semicolon export with commas inside
// company names ("Acme, Inc") is still read by its semicolons.
export function detectDelimiter(text: string): ListDelimiter {
  const sample = text
    .split(/\r?\n/)
    .filter((line) => line.trim() !== '' && !isComment(line))
    .slice(0, 25);
  if (sample.some((line) => line.includes('\t'))) return 'tab';

  let best: ListDelimiter = 'comma';
  let bestCoverage = -1;
  let bestTotal = -1;
  for (const candidate of ['comma', 'semicolon', 'pipe'] as const) {
    const counts = sample.map((line) => countOutsideQuotes(line, DELIMITER_CHAR[candidate]));
    const coverage = counts.filter((count) => count > 0).length;
    const total = counts.reduce((sum, count) => sum + count, 0);
    if (coverage > bestCoverage || (coverage === bestCoverage && total > bestTotal)) {
      best = candidate;
      bestCoverage = coverage;
      bestTotal = total;
    }
  }
  return best;
}

// CSV rules as spreadsheets write them: a field may be wrapped in double
// quotes, a quote inside it is doubled, and a quoted field may run across a
// line break.
function splitRecords(text: string, delimiter: string, honourQuotes: boolean): { records: RawRecord[]; unterminated: boolean } {
  const records: RawRecord[] = [];
  let cells: string[] = [];
  let cell = '';
  let inQuotes = false;
  let line = 1;
  let startLine = 1;
  let rawStart = 0;

  const endRecord = (endIndex: number) => {
    cells.push(cell);
    records.push({ cells, line: startLine, text: text.slice(rawStart, endIndex).replace(/\r$/, '') });
    cells = [];
    cell = '';
  };

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        if (ch === '\n') line += 1;
        cell += ch;
      }
      continue;
    }
    if (ch === '"' && honourQuotes && cell.trim() === '') {
      inQuotes = true;
      cell = '';
      continue;
    }
    if (ch === delimiter) {
      cells.push(cell);
      cell = '';
      continue;
    }
    if (ch === '\n') {
      endRecord(i);
      line += 1;
      startLine = line;
      rawStart = i + 1;
      continue;
    }
    if (ch === '\r') continue;
    cell += ch;
  }

  if (inQuotes) return { records, unterminated: true };
  if (cell !== '' || cells.length > 0) endRecord(text.length);
  return { records, unterminated: false };
}

const HEADER_WORDS: Record<Role, string[]> = {
  name: [
    'name', 'supplier', 'suppliers', 'supplier name', 'vendor', 'vendor name', 'company',
    'company name', 'legal name', 'supplier legal name',
  ],
  country: [
    'country', 'countries', 'site country', 'country of site', 'supplying country',
    'country of supplying site', 'country of the supplying site', 'supplier country', 'location',
    'site', 'site location', 'origin', 'country of origin', 'manufacturing country', 'plant country',
    'country code', 'ctry',
  ],
  category: [
    'category', 'categories', 'spend category', 'category name', 'commodity', 'commodity group',
    'sub category', 'subcategory', 'segment', 'material', 'material group', 'product category',
  ],
  tier: [
    'tier', 'supplier tier', 'exposure', 'exposure tier', 'criticality', 'critical', 'priority',
    'risk tier', 'segmentation', 'importance', 'classification',
  ],
};

function headerKey(cell: string): string {
  return cell
    .toLowerCase()
    .replace(/[_\-/.]+/g, ' ')
    .replace(/[^a-z0-9 ]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// A first line counts as a header when at least two of its cells are column
// names we recognise. Matching is on the whole cell, never a substring, so a
// data row like "Supplier Co, Germany, Materials" is not mistaken for one.
function readHeader(cells: string[]): RoleColumns | null {
  const keys = cells.map(headerKey);
  const roles: RoleColumns = {};
  (Object.keys(HEADER_WORDS) as Role[]).forEach((role) => {
    const matches = keys
      .map((key, idx) => ({ key, idx }))
      .filter(({ key, idx }) => HEADER_WORDS[role].includes(key) && !Object.values(roles).includes(idx));
    if (matches.length === 0) return;
    // "Vendor, Vendor name": the one that says "name" is the name.
    const preferred = role === 'name' ? matches.find(({ key }) => key.includes('name')) ?? matches[0] : matches[0];
    roles[role] = preferred.idx;
  });
  if (Object.keys(roles).length < 2) return null;

  // A header that names the country and category but calls the name column
  // something we don't know ("Partner", "Firm"): take the first column
  // nothing else claimed. A wrong guess shows up as odd names in the results,
  // which is easier to act on than every line being skipped.
  if (roles.name === undefined) {
    const taken = new Set(Object.values(roles));
    const free = cells.findIndex((_cell, idx) => !taken.has(idx));
    if (free >= 0) roles.name = free;
  }
  return roles;
}

const TIER_WORDS: Record<string, CheckTier> = {
  critical: 'Critical', crit: 'Critical', '1': 'Critical',
  high: 'High', '2': 'High',
  medium: 'Medium', med: 'Medium', moderate: 'Medium', '3': 'Medium',
  low: 'Low', '4': 'Low',
};

// Words or numbers. The numbers follow this board's own convention:
// 1 = Critical, 2 = High, 3 = Medium. "Critical (tier 1)" reads as Critical;
// "non-critical" is left unread rather than read as its opposite.
export function normaliseTier(raw: string): CheckTier | null {
  const key = raw.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  if (!key) return null;
  const numeric = key.match(/^(?:tier|t)?\s*([1-4])$/);
  if (numeric) return TIER_WORDS[numeric[1]];
  const tokens = key.split(' ');
  if (tokens.includes('not') || tokens.includes('non')) return null;
  for (const token of tokens) {
    if (/^\d$/.test(token)) continue;
    const tier = TIER_WORDS[token];
    if (tier) return tier;
  }
  return null;
}

function cellAt(cells: string[], idx: number | undefined): string {
  if (idx === undefined) return '';
  return (cells[idx] ?? '').replace(/\s+/g, ' ').trim();
}

export function parseList(text: string): ParsedList {
  const delimiter = detectDelimiter(text);
  let { records, unterminated } = splitRecords(text, DELIMITER_CHAR[delimiter], true);
  // An opening quote that never closes would swallow everything after it.
  // Read the text again with quotes taken literally instead.
  if (unterminated) {
    records = splitRecords(text, DELIMITER_CHAR[delimiter], false).records;
  }

  const meaningful = records.filter(
    (record) => record.cells.some((cell) => cell.trim() !== '') && !isComment(record.text),
  );

  let header: string[] | null = null;
  let roles: RoleColumns = { name: 0, country: 1, category: 2, tier: 3 };
  let body = meaningful;
  if (meaningful.length > 0) {
    const detected = readHeader(meaningful[0].cells);
    if (detected) {
      header = meaningful[0].cells.map((cell) => cell.trim()).filter((cell) => cell !== '');
      roles = detected;
      body = meaningful.slice(1);
    }
  }

  const skipped: SkippedLine[] = [];
  const unrecognisedTiers = new Set<string>();
  const categoryLabels = new Map<string, string>();
  const rowsByKey = new Map<string, CheckRow>();
  let mergedDuplicates = 0;
  let rowsWithoutCategory = 0;

  for (const record of body) {
    const name = cellAt(record.cells, roles.name);
    const countryRaw = cellAt(record.cells, roles.country);
    const categoryRaw = cellAt(record.cells, roles.category);
    const tierRaw = cellAt(record.cells, roles.tier);

    if (!name) {
      skipped.push({ line: record.line, text: record.text, reason: 'no supplier name' });
      continue;
    }
    if (!countryRaw) {
      skipped.push({
        line: record.line,
        text: record.text,
        reason: record.cells.length < 2 ? 'expected name, country, category' : 'no country',
      });
      continue;
    }

    const country = normaliseCountry(countryRaw);

    // Categories are grouped case-insensitively and shown as first typed.
    let category: string | null = null;
    if (categoryRaw) {
      const key = categoryRaw.toLowerCase();
      if (!categoryLabels.has(key)) categoryLabels.set(key, categoryRaw);
      category = categoryLabels.get(key) ?? categoryRaw;
    }

    let tier: CheckTier | null = null;
    if (tierRaw) {
      tier = normaliseTier(tierRaw);
      if (!tier) unrecognisedTiers.add(tierRaw);
    }

    // The same supplier, site country and category twice is one row: keep
    // the more critical tier.
    const key = `${name.toLowerCase()}|${country.toLowerCase()}|${category?.toLowerCase() ?? ''}`;
    const existing = rowsByKey.get(key);
    if (existing) {
      mergedDuplicates += 1;
      if (tier && (!existing.tier || TIER_RANK[tier] < TIER_RANK[existing.tier])) {
        existing.tier = tier;
      }
      continue;
    }

    rowsByKey.set(key, { name, country, category, tier, line: record.line });
    if (!category) rowsWithoutCategory += 1;
  }

  return {
    rows: Array.from(rowsByKey.values()),
    skipped,
    header,
    delimiter,
    mergedDuplicates,
    unrecognisedTiers: Array.from(unrecognisedTiers),
    rowsWithoutCategory,
  };
}
