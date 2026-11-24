"""Checking a bundle against the firmware a device runs.

These answer the questions every update flow asks, without doing any I/O:
is this image already running, and are its dependencies met? Reading the
firmware info from the device, and what to do with the answers, is left to
the caller.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .bundle import InstalledImage, ManifestV1Image
from .version import Version, _parse_spec

__all__ = [
    "UnmetDependency",
    "running_images",
    "unmet_dependencies",
]


class _FirmwareInfo(Protocol):
    """What the AVSS and transceiver firmware info responses share."""

    @property
    def app_version(self) -> int: ...
    @property
    def app_build_version(self) -> str: ...
    @property
    def net_version(self) -> int: ...
    @property
    def net_build_version(self) -> str: ...


def running_images(info: _FirmwareInfo) -> dict[str, InstalledImage]:
    """The images a device runs, by component, from its firmware info.

    `info` is the response to Get Firmware Info, from a node or from a
    transceiver. A component the device has nothing to report for is left
    out, such as a "net" image too old to tell the app its version.
    """
    images = {}
    for name, version, build in (
        ("app", info.app_version, info.app_build_version),
        ("net", info.net_version, info.net_build_version),
    ):
        if version == 0 and not build:
            continue
        images[name] = InstalledImage(Version.from_numeric(version), build or None)
    return images


@dataclass(frozen=True)
class UnmetDependency:
    """A version match specification the running firmware does not meet."""

    # The component whose image has the dependency.
    image: str
    # The component it depends on, and the specification it must meet.
    component: str
    spec: str
    # The version the device runs for that component, or None if unknown.
    running: Version | None

    def __str__(self) -> str:
        running = "unknown" if self.running is None else str(self.running)
        return (
            f"'{self.image}' requires '{self.component}' {self.spec},"
            f" running version is {running}"
        )


def unmet_dependencies(
    images: Iterable[tuple[str, ManifestV1Image]],
    running: Mapping[str, InstalledImage],
) -> list[UnmetDependency]:
    """The dependencies of `images` that the running firmware does not meet.

    `images` are (component, image) pairs in the order they will be applied,
    such as `ManifestV1.images`. `running` is what the device runs now, by
    component, such as `running_images` gives.

    An image the device already runs is left out, since it will not be
    applied. So is a dependency on a component that an earlier image in
    `images` installs, since that image replaces what runs now.
    (`read_bundle` has already checked that such a dependency holds.)

    A dependency on a component the device does not report counts as unmet,
    unless its specification is empty.
    An empty list means the images can be applied.
    """
    replaced: set[str] = set()
    unmet = []
    for name, image in images:
        if image.matches(running.get(name)):
            continue
        for component, spec in image.dependencies:
            if component in replaced:
                continue
            current = running.get(component)
            version = current.version if current is not None else None
            # An empty specification asks for nothing, so it is met even
            # when the version is unknown.
            if version is None and not _parse_spec(spec):
                continue
            if version is None or not version.matches(spec):
                unmet.append(UnmetDependency(name, component, spec, version))
        replaced.add(name)
    return unmet
