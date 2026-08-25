"""Applying DFU bundles to AVSS nodes and transceivers.

For each component in the bundle, in order — check its dependencies against
the installed firmware, skip straight to confirmation if the component is
already running, otherwise upload, apply, wait for the device to reboot into
the new image, verify that the image was not reverted, and confirm it.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Protocol

import click
from bleak import BleakScanner

from anura.avss import procedures
from anura.avss.bleak_avss_client import BleakAVSSClient
from anura.avss.client import AVSSClient
from anura.avss.exceptions import AVSSOpCodeUnsupportedError
from anura.dfu import (
    Bundle,
    Component,
    Dependency,
    InstalledComponent,
    Version,
    total_dependencies,
    unmet_dependencies,
)
from anura.transceiver.client import TransceiverClient
from anura.transceiver.exceptions import TransceiverError
from anura.transceiver.models import BluetoothAddrLE
from anura.transceiver.proxy_avss_client import ProxyAVSSClient

from .progress import upload_progress

RECONNECT_TIMEOUT = 60.0


class BundleSession(Protocol):
    """Bundle operations on a connected device."""

    async def installed(self) -> dict[str, InstalledComponent]: ...

    async def upload(
        self, component: Component, progress: Callable[[int], None]
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

    async def installed(self) -> dict[str, InstalledComponent]:
        try:
            info = await self._client.get_firmware_info()
        except AVSSOpCodeUnsupportedError:
            # Firmware too old to support Get Firmware Info: only the app
            # version is available and the net image is unknown.
            version_info = await self._client.get_version()
            return {
                "app": InstalledComponent(
                    version=Version.from_string(version_info.version),
                    build=version_info.build_version,
                ),
            }
        return {
            "app": InstalledComponent(
                version=Version.from_numeric(info.app_version),
                build=info.app_build_version,
            ),
            "net": InstalledComponent(
                version=Version.from_numeric(info.net_version),
                build=info.net_build_version,
            ),
        }

    async def upload(
        self, component: Component, progress: Callable[[int], None]
    ) -> None:
        await procedures.upload_firmware(
            self._client, component.contents, image=0, progress=progress
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
        async with BleakAVSSClient(device) as client:
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
                    ProxyAVSSClient(self._transceiver, self._address)
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

    async def installed(self) -> dict[str, InstalledComponent]:
        info = await self._client.get_firmware_info()
        return {
            "app": InstalledComponent(
                version=Version.from_numeric(info.app_version),
                build=info.app_build_version,
            ),
            "net": InstalledComponent(
                version=Version.from_numeric(info.net_version),
                build=info.net_build_version,
            ),
        }

    async def upload(
        self, component: Component, progress: Callable[[int], None]
    ) -> None:
        await self._client.dfu_prepare(size=len(component.contents))
        await self._client.dfu_write_image(component.contents, progress=progress)

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
    dependencies: list[Dependency], installed: dict[str, InstalledComponent]
) -> None:
    if unmet := unmet_dependencies(dependencies, installed):
        raise RuntimeError("Dependency not met: " + "; ".join(str(u) for u in unmet))


async def apply_bundle(target: BundleTarget, bundle: Bundle) -> None:
    """Apply every component of a firmware bundle to a device, in order."""
    if not bundle.components:
        raise RuntimeError("Bundle contains no firmware components")

    checked_bundle_dependencies = False

    for component in bundle.components:
        async with target.connect() as session:
            installed = await session.installed()

            if not checked_bundle_dependencies:
                _check_dependencies(total_dependencies(bundle.components), installed)
                checked_bundle_dependencies = True

            current = installed.get(component.name)
            if current is not None and component.matches_installed(
                current.version, current.build
            ):
                click.echo(
                    f"Component '{component.name}' is already running "
                    f"{component.version}, confirming"
                )
                await session.confirm()
                continue

            _check_dependencies(component.dependencies, installed)

            with upload_progress(
                len(component.contents),
                f"Uploading '{component.name}' {component.version}",
            ) as on_progress:
                await session.upload(component, on_progress)
            await session.apply()

        click.echo("Waiting for device to reboot with new firmware image...")
        await target.wait_reboot()

        async with target.connect() as session:
            installed = await session.installed()
            current = installed.get(component.name)
            if current is None or not component.matches_installed(
                current.version, current.build
            ):
                running = (
                    f" (running {current.version})"
                    if current is not None and current.version is not None
                    else ""
                )
                raise RuntimeError(
                    f"Device has reverted the '{component.name}' update{running}"
                )
            click.echo(f"Confirming '{component.name}' {component.version}")
            await session.confirm()
