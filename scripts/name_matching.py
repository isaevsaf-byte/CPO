"""Whole-word supplier-name matching shared by screening.py and ransom.py.

The rules are the harvester's own (supplier_search_terms and
supplier_terms_hit in update_intel.py): whole-word matching, a short-name
guard, aliases supplied by the caller. update_intel.py cannot be imported
from here because it loads the watchlist at import time, so callers pass
the terms in as ``name_terms: dict[str, list[str]]``.

Two differences from supplier_terms_hit, both from data seen on the live
feeds:

- Every run of punctuation or whitespace is folded to one space on both
  sides before comparing. The DHS UFLPA page writes "Xinjiang&nbsp;Goens
  Energy Technology", so a pattern with a plain space never saw it;
  RansomLook titles victims by domain ("camorim.com.br", "bakemyday.se"),
  which folds to "CAMORIM COM BR"; and the alias "SWM (MATIV)" ends in ")",
  where a regex \\b after the bracket can never match.
- Terms shorter than five characters are never screened on their own, even
  when they are a supplier's only name. On the Consolidated Screening List
  "GPI" is an alias of the Prokhorov General Physics Institute and "ITC"
  names an SDN consultancy in Cyprus and an Iran Air aircraft (EP-ITC).
  supplier_search_terms keeps such names as a last resort so news scanning
  has something to search for; for a sanctions or leak-site flag a wrong
  match is worse than none, so a supplier left with no long term is
  reported as unscreened instead.
"""

import re

MIN_TERM_LEN = 5

_SEPARATORS = re.compile(r"[\W_]+")


def normalize(text) -> str:
    """Uppercase text with every run of non-alphanumerics folded to one space.

    The result is padded with a space at each end, so a term is present as a
    whole word exactly when " TERM " is a substring of it.
    """
    if not text:
        return " "
    return " " + _SEPARATORS.sub(" ", str(text).upper()).strip() + " "


def usable_terms(terms, min_len: int = MIN_TERM_LEN) -> list:
    """Normalized, de-duplicated terms long enough to screen on their own."""
    out = []
    for term in terms or []:
        folded = normalize(term).strip()
        if len(folded) >= min_len and folded not in out:
            out.append(folded)
    return out


def first_match(haystack: str, terms) -> str | None:
    """The first term present as a whole word in an already-normalized haystack."""
    for term in terms:
        if f" {term} " in haystack:
            return term
    return None


def covering_terms(terms) -> list:
    """The fewest terms whose substring searches return everything the full set would.

    A substring search for "MATIV" already returns every post containing
    "SWM MATIV", so the longer term adds nothing but a request.
    """
    chosen = []
    for term in sorted(set(terms), key=len):
        if not any(f" {c} " in f" {term} " for c in chosen):
            chosen.append(term)
    return chosen
