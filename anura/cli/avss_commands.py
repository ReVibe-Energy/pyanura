import asyncio
import functools
import json
import logging
import math
import sys
import time
from pathlib import Path

import click
from bleak import BleakError, BleakScanner

import anura.avss as avss
from anura.avss.bleak_avss_client import BleakAVSSClient
from anura.transceiver import BluetoothAddrLE, TransceiverClient
from anura.transceiver.proxy_avss_client import ProxyAVSSClient

from .session import SessionFile

logger = logging.getLogger(__name__)


def with_avss_client(f):
    @click.option("--transceiver", help="Hostname, IP address or usb:<serial>")
    @click.option(
        "--transceiver-port", default=7645, show_default=True, help="TCP port number"
    )
    @click.option("--address", help="Bluetooth address of AVSS node.", required=True)
    @functools.wraps(f)
    def wrapper(transceiver, transceiver_port, address, *args, **kwargs):
        address = BluetoothAddrLE.parse(address)

        async def do_async():
            try:
                logger.info(f"Connecting to {address}")
                async with BleakAVSSClient(address.address_str()) as client:
                    logger.info(f"Connected")
                    return await f(*args, client=client, **kwargs)
            except BleakError as ex:
                click.echo(f"Error: {ex}", err=True)
                sys.exit(1)

        async def do_proxy_async():
            logger.info(f"Connect to transceiver {transceiver}")
            async with TransceiverClient(transceiver, transceiver_port) as trx_client:
                # Check if the transceiver is assigned to the given node
                resp = await trx_client.get_assigned_nodes()
                if not any(node.address == address for node in resp.nodes):
                    click.echo(f"Error: Transceiver not assigned to node {address}")
                    sys.exit(1)

                async with ProxyAVSSClient(trx_client, address) as client:
                    return await f(*args, client=client, **kwargs)

        if transceiver:
            asyncio.run(do_proxy_async())
        else:
            asyncio.run(do_async())

    return wrapper


@click.group("avss")
def avss_group():
    """Anura Vibration Sensing Service (AVSS) commands."""
    pass


@avss_group.command()
def scan():
    """Scan for AVSS nodes using the computer's Bluetooth adapter."""
    stop_event = asyncio.Event()

    def on_detection(device, advertising_data):
        if avss.ServiceUuid in advertising_data.service_uuids:
            print(f"{device.address} {advertising_data.local_name}")

    async def do_async():
        try:
            async with BleakScanner(on_detection) as scanner:
                await stop_event.wait()
        except BleakError as ex:
            click.echo(f"ERROR: {ex}", err=True)
            sys.exit(1)

    asyncio.run(do_async())


@avss_group.command()
@click.option("--transceiver", help="Hostname, IP address or usb:<serial>")
@click.option(
    "--transceiver-port", default=7645, show_default=True, help="TCP port number"
)
@click.option("--address", help="Bluetooth address of AVSS node.", required=True)
@click.option("--file", metavar="FILE", help="Path to firmware image.")
@click.option("--confirm-only", is_flag=True, help="Run only the confirm step.")
def upgrade(transceiver, transceiver_port, address, file, confirm_only):
    """Upgrade node firmware."""

    if not confirm_only and not file:
        click.echo(
            "Error: At least one of options '--file' and '--confirm-only' must be given.",
            err=True,
        )
        sys.exit(1)

    if not confirm_only:
        try:
            binary = Path(file).read_bytes()
        except OSError as ex:
            click.echo(f"Error: {ex}", err=True)
            sys.exit(1)

    address = BluetoothAddrLE.parse(address)

    async def do_async():
        try:
            device = await BleakScanner.find_device_by_address(address.address_str())
            image_index = 0

            if not confirm_only:
                async with BleakAVSSClient(device) as client:
                    await client.prepare_upgrade(image_index, len(binary))
                    await client.program_transfer(binary)
                    await client.apply_upgrade()

                click.echo("Waiting for node to reboot with new firmware image...")
                # Wait at last 5 seconds to make sure we don't find the device
                # before it has actually rebooted and started swapping images.
                await asyncio.sleep(5)
                device = await BleakScanner.find_device_by_address(
                    address.address_str(), timeout=60
                )

            async with BleakAVSSClient(device) as client:
                click.echo("Confirming new image")
                await client.confirm_upgrade(image_index)
        except Exception as ex:
            click.echo(f"Error: {ex}", err=True)
            sys.exit(1)

    async def do_proxy_async():
        try:
            logger.info(f"Connect to transceiver {transceiver}")
            async with TransceiverClient(transceiver, transceiver_port) as trx_client:
                # Check if the transceiver is assigned to the given node
                resp = await trx_client.get_assigned_nodes()
                if not any(node.address == address for node in resp.nodes):
                    click.echo(f"Error: Transceiver not assigned to node {address}")
                    sys.exit(1)

                image_index = 0

                if not confirm_only:
                    async with ProxyAVSSClient(trx_client, address) as client:
                        await client.prepare_upgrade(image_index, len(binary))
                        await client.program_transfer(binary)
                        await client.apply_upgrade()

                    click.echo("Waiting for node to reboot with new firmware image...")
                    await asyncio.sleep(30.0)

                async with ProxyAVSSClient(trx_client, address) as client:
                    while True:
                        try:
                            version = await client.get_version()
                            break
                        except:
                            await asyncio.sleep(1.0)

                    click.echo(
                        f"Version: {version.version} (build: {version.build_version})"
                    )
                    click.echo("Confirming new image")
                    await client.confirm_upgrade(image_index)

        except Exception as ex:
            click.echo(f"Error: {ex}", err=True)
            sys.exit(1)

    if transceiver:
        asyncio.run(do_proxy_async())
    else:
        asyncio.run(do_async())


