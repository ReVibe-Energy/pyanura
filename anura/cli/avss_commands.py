import anura.avss as avss
from anura.transceiver import TransceiverClient, BluetoothAddrLE
from anura.transceiver.proxy_avss_client import ProxyAVSSClient
from anura.avss.bleak_avss_client import BleakAVSSClient
import asyncio
from bleak import BleakError, BleakScanner
import click
import functools
import json
import logging
import math
from pathlib import Path
import sys

logger = logging.getLogger(__name__)

def with_avss_client(f):
    @click.option("--transceiver", help="Hostname or IP address")
    @click.option("--transceiver-port", default=7645, show_default=True, help="TCP port number")
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
@click.option("--transceiver", help="Hostname or IP address")
@click.option("--transceiver-port", default=7645, show_default=True, help="TCP port number")
@click.option("--address", help="Bluetooth address of AVSS node.", required=True)
@click.option("--file", metavar="FILE", help="Path to firmware image.")
@click.option("--confirm-only", is_flag=True, help="Run only the confirm step.")
def upgrade(transceiver, transceiver_port, address, file, confirm_only):
    """Upgrade node firmware."""

    if not confirm_only and not file:
        click.echo("Error: At least one of options '--file' and '--confirm-only' must be given.", err=True)
        sys.exit(1)

    if not confirm_only:
        binary = Path(file).read_bytes()

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
                device = await BleakScanner.find_device_by_address(address, timeout=60)

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

                    click.echo(f"Version: {version.version} (build: {version.build_version})")
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
    with client.reports() as reports:
        click.echo(f"Starting {duration} s throughput test...")
        await client.test_throughput(duration=duration*1000)
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

        click.echo(f"Received {test.transfer_info.num_bytes} B "
                   f"over {test.transfer_info.num_segments} segments "
                   f"in {test.transfer_info.elapsed_time:.2f} s")

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
@with_avss_client
async def write_settings(client: avss.AVSSClient, file: str):
    """Write settings."""
    settings = json.loads(Path(file).read_text())
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
