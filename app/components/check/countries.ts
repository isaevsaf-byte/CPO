// Country names as people actually type them, mapped to one spelling per
// country. The spellings match data/country_risk.json (so "Korea, Republic
// of" still picks up South Korea's standing floor) and the board itself
// ("USA"). Anything not listed is kept as typed, title-cased when it arrived
// all in one case, so "germany" and "Germany" still land in the same row.

const ALIASES: Record<string, string> = {
  // Countries with a standing floor in data/country_risk.json
  china: 'China', cn: 'China', prc: 'China', 'p r c': 'China', 'peoples republic of china': 'China',
  'mainland china': 'China', 'china mainland': 'China',
  taiwan: 'Taiwan', tw: 'Taiwan', roc: 'Taiwan', 'republic of china': 'Taiwan', 'chinese taipei': 'Taiwan',
  'taiwan roc': 'Taiwan', 'taiwan province of china': 'Taiwan',
  'south korea': 'South Korea', korea: 'South Korea', kr: 'South Korea', rok: 'South Korea',
  'republic of korea': 'South Korea', 'korea republic of': 'South Korea', 'korea south': 'South Korea',
  's korea': 'South Korea',
  'north korea': 'North Korea', kp: 'North Korea', dprk: 'North Korea',
  'democratic peoples republic of korea': 'North Korea', 'korea dpr': 'North Korea', 'korea north': 'North Korea',
  russia: 'Russia', ru: 'Russia', 'russian federation': 'Russia',
  ukraine: 'Ukraine', ua: 'Ukraine',
  iran: 'Iran', ir: 'Iran', 'islamic republic of iran': 'Iran', 'iran islamic republic of': 'Iran',
  syria: 'Syria', sy: 'Syria', 'syrian arab republic': 'Syria',
  israel: 'Israel', il: 'Israel',
  palestine: 'Palestine', ps: 'Palestine', 'state of palestine': 'Palestine',
  'palestinian territories': 'Palestine', 'occupied palestinian territory': 'Palestine',
  lebanon: 'Lebanon', lb: 'Lebanon',
  yemen: 'Yemen', ye: 'Yemen',
  sudan: 'Sudan', sd: 'Sudan',
  myanmar: 'Myanmar', mm: 'Myanmar', burma: 'Myanmar',
  india: 'India', in: 'India',
  'south africa': 'South Africa', za: 'South Africa', rsa: 'South Africa',
  finland: 'Finland', fi: 'Finland', suomi: 'Finland',

  // Common supplier countries
  usa: 'USA', us: 'USA', 'u s': 'USA', 'u s a': 'USA', 'united states': 'USA',
  'united states of america': 'USA', america: 'USA',
  'united kingdom': 'United Kingdom', uk: 'United Kingdom', 'u k': 'United Kingdom', gb: 'United Kingdom',
  'great britain': 'United Kingdom', britain: 'United Kingdom', england: 'United Kingdom',
  scotland: 'United Kingdom', wales: 'United Kingdom', 'northern ireland': 'United Kingdom',
  germany: 'Germany', de: 'Germany', deutschland: 'Germany',
  netherlands: 'Netherlands', nl: 'Netherlands', 'the netherlands': 'Netherlands', holland: 'Netherlands',
  japan: 'Japan', jp: 'Japan',
  vietnam: 'Vietnam', vn: 'Vietnam', 'viet nam': 'Vietnam',
  turkey: 'Turkey', tr: 'Turkey', turkiye: 'Turkey',
  'united arab emirates': 'United Arab Emirates', uae: 'United Arab Emirates', 'u a e': 'United Arab Emirates',
  ae: 'United Arab Emirates',
  czechia: 'Czechia', 'czech republic': 'Czechia', cz: 'Czechia',
  'hong kong': 'Hong Kong', hk: 'Hong Kong',
  switzerland: 'Switzerland', ch: 'Switzerland',
  france: 'France', fr: 'France',
  italy: 'Italy', it: 'Italy',
  spain: 'Spain', es: 'Spain',
  portugal: 'Portugal', pt: 'Portugal',
  poland: 'Poland', pl: 'Poland',
  sweden: 'Sweden', se: 'Sweden',
  norway: 'Norway', no: 'Norway',
  denmark: 'Denmark', dk: 'Denmark',
  austria: 'Austria', at: 'Austria',
  belgium: 'Belgium', be: 'Belgium',
  ireland: 'Ireland', ie: 'Ireland',
  hungary: 'Hungary', hu: 'Hungary',
  romania: 'Romania', ro: 'Romania',
  slovakia: 'Slovakia', sk: 'Slovakia',
  mexico: 'Mexico', mx: 'Mexico',
  brazil: 'Brazil', br: 'Brazil',
  canada: 'Canada', ca: 'Canada',
  australia: 'Australia', au: 'Australia',
  indonesia: 'Indonesia', id: 'Indonesia',
  malaysia: 'Malaysia', my: 'Malaysia',
  thailand: 'Thailand', th: 'Thailand',
  philippines: 'Philippines', ph: 'Philippines',
  singapore: 'Singapore', sg: 'Singapore',
  bangladesh: 'Bangladesh', bd: 'Bangladesh',
  pakistan: 'Pakistan', pk: 'Pakistan',
  'sri lanka': 'Sri Lanka', lk: 'Sri Lanka',
  cambodia: 'Cambodia', kh: 'Cambodia',
  egypt: 'Egypt', eg: 'Egypt',
  morocco: 'Morocco', ma: 'Morocco',
  'saudi arabia': 'Saudi Arabia', sa: 'Saudi Arabia',
  argentina: 'Argentina', ar: 'Argentina',
  chile: 'Chile', cl: 'Chile',
  colombia: 'Colombia', co: 'Colombia',
  'new zealand': 'New Zealand', nz: 'New Zealand',
};

function aliasKey(input: string): string {
  return input
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function titleCase(input: string): string {
  return input
    .toLowerCase()
    .replace(/(^|[\s\-(])([a-z])/g, (_match, lead: string, letter: string) => lead + letter.toUpperCase());
}

export function normaliseCountry(raw: string): string {
  const trimmed = raw.trim().replace(/\s+/g, ' ');
  if (!trimmed) return '';
  const known = ALIASES[aliasKey(trimmed)];
  if (known) return known;
  const oneCase = trimmed === trimmed.toLowerCase() || trimmed === trimmed.toUpperCase();
  // Short all-caps entries are more likely codes or acronyms than names.
  if (oneCase && !(trimmed === trimmed.toUpperCase() && trimmed.length <= 3)) {
    return titleCase(trimmed);
  }
  return trimmed;
}

/** Plain code-unit comparison, case-folded. localeCompare can order the same
 *  strings differently on the build server and in a browser, and these lists
 *  are rendered in both. */
export function compareText(a: string, b: string): number {
  const x = a.toLowerCase();
  const y = b.toLowerCase();
  return x < y ? -1 : x > y ? 1 : 0;
}

/** One spelling per country, for type-ahead suggestions. */
export const COUNTRY_SUGGESTIONS: string[] = Array.from(new Set(Object.values(ALIASES))).sort(compareText);