@avss_group.command()
@with_avss_client
async def get_version(client: avss.AVSSClient):
    """Get the node firmware version."""
    resp = await client.get_version()
    click.echo(resp)


@avss_group.command()
@with_avss_client
async def reset(client: avss.AVSSClient):
    """Reset a node."""
    await client.reboot()
    click.echo("Resetting shortly.")


@avss_group.command()
@click.option("--duration", default=1)
@with_avss_client
async def throughput(client: avss.AVSSClient, duration: float):
    """Perform a throughput test."""
    # Passing parse=False lets us access the raw payload and, more importantly,
    # it lets us access the transfer info.
    with client.reports(parse=False) as reports:
        click.echo(f"Starting {duration} s throughput test...")
        await client.test_throughput(duration=duration * 1000)
        test = await anext(reports)

        if test.transfer_info.elapsed_time > 0:
            throughput = (
                test.transfer_info.num_bytes / test.transfer_info.elapsed_time / 1000
            )
        else:
            throughput = "??"

        segment_size = math.ceil(
            test.transfer_info.num_bytes / test.transfer_info.num_segments
        )

        click.echo(
            f"Received {test.transfer_info.num_bytes} B "
            f"over {test.transfer_info.num_segments} segments "
            f"in {test.transfer_info.elapsed_time:.2f} s"
        )

        click.echo(f"Throughput:   {throughput:.2f} kB/s")
        click.echo(f"Segment size: {segment_size} B")


@avss_group.command()
@with_avss_client
async def read_settings(client: avss.AVSSClient):
    """Read settings."""
    with client.reports() as reports:
        logger.info("Requesting settings report from device")
        await client.report_settings()

        logger.info("Waiting for settings report")
        async for msg in reports:
            if isinstance(msg, avss.SettingsReport):
                click.echo(json.dumps(avss.SettingsMapper.to_readable(msg.settings)))
                break


@avss_group.command()
@click.option("--file", metavar="FILE", help="Path to settings file.")
@click.option("--reset-defaults", is_flag=True, help="Reset default values")
@with_avss_client
async def write_settings(client: avss.AVSSClient, file: str, reset_defaults: bool):
    """Write settings."""
    if file:
        try:
            settings = json.loads(Path(file).read_text())
        except FileNotFoundError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
    else:
        settings = {}

    try:
        resp = await client.write_settings_v2(
            settings, reset_defaults=reset_defaults, apply=True
        )
        click.echo(resp)
    except avss.client.AVSSOpCodeUnsupportedError:
        logger.info("Write Settings v2 opcode not supported, using fallback...")
        resp = await client.write_settings(settings)
        click.echo(resp)
        resp = await client.apply_settings(persist=True)
        click.echo(resp)


