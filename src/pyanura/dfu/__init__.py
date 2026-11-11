"""Device Firmware Update (DFU) bundles.

A firmware bundle is a zip archive with a ``meta.json`` manifest at the zip
root describing one flashable image per firmware component ("app", "net"),
along with target version/build metadata and version constraints on the
firmware already installed on the device. :func:`read_bundle` reads the
images the manifest names and exposes the manifest as written, as a type per
manifest version; :class:`Version` parses the version strings it holds, and
:meth:`Version.matches` tests a version against its version match
specifications. :meth:`ManifestV1Image.matches` and
:func:`unmet_dependencies` check a bundle against the images a device runs,
which :func:`running_images` reads from its firmware info.
"""

from .bundle import (
    Bundle,
    InstalledImage,
    Manifest,
    ManifestV1,
    ManifestV1Image,
    read_bundle,
)
from .checks import UnmetDependency, running_images, unmet_dependencies
from .version import Version

__all__ = [
    "Bundle",
    "InstalledImage",
    "Manifest",
    "ManifestV1",
    "ManifestV1Image",
    "UnmetDependency",
    "Version",
    "read_bundle",
    "running_images",
    "unmet_dependencies",
]
