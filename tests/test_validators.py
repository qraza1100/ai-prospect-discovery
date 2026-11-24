import pytest

from app.utils.normalizers import matches_any, name_similarity, normalize_handle, registrable_domain
from app.utils.validators import (
    email_from_mailto,
    extract_emails,
    extract_phones,
    extract_whatsapp_numbers,
    normalize_email,
    normalize_phone,
    phone_from_tel,
    whatsapp_link,
    whatsapp_number_from_url,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Coach.Team@GmailX.com", "coach.team@gmailx.com"),
        ("hello@example.com", None),
        ("name@company.com", None),
        ("logo@2x.png", None),
        ("not-an-email", None),
        ("a@b", None),
    ],
)
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


def test_extract_emails_from_bio_text():
    text = "Bookings 📧 bookings@kickoff-dc.org | backup: Team.Manager@kickoff-dc.org."
    assert extract_emails(text) == {"bookings@kickoff-dc.org", "team.manager@kickoff-dc.org"}


def test_extract_emails_ignores_asset_names_and_placeholders():
    assert extract_emails("sprite icon@2x.png and your.name@domain.com") == set()


def test_mailto_strips_query():
    assert email_from_mailto("mailto:Info@Kickoff-DC.org?subject=Hi") == "info@kickoff-dc.org"
    assert email_from_mailto("https://kickoff-dc.org") is None


def test_normalize_phone_requires_valid_number():
    assert normalize_phone("+1 202-456-1111") == "+12024561111"
    assert normalize_phone("(202) 456-1111", "us") == "+12024561111"
    assert normalize_phone("12345") is None
    assert normalize_phone("+1 000-000-0000") is None


def test_extract_phones_needs_context_or_plus_prefix():
    assert extract_phones("Call us: (202) 456-1111", "us") == {"+12024561111"}
    assert extract_phones("Followers 2024561111", "us") == set()
    assert extract_phones("Reach +44 20 7946 0958 anytime", None) == {"+442079460958"}


def test_tel_link():
    assert phone_from_tel("tel:+971-4-330-0000") == "+97143300000"
    assert phone_from_tel("mailto:x@y.com") is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://wa.me/971501234567", "+971501234567"),
        ("https://api.whatsapp.com/send?phone=919825012345&text=hi", "+919825012345"),
        ("https://chat.whatsapp.com/AbCdEf", None),
        ("https://wa.me/123", None),
        ("https://wa.me/abc", None),
    ],
)
def test_whatsapp_number_from_url(url, expected):
    assert whatsapp_number_from_url(url) == expected


def test_extract_whatsapp_numbers_from_text():
    text = "Order on WhatsApp wa.me/971501234567 or api.whatsapp.com/send?phone=919825012345"
    assert extract_whatsapp_numbers(text) == {"+971501234567", "+919825012345"}
    assert whatsapp_link("+971501234567") == "https://wa.me/971501234567"


def test_name_similarity_is_order_and_accent_insensitive():
    assert name_similarity("José Álvarez", "alvarez jose") == 1.0
    assert name_similarity("Dr. Jane Roe", "Jane Roe") == 1.0
    assert name_similarity("Jane Roe", "John Smith") < 0.5
    assert name_similarity(None, "Jane Roe") == 0.0


def test_handle_normalization():
    assert normalize_handle("@Jane.Roe_DC") == "janeroedc"


def test_registrable_domain_uses_public_suffixes():
    assert registrable_domain("https://shop.kickoff.co.uk/about") == "kickoff.co.uk"
    assert registrable_domain("https://www.kickoff-dc.org/") == "kickoff-dc.org"


def test_matches_any_handles_acronyms_case_sensitively():
    assert matches_any("Proud DC native", ["DC"]) == ["DC"]
    assert matches_any("dc comics collector", ["DC"]) == []
    assert matches_any("Living in Washington, D.C. area", ["Washington, D.C."]) == ["Washington, D.C."]
    assert matches_any("footballer", ["football"]) == []
