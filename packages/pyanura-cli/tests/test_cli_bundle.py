import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
from pyanura_cli.bundle import apply_bundle

from pyanura.dfu import Bundle, InstalledImage, ManifestV1, ManifestV1Image, Version


class FakeDevice:
    """A device that runs what it was last sent, unless told to revert.

    It is both the target and the session. `log` records what the update
    did to it.
    """

    def __init__(self, revert: bool = False, **versions: str) -> None:
        self.versions = {
            name: Version.from_string(version) for name, version in versions.items()
        }
        self.revert = revert
        self.log: list[str] = []
        self._uploaded = b""

    @asynccontextmanager
    async def connect(self) -> AsyncIterator["FakeDevice"]:
        yield self

    async def wait_reboot(self) -> None:
        self.log.append("reboot")

    async def running(self) -> dict[str, InstalledImage]:
        return {
            name: InstalledImage(version) for name, version in self.versions.items()
        }

    async def upload(self, contents: bytes, progress: Callable[[int], None]) -> None:
        self.log.append(f"upload {contents.decode()}")
        progress(len(contents))
        self._uploaded = contents

    async def apply(self) -> None:
        name, version = self._uploaded.decode().split()
        self.log.append(f"apply {name}")
        if not self.revert:
            self.versions[name] = Version.from_string(version)

    async def confirm(self) -> None:
        self.log.append("confirm")


def image(name: str, version: str, **matches: str) -> ManifestV1Image:
    return ManifestV1Image(
        target_version=version,
        path=f"{name}.bin",
        app_version_match=matches.get("app"),
        net_version_match=matches.get("net"),
    )


def make_bundle(**images: ManifestV1Image) -> Bundle:
    """A bundle whose image contents are "<name> <version>"."""
    manifest = ManifestV1(**images)
    contents = {
        image.path: f"{name} {image.target_version}".encode()
        for name, image in manifest.images
    }
    return Bundle(manifest, contents)


def apply(device: FakeDevice, bundle: Bundle) -> None:
    asyncio.run(apply_bundle(device, bundle))


def test_applies_each_image_in_order():
    device = FakeDevice(app="1.0", net="1.0")

    apply(device, make_bundle(app=image("app", "2.0"), net=image("net", "2.0")))

    assert device.log == [
        "upload app 2.0",
        "apply app",
        "reboot",
        "confirm",
        "upload net 2.0",
        "apply net",
        "reboot",
        "confirm",
    ]


def test_an_image_already_running_is_only_confirmed():
    device = FakeDevice(app="2.0", net="1.0")

    apply(device, make_bundle(app=image("app", "2.0"), net=image("net", "2.0")))

    assert device.log == ["confirm", "upload net 2.0", "apply net", "reboot", "confirm"]


def test_stops_before_uploading_when_a_later_image_has_an_unmet_dependency():
    device = FakeDevice(app="1.0", net="1.0")
    bundle = make_bundle(app=image("app", "2.0"), net=image("net", "2.0", net=">=1.5"))

    with pytest.raises(RuntimeError, match=r"'net' requires 'net' >=1\.5"):
        apply(device, bundle)
    assert device.log == []


def test_a_dependency_the_bundle_satisfies_itself_does_not_stop_it():
    device = FakeDevice(app="1.0", net="1.0")
    bundle = make_bundle(app=image("app", "2.0"), net=image("net", "2.0", app=">=2.0"))

    apply(device, bundle)

    assert device.versions == {"app": Version(2, 0), "net": Version(2, 0)}


def test_a_reverted_image_fails_the_update_without_confirming():
    device = FakeDevice(revert=True, app="1.0")

    with pytest.raises(
        RuntimeError, match=r"not running 'app' 2\.0 after the reboot, it runs 1"
    ):
        apply(device, make_bundle(app=image("app", "2.0")))
    assert "confirm" not in device.log
