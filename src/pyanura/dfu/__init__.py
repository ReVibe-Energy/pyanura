"""Device Firmware Update (DFU) bundles.

A firmware bundle is a zip archive with a ``meta.json`` manifest at the zip
root describing one flashable image per firmware component ("app", "net"),
along with target version/build metadata and version constraints on the
firmware already installed on the device. :func:`open_bundle` exposes the
manifest as written, as a type per manifest version; :class:`Version` and
:class:`VersionSet` parse the version strings and constraints it holds.
"""

from .bundle import (
    BundleArchive,
    Manifest,
    ManifestV1,
    ManifestV1Image,
    open_bundle,
)
from .version import Version, VersionSet

__all__ = [
    "BundleArchive",
    "Manifest",
    "ManifestV1",
    "ManifestV1Image",
    "Version",
    "VersionSet",
    "open_bundle",
]
