from collections import namedtuple
from typing import Self

# Inheriting from namedtuple allows us to compare Version instances directly
# using the standard comparison operators (==, !=, <, <=, >, >=).
_VersionTuple = namedtuple(
    "Version",
    ("major", "minor", "patch", "tweak"),
    defaults=(0, 0, 0),  # defaults for minor, patch and tweak
)


class Version(_VersionTuple):
    """
    Represents a version number with major, minor, patch and version tweak
    components.
    """

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}.{self.tweak}"

    @classmethod
    def from_string(cls, version_str: str) -> Self:
        """
        Parse a version string in the format "major.minor.patch.tweak".
        """
        parts = version_str.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        tweak = int(parts[3]) if len(parts) > 3 else 0

        return cls(major, minor, patch, tweak)

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


class VersionSet:
    """
    Represents a set of versions. A VersionSet can be used for checking if a
    Version instance is in the set by doing `Version() in VersionSet()`.

    A VersionSet is defined by a set of constraints. For a `Version` to be
    considered within a `VersionSet`, it must fulfill all constraints. A
    VersionSet without constraints, `VersionSet()` is the set of all possible
    versions.

    The constraints are represented by a list of tuples, where each tuple
    contains an operator and a Version instance.

    To initialize a VersionSet, provide a string of constraints in the format
    ">=1.2.3,<2.0.0". The operators can be one of the following: >=, >, <=, <,
    ==, !=.

    Example:
    >>> version_set = VersionSet(constraints=">=1.0.0,<2.0.0")
    >>> version = Version.from_string("1.5.0")
    >>> version in version_set
    True
    """

    def __init__(self, *, constraints: str | None = None):
        """
        Parse a version constraint string in the format ">=1.2.3,<2.0.0".
        """
        self.constraints: list[tuple[str, Version]] = []
        if not constraints:
            return
        for constraint in constraints.split(","):
            # NB! The order of the operators matters. Strings starting with "<="
            # also start with "<". Therefore, check for "<=" before "<".
            for op in ("==", "!=", "<=", ">=", "<", ">"):
                if constraint.startswith(op):
                    self.constraints.append(
                        (op, Version.from_string(constraint[len(op) :]))
                    )
                    break
            else:
                raise ValueError(f"Invalid constraint: '{constraint}'")

    def __str__(self) -> str:
        return ",".join(f"{op}{version}" for op, version in self.constraints)

    def __contains__(self, version: Version) -> bool:
        """
        Check if a Version instance satisfies the constraints in the VersionSet.
        """
        for operator, constraint_version in self.constraints:
            if operator == ">=" and not (version >= constraint_version):
                return False
            elif operator == ">" and not (version > constraint_version):
                return False
            elif operator == "<=" and not (version <= constraint_version):
                return False
            elif operator == "<" and not (version < constraint_version):
                return False
            elif operator == "==" and not (version == constraint_version):
                return False
            elif operator == "!=" and not (version != constraint_version):
                return False
        return True

    def intersection(self, other: "VersionSet") -> "VersionSet":
        """
        Compute the intersection between this VersionSet and another VersionSet.

        The intersection of two VersionSets contains all versions that satisfy
        both sets of constraints.

        Args:
            other: Another VersionSet instance to intersect with.

        Returns:
            A new VersionSet representing the intersection of both sets.
        """
        result = VersionSet()
        result.constraints = self.constraints + other.constraints
        return result
