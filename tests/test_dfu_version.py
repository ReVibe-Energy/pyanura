import pytest

from pyanura.dfu import Version


def test_version_from_string():
    assert Version.from_string("1") == Version(1, 0, 0, 0)
    assert Version.from_string("1.2") == Version(1, 2, 0, 0)
    assert Version.from_string("1.2.3") == Version(1, 2, 3, 0)
    assert Version.from_string("1.2.3.4") == Version(1, 2, 3, 4)
    assert Version.from_string("255.255.255.255") == Version(255, 255, 255, 255)


@pytest.mark.parametrize(
    "version_str",
    ["", "1.2.3.4.5", "256.0.0", "1.-2", "26.5.0+99", " 1.2", "1..2", "1.2.", "a.b"],
)
def test_version_from_string_rejects_invalid(version_str):
    with pytest.raises(ValueError, match="Invalid version"):
        Version.from_string(version_str)


def test_version_from_numeric():
    assert Version.from_numeric(0x01020304) == Version(1, 2, 3, 4)


def test_version_as_numeric_round_trips():
    version = Version(1, 2, 3, 4)
    assert Version.from_numeric(version.as_numeric()) == version


def test_version_str():
    assert str(Version.from_string("1.2.3.4")) == "1.2.3.4"
    assert str(Version.from_string("1.2")) == "1.2.0.0"


def v(version_str: str) -> Version:
    return Version.from_string(version_str)


@pytest.mark.parametrize("spec", ["", " "])
def test_empty_spec_matches_all_versions(spec):
    assert v("0.0.0").matches(spec)
    assert v("255.255.255").matches(spec)


def test_greater_than_or_equal():
    assert v("1.2.3").matches(">=1.2.3")
    assert v("2.0.0").matches(">=1.2.3")
    assert not v("1.2.2").matches(">=1.2.3")


def test_greater_than():
    assert v("1.2.4").matches(">1.2.3")
    assert not v("1.2.3").matches(">1.2.3")


def test_less_than():
    assert v("1.9.9").matches("<2.0.0")
    assert not v("2.0.0").matches("<2.0.0")


def test_less_than_or_equal():
    assert v("2.0.0").matches("<=2.0.0")
    assert not v("2.0.1").matches("<=2.0.0")


def test_equal():
    assert v("1.2.3").matches("==1.2.3")
    assert not v("1.2.4").matches("==1.2.3")


def test_not_equal():
    assert v("1.2.4").matches("!=1.2.3")
    assert not v("1.2.3").matches("!=1.2.3")


def test_all_comparisons_must_hold():
    spec = ">=1.0.0,<2.0.0,!=1.5.0"

    assert v("1.0.0").matches(spec)
    assert v("1.5.1").matches(spec)
    assert not v("1.5.0").matches(spec)
    assert not v("0.9.9").matches(spec)
    assert not v("2.0.0").matches(spec)


def test_whitespace_around_comparisons_is_ignored():
    assert v("1.5.0").matches(" >= 1.0 , < 2.0 ")
    assert not v("2.0.0").matches(" >= 1.0 , < 2.0 ")


@pytest.mark.parametrize(
    "spec", ["invalid", "2.0.0", "~1.0", ">=1.0,", ">=", ">=1.0.0.0.0", "<256"]
)
def test_malformed_spec_raises(spec):
    with pytest.raises(ValueError, match="Invalid version match"):
        v("1.0").matches(spec)