@avss_group.command()
@with_avss_client
async def deactivate(client: avss.AVSSClient):
    """Deactivate(decommission) a node."""
    await client.deactivate(key=0xFEEDF00D)
    click.echo("Deactivating shortly.")


@avss_group.command()
@with_avss_client
async def health_report(client: avss.AVSSClient):
    """Health report."""
    with client.reports() as reports:
        resp = await client.report_health(count=1)

        logger.info("Waiting for health report")
        async for msg in reports:
            if isinstance(msg, avss.HealthReport):
                click.echo(f"Health report: {msg}")
                break


@avss_group.command()
@with_avss_client
async def get_firmware_info(client: avss.AVSSClient):
    """Get firmware info"""
    info = await client.get_firmware_info()
    major = (info.app_version >> 24) & 0xFF
    minor = (info.app_version >> 16) & 0xFF
    patch = (info.app_version >> 8) & 0xFF
    tweak = info.app_version & 0xFF
    click.echo(
        f"App version: v{major}.{minor}.{patch}.{tweak}, build: {info.app_build_version}, status: {info.app_status}"
    )
    major = (info.net_version >> 24) & 0xFF
    minor = (info.net_version >> 16) & 0xFF
    patch = (info.net_version >> 8) & 0xFF
    tweak = info.net_version & 0xFF
    click.echo(
        f"Net version: v{major}.{minor}.{patch}.{tweak}, build: {info.net_build_version}"
    )


@avss_group.command()
@click.option("--duration", default=2, help="Time(seconds) to run measurement")
@with_avss_client
async def trigger_measurement(client: avss.AVSSClient, duration: float):
    """Trigger measurement"""
    resp = await client.trigger_measurement(duration_ms=duration * 1000)
    click.echo(resp)


@avss_group.command()
@click.option("--duration", default=4)
@click.option("--output", help="path to output file", required=True)
@click.option("--captures", is_flag=True, help="Fetch capture reports")
@click.option("--snippets", is_flag=True, help="Fetch snippet reports")
@click.option("--aggregates", is_flag=True, help="Fetch aggregated values reports")
@with_avss_client
async def quick_measurement(
    client: avss.AVSSClient, duration, output, captures, snippets, aggregates
):
    """Quick measurement"""
    settings = {
        "base_sample_rate_hz": 1024,
        "snippet_mode": 0,
        "capture_mode": 0,
        "aggregates_mode": 0,
    }

    if captures:
        settings.update(
            {
                "capture_mode": 1,
                "capture_buffer_length": 1024,
                "events_motion_start_enable": True,
                "events_motion_start_capture": True,
                "events_motion_start_capture_duration_ms": (duration * 1000),
            }
        )

    if snippets:
        settings.update(
            {
                "snippet_length": 1024,
                "snippet_mode": 2,
            }
        )

    if aggregates:
        settings.update(
            {
                "aggregates_mode": 1,
                "aggregates_sample_rate_hz": 512,
                "aggregates_interval_ms": 1000,
                "aggregates_fft_mode": 0,
                "aggregates_fft_length": 512,
                "aggregates_param_enable_0_31": 0xFFFFFFFF,
                "aggregates_param_enable_32_63": 0xFFFFFFFF,
            }
        )

    await client.write_settings(settings)
    resp = await client.apply_settings(persist=True)

    if resp.will_reboot:
        click.echo(
            "Rebooting node to apply settings, re-run command to start measurement"
        )
        sys.exit()

    with client.reports(parse=False) as reports:
        if captures:
            await client.report_capture(count=None, auto_resume=False)

        if snippets:
            await client.report_snippets(count=None, auto_resume=False)

        if aggregates:
            await client.report_aggregates(count=None, auto_resume=False)

        await client.trigger_measurement(duration_ms=duration * 1000)

        click.echo("Waiting for reports")

        async def collect_reports():
            with SessionFile(output, read_only=False) as f:
                f.update_session_info(time.time_ns())

                async for report in reports:
                    f.insert_avss_report(
                        received_at=time.time_ns(),
                        node_id="NODE",
                        report_type=report.report_type,
                        payload_cbor=report.payload_cbor,
                    )
                    click.echo(f"Report Type {report.report_type}")

        try:
            await asyncio.wait_for(collect_reports(), duration)
        except TimeoutError:
            pass
