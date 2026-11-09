import re
from collections import namedtuple
from typing import Self

# Inheriting from namedtuple allows us to compare Version instances directly
# using the standard comparison operators (==, !=, <, <=, >, >=).
_VersionTuple = namedtuple(
    "Version",
    ("major", "minor", "patch", "tweak"),
    defaults=(0, 0, 0),  # defaults for minor, patch and tweak
)

_PART = re.compile(r"[0-9]+")

# NB! The order matters: "<=" must be tried before "<", and ">=" before ">".
_OPERATORS = ("==", "!=", "<=", ">=", "<", ">")


class Version(_VersionTuple):
    """
    Represents a version number with major, minor, patch and version tweak
    components, each in the range 0-255 as in the bit-packed format that
    Anura devices use.
    """

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}.{self.tweak}"

    @classmethod
    def from_string(cls, version_str: str) -> Self:
        """
        Parse a version string in the format "major[.minor[.patch[.tweak]]]".

        Omitted components are 0.

        Raises:
            ValueError: If the string has more than four components, or one
                that is not a decimal number in the range 0-255.
        """
        parts = version_str.split(".")
        if len(parts) > 4:
            raise ValueError(f"Invalid version {version_str!r}: too many components")
        numbers = []
        for part in parts:
            if not _PART.fullmatch(part) or int(part) > 255:
                raise ValueError(
                    f"Invalid version {version_str!r}: components must be"
                    " numbers in the range 0-255"
                )
            numbers.append(int(part))
        return cls(*numbers)

    @classmethod
    def from_numeric(cls, version_numeric: int) -> Self:
        """
        Parse a version number in the bit-packed format that Anura devices use.
        """
        major = (version_numeric >> 24) & 0xFF
        minor = (version_numeric >> 16) & 0xFF
        patch = (version_numeric >> 8) & 0xFF
        tweak = version_numeric & 0xFF

        return cls(major, minor, patch, tweak)

    def as_numeric(self) -> int:
        """
        Convert the version to a bit-packed integer format.
        """
        return (self.major << 24) | (self.minor << 16) | (self.patch << 8) | self.tweak

    def matches(self, spec: str) -> bool:
        """
        Whether this version satisfies a version match specification.

        A specification, as held by a bundle manifest's ``*_version_match``
        keys, is a comma-separated list of comparisons such as
        ``">=26.4.0,<27"``, all of which must hold. The operators are ``==``,
        ``!=``, ``<``, ``<=``, ``>`` and ``>=``; whitespace around a
        comparison or its version is ignored. An empty specification matches
        every version.

        Raises:
            ValueError: If the specification is malformed.
        """
        return all(_compare(self, op, version) for op, version in _parse_spec(spec))


def _parse_spec(spec: str) -> list[tuple[str, Version]]:
    """Parse a version match specification into (operator, version) pairs.

    Raises:
        ValueError: If the specification is malformed.
    """
    if not spec.strip():
        return []
    comparisons = []
    for comparison in spec.split(","):
        comparison = comparison.strip()
        op = next((op for op in _OPERATORS if comparison.startswith(op)), None)
        if op is None:
            raise ValueError(
                f"Invalid version match {spec!r}: {comparison!r} has no operator"
            )
        try:
            version = Version.from_string(comparison[len(op) :].strip())
        except ValueError as e:
            raise ValueError(f"Invalid version match {spec!r}: {e}") from None
        comparisons.append((op, version))
    return comparisons


def _compare(version: Version, op: str, other: Version) -> bool:
    match op:
        case "==":
            return version == other
        case "!=":
            return version != other
        case "<":
            return version < other
        case "<=":
            return version <= other
        case ">":
            return version > other
        case ">=":
            return version >= other
    raise AssertionError(op)
