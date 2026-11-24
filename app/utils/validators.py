from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

import phonenumbers
from email_validator import EmailNotValidError, validate_email

_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,24}(?![\w-])"
)
_WHATSAPP_PATTERN = re.compile(
    r"(?:https?://)?(?:wa\.me/|(?:api\.|web\.)?whatsapp\.com/send/?\?(?:[^\s\"'<>]*&)?phone=)\+?(\d{7,15})",
    re.IGNORECASE,
)
_PHONE_CONTEXT = re.compile(
    r"(?:phone|tel|call|mobile|mob|contact|whatsapp|cell|☎|\U0001f4de|\U0001f4f1)",
    re.IGNORECASE,
)
_PLACEHOLDER_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "domain.com",
    "email.com",
    "yourdomain.com",
    "yoursite.com",
    "company.com",
    "test.com",
    "sentry.io",
    "wixpress.com",
    "sentry-next.wixpress.com",
}
_PLACEHOLDER_LOCALS = {"name", "your.name", "yourname", "user", "username", "email", "someone"}
_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".ico")


def normalize_email(candidate: str) -> str | None:
    value = unquote(candidate).strip().strip(".,;:()[]<>\"'")
    if value.lower().endswith(_ASSET_SUFFIXES):
        return None
    try:
        result = validate_email(value, check_deliverability=False)
    except EmailNotValidError:
        return None
    email = result.normalized.lower()
    local, _, domain = email.partition("@")
    if domain in _PLACEHOLDER_DOMAINS or local in _PLACEHOLDER_LOCALS:
        return None
    return email


def extract_emails(text: str) -> set[str]:
    found: set[str] = set()
    for match in _EMAIL_PATTERN.finditer(text or ""):
        email = normalize_email(match.group(0))
        if email:
            found.add(email)
    return found


def email_from_mailto(href: str) -> str | None:
    if not href.lower().startswith("mailto:"):
        return None
    address = href[7:].split("?", 1)[0].split(",", 1)[0]
    return normalize_email(address)


def normalize_phone(raw: str, region: str | None = None) -> str | None:
    """Return the number in E.164 form, or None unless it is a valid, dialable number."""
    try:
        parsed = phonenumbers.parse(raw, (region or "").upper() or None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def phone_from_tel(href: str, region: str | None = None) -> str | None:
    if not href.lower().startswith("tel:"):
        return None
    return normalize_phone(unquote(href[4:]).split(";", 1)[0], region)


def extract_phones(text: str, region: str | None = None, require_context: bool = True) -> set[str]:
    """Find valid phone numbers. With require_context, a number counts only when a word such as
    "phone" or "call" appears within 40 characters before it, or it is written in +E.164 form."""
    found: set[str] = set()
    if not text:
        return found
    matcher = phonenumbers.PhoneNumberMatcher(
        text, (region or "").upper() or None, leniency=phonenumbers.Leniency.VALID
    )
    for match in matcher:
        if require_context:
            window = text[max(0, match.start - 40) : match.start]
            if not (match.raw_string.lstrip().startswith("+") or _PHONE_CONTEXT.search(window)):
                continue
        found.add(phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164))
    return found


def whatsapp_link(e164: str) -> str:
    return f"https://wa.me/{e164.lstrip('+')}"


def whatsapp_number_from_url(url: str) -> str | None:
    """Return the E.164 number behind a click-to-chat URL, or None if it is not a valid number."""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = (parts.hostname or "").lower()
    digits: str | None = None
    if host == "wa.me":
        digits = parts.path.strip("/").split("/", 1)[0]
    elif host in {"api.whatsapp.com", "web.whatsapp.com", "whatsapp.com", "www.whatsapp.com"} and (
        parts.path.rstrip("/") == "/send"
    ):
        digits = (parse_qs(parts.query).get("phone") or [""])[0]
    if not digits or not digits.lstrip("+").isdigit():
        return None
    return normalize_phone("+" + digits.lstrip("+"))


def extract_whatsapp_numbers(text: str) -> set[str]:
    found: set[str] = set()
    for match in _WHATSAPP_PATTERN.finditer(text or ""):
        e164 = normalize_phone("+" + match.group(1))
        if e164:
            found.add(e164)
    return found
