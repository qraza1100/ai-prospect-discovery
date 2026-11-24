from __future__ import annotations

import html
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlsplit

import tldextract

_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())
_TAGS = re.compile(r"<[^>]+>")
_NON_WORD = re.compile(r"[^\w\s'-]", re.UNICODE)
_HANDLE_JUNK = re.compile(r"[._\-]")
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "eng", "er"}
_GENERIC_NAMES = {
    "home",
    "homepage",
    "welcome",
    "about",
    "about us",
    "contact",
    "contact us",
    "log in",
    "login",
    "sign up",
    "page not found",
    "instagram",
    "facebook",
    "linkedin",
    "youtube",
    "threads",
    "x",
    "twitter",
    "official site",
    "official website",
    "events",
    "blog",
    "news",
    "services",
    "shop",
    "menu",
    "team",
    "careers",
    "faq",
    "gallery",
    "portfolio",
    "our story",
}


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(_TAGS.sub(" ", value))
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split())


def fold(value: str) -> str:
    """Case- and accent-insensitive form used for matching."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.split())


def strip_symbols(value: str) -> str:
    kept = []
    for ch in value:
        category = unicodedata.category(ch)
        if category.startswith(("L", "N", "M")) or ch in " &'.-,":
            kept.append(ch)
        else:
            kept.append(" ")
    return " ".join("".join(kept).split()).strip(" -,.")


def clean_display_name(value: str | None) -> str | None:
    if not value:
        return None
    name = strip_symbols(clean_text(value))
    if not name or len(name) > 80 or not any(ch.isalpha() for ch in name):
        return None
    if fold(name) in _GENERIC_NAMES:
        return None
    return name


def name_tokens(name: str) -> list[str]:
    tokens = _NON_WORD.sub(" ", fold(name)).replace("-", " ").split()
    return [t for t in tokens if t not in _HONORIFICS]


def name_key(name: str | None) -> str:
    return " ".join(sorted(name_tokens(name or "")))


def name_similarity(a: str | None, b: str | None) -> float:
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    return SequenceMatcher(None, ka, kb).ratio()


def normalize_handle(handle: str | None) -> str:
    if not handle:
        return ""
    return _HANDLE_JUNK.sub("", handle.lstrip("@").casefold())


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def registrable_domain(url_or_host: str) -> str:
    host = host_of(url_or_host) if "://" in url_or_host else url_or_host.casefold()
    parts = _EXTRACT(host)
    if parts.domain and parts.suffix:
        return f"{parts.domain}.{parts.suffix}"
    return host


def contains_term(text_folded: str, term: str) -> bool:
    """Whole-word match on folded text. Short acronyms (e.g. "DC") must match case-sensitively."""
    term = term.strip()
    if not term:
        return False
    if len(term) <= 3 and term.isupper():
        return False
    pattern = r"(?<!\w)" + re.escape(fold(term)) + r"(?!\w)"
    return re.search(pattern, text_folded) is not None


def contains_acronym(raw_text: str, term: str) -> bool:
    term = term.strip()
    if not (term and len(term) <= 3 and term.isupper()):
        return False
    return re.search(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])", raw_text) is not None


def matches_any(raw_text: str, terms: list[str]) -> list[str]:
    folded = fold(raw_text)
    hits: list[str] = []
    for term in terms:
        if term and (contains_term(folded, term) or contains_acronym(raw_text, term)):
            hits.append(term)
    return hits
