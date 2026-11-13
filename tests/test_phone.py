import pytest

from callgate.phone import PhoneError, digest, mask, normalize


@pytest.mark.parametrize(
    ("raw", "out"),
    [
        ("+15555550142", "+15555550142"),
        ("(555) 555-0142", "+15555550142"),
        ("1 555 555 0142", "+15555550142"),
        ("0044 20 7946 0000", "+442079460000"),
    ],
)
def test_normalize(raw, out):
    assert normalize(raw) == out


@pytest.mark.parametrize("raw", ["555-0142", "+0123456789", "call me", "", "+1555"])
def test_normalize_rejects(raw):
    with pytest.raises(PhoneError):
        normalize(raw)


def test_no_default_country_requires_plus():
    with pytest.raises(PhoneError):
        normalize("5555550142", default_country=None)


def test_mask_and_digest():
    assert mask("+15555550142") == "+1***42"
    assert digest("+15555550142", b"k") == digest("+15555550142", b"k")
    assert digest("+15555550142", b"k") != digest("+15555550142", b"other")
