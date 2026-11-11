"""Applying DFU bundles to AVSS nodes and transceivers.

For each image in the bundle, in order: if the device already runs it, just
confirm it. Otherwise check the dependencies against the running firmware,
upload and apply the image, wait for the device to reboot, check that it
runs the image, and confirm it.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Protocol

import click
from bleak import BleakScanner

from pyanura.avss import procedures
from pyanura.avss.client import AVSSClient
from pyanura.avss.exceptions import AVSSOpCodeUnsupportedError
from pyanura.avss.transport import BleakAVSSTransport, ProxyAVSSTransport
from pyanura.dfu import (
    Bundle,
    InstalledImage,
    ManifestV1Image,
    Version,
    running_images,
    unmet_dependencies,
)
from pyanura.transceiver.client import TransceiverClient
from pyanura.transceiver.exceptions import TransceiverError
from pyanura.transceiver.models import BluetoothAddrLE

from .connect import avss_client
from .progress import upload_progress

RECONNECT_TIMEOUT = 60.0


class BundleSession(Protocol):
    """Bundle operations on a connected device."""

    async def running(self) -> dict[str, InstalledImage]: ...

    async def upload(
        self, contents: bytes, progress: Callable[[int], None]
    ) -> None: ...

    async def apply(self) -> None: ...

    async def confirm(self) -> None: ...


class BundleTarget(Protocol):
    """A device that bundles can be applied to, reconnectable across reboots."""

    def connect(self) -> AbstractAsyncContextManager[BundleSession]: ...

    async def wait_reboot(self) -> None: ...


class AVSSBundleSession:
    def __init__(self, client: AVSSClient):
        self._client = client

    async def running(self) -> dict[str, InstalledImage]:
        try:
            info = await self._client.get_firmware_info()
        except AVSSOpCodeUnsupportedError:
            # Firmware too old to support Get Firmware Info: only the app
            # version is available and the net image is unknown.
            version_info = await self._client.get_version()
            return {
                "app": InstalledImage(
                    Version.from_string(version_info.version),
                    version_info.build_version or None,
                ),
            }
        return running_images(info)

    async def upload(self, contents: bytes, progress: Callable[[int], None]) -> None:
        await procedures.upload_firmware(
            self._client, contents, image=0, progress=progress
        )

    async def apply(self) -> None:
        await self._client.apply_upgrade()

    async def confirm(self) -> None:
        await self._client.confirm_upgrade(0)


class BleakAVSSTarget:
    """An AVSS node reached over a direct Bluetooth connection."""

    def __init__(self, address: str):
        self._address = address

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[AVSSBundleSession]:
        device = await BleakScanner.find_device_by_address(
            self._address, timeout=RECONNECT_TIMEOUT
        )
        if device is None:
            raise RuntimeError(f"Node {self._address} not found")
        async with avss_client(BleakAVSSTransport(device)) as client:
            yield AVSSBundleSession(client)

    async def wait_reboot(self) -> None:
        # Wait at least 5 seconds to make sure we don't find the device
        # before it has actually rebooted and started swapping images.
        await asyncio.sleep(5.0)


class ProxyAVSSTarget:
    """An AVSS node reached through a connected transceiver."""

    def __init__(self, transceiver: TransceiverClient, address: BluetoothAddrLE):
        self._transceiver = transceiver
        self._address = address

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[AVSSBundleSession]:
        # The node may not have reconnected to the transceiver yet;
        # retry until it answers.
        deadline = time.monotonic() + RECONNECT_TIMEOUT
        while True:
            stack = AsyncExitStack()
            try:
                client = await stack.enter_async_context(
                    avss_client(ProxyAVSSTransport(self._transceiver, self._address))
                )
                await client.get_version()
                break
            except Exception:
                await stack.aclose()
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(1.0)
        async with stack:
            yield AVSSBundleSession(client)

    async def wait_reboot(self) -> None:
        await asyncio.sleep(30.0)


class TransceiverBundleSession:
    def __init__(self, client: TransceiverClient):
        self._client = client

    async def running(self) -> dict[str, InstalledImage]:
        return running_images(await self._client.get_firmware_info())

    async def upload(self, contents: bytes, progress: Callable[[int], None]) -> None:
        await self._client.dfu_prepare(size=len(contents))
        await self._client.dfu_write_image(contents, progress=progress)

    async def apply(self) -> None:
        await self._client.dfu_apply()

    async def confirm(self) -> None:
        await self._client.dfu_confirm()


class TransceiverTarget:
    """A transceiver reached over TCP or USB."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[TransceiverBundleSession]:
        # The transceiver may still be rebooting; retry until it answers.
        deadline = time.monotonic() + RECONNECT_TIMEOUT
        while True:
            stack = AsyncExitStack()
            try:
                client = await stack.enter_async_context(
                    TransceiverClient(self._host, self._port)
                )
                break
            except (TimeoutError, OSError, TransceiverError):
                await stack.aclose()
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(1.0)
        async with stack:
            yield TransceiverBundleSession(client)

    async def wait_reboot(self) -> None:
        # Wait at least 5 seconds to make sure we don't reconnect before the
        # device has actually rebooted and started swapping images.
        await asyncio.sleep(5.0)


def _check_dependencies(
    images: tuple[tuple[str, ManifestV1Image], ...],
    running: dict[str, InstalledImage],
) -> None:
    if unmet := unmet_dependencies(images, running):
        raise RuntimeError("Dependency not met: " + "; ".join(str(u) for u in unmet))


async def apply_bundle(target: BundleTarget, bundle: Bundle) -> None:
    """Apply every image of a firmware bundle to a device, in order."""
    images = bundle.manifest.images
    if not images:
        raise RuntimeError("Bundle contains no firmware images")

    for index, (name, image) in enumerate(images):
        async with target.connect() as session:
            running = await session.running()

            if image.matches(running.get(name)):
                click.echo(
                    f"Component '{name}' is already running "
                    f"{image.target_version}, confirming"
                )
                await session.confirm()
                continue

            # Check this image and the ones after it, so that the update
            # stops before it starts rather than halfway through.
            _check_dependencies(images[index:], running)

            contents = bundle.images[image.path]
            with upload_progress(
                len(contents), f"Uploading '{name}' {image.target_version}"
            ) as on_progress:
                await session.upload(contents, on_progress)
            await session.apply()

        click.echo("Waiting for device to reboot with new firmware image...")
        await target.wait_reboot()

        async with target.connect() as session:
            current = (await session.running()).get(name)
            if not image.matches(current):
                now = f", it runs {current.version}" if current is not None else ""
                raise RuntimeError(
                    f"Device is not running '{name}' {image.target_version}"
                    f" after the reboot{now}"
                )
            click.echo(f"Confirming '{name}' {image.target_version}")
            await session.confirm()
