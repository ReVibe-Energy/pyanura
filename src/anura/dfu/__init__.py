"""Device Firmware Update (DFU) bundles.

A firmware bundle is a zip archive with a ``meta.json`` manifest at the zip
root describing one flashable image per firmware component ("app", "net"),
along with target version/build metadata and version constraints on the
firmware already installed on the device.
"""

from .bundle import (
    Bundle,
    Component,
    Dependency,
    InstalledComponent,
    UnmetDependency,
    parse_bundle,
    total_dependencies,
    unmet_dependencies,
)
from .version import Version, VersionSet

__all__ = [
    "Bundle",
    "Component",
    "Dependency",
    "InstalledComponent",
    "UnmetDependency",
    "Version",
    "VersionSet",
    "parse_bundle",
    "total_dependencies",
    "unmet_dependencies",
]
