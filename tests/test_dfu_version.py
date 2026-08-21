import pytest

from anura.dfu import Version, VersionSet


def test_version_from_string():
    assert Version.from_string("1") == Version(1, 0, 0, 0)
    assert Version.from_string("1.2") == Version(1, 2, 0, 0)
    assert Version.from_string("1.2.3") == Version(1, 2, 3, 0)
    assert Version.from_string("1.2.3.4") == Version(1, 2, 3, 4)


def test_version_from_numeric():
    assert Version.from_numeric(0x01020304) == Version(1, 2, 3, 4)


def test_version_as_numeric_round_trips():
    version = Version(1, 2, 3, 4)
    assert Version.from_numeric(version.as_numeric()) == version


def test_version_str():
    assert str(Version.from_string("1.2.3.4")) == "1.2.3.4"
    assert str(Version.from_string("1.2")) == "1.2.0.0"


def test_empty_version_set_contains_all_versions():
    version_set = VersionSet()

    assert Version.from_string("0.0.0") in version_set
    assert Version.from_string("1.0.0") in version_set
    assert Version.from_string("255.255.255") in version_set


def test_single_constraint_greater_than_or_equal():
    version_set = VersionSet(constraints=">=1.2.3")

    assert Version.from_string("1.2.3") in version_set
    assert Version.from_string("1.2.4") in version_set
    assert Version.from_string("2.0.0") in version_set
    assert Version.from_string("1.2.2") not in version_set
    assert Version.from_string("1.1.9") not in version_set


def test_single_constraint_less_than():
    version_set = VersionSet(constraints="<2.0.0")

    assert Version.from_string("1.9.9") in version_set
    assert Version.from_string("1.0.0") in version_set
    assert Version.from_string("2.0.0") not in version_set
    assert Version.from_string("2.1.0") not in version_set


def test_multiple_constraints():
    version_set = VersionSet(constraints=">=1.0.0,<2.0.0,!=1.5.0")

    assert Version.from_string("1.0.0") in version_set
    assert Version.from_string("1.4.9") in version_set
    assert Version.from_string("1.5.1") in version_set
    assert Version.from_string("1.5.0") not in version_set
    assert Version.from_string("0.9.9") not in version_set
    assert Version.from_string("2.0.0") not in version_set


def test_equality_constraint():
    version_set = VersionSet(constraints="==1.2.3")

    assert Version.from_string("1.2.3") in version_set
    assert Version.from_string("1.2.4") not in version_set
    assert Version.from_string("1.2.2") not in version_set


def test_not_equal_constraint():
    version_set = VersionSet(constraints="!=1.2.3")

    assert Version.from_string("1.2.3") not in version_set
    assert Version.from_string("1.2.4") in version_set
    assert Version.from_string("1.2.2") in version_set


def test_intersection():
    version_set = VersionSet(constraints=">=1.0.0").intersection(
        VersionSet(constraints="<2.0.0")
    )

    assert Version.from_string("1.5.0") in version_set
    assert Version.from_string("0.9.9") not in version_set
    assert Version.from_string("2.0.0") not in version_set


def test_version_set_str():
    assert str(VersionSet(constraints=">=1.0.0,<2.0.0")) == ">=1.0.0.0,<2.0.0.0"


def test_invalid_constraint_raises_error():
    with pytest.raises(ValueError, match="Invalid constraint"):
        VersionSet(constraints="invalid_constraint")

    with pytest.raises(ValueError, match="Invalid constraint"):
        VersionSet(constraints="2.0.0")
