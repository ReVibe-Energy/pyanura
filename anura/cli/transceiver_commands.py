import asyncio
import functools
import logging
import sys
import time
from pathlib import Path

import click
import zeroconf

from anura.transceiver.client import TransceiverClient
from anura.transceiver.models import BluetoothAddrLE, ScanNodesReceivedEvent
from anura.transceiver.proxy_avss_client import ProxyAVSSClient
from anura.transceiver.transport import USBTransport

logger = logging.getLogger(__name__)


def with_transceiver_client(f):
    @click.option(
        "--host",
        metavar="HOST",
        required=True,
        help="Hostname, IP address or usb:<serial>",
    )
    @click.option(
        "--port",
        metavar="PORT",
        default=7645,
        show_default=True,
        help="TCP port number",
    )
    @functools.wraps(f)
    def wrapper(host, port, *args, **kwargs):
        async def do_async():
            try:
                logger.debug(f"Connecting to {host}:{port}")
                async with TransceiverClient(host, port) as client:
                    logger.debug("Connected")
                    return await f(*args, client=client, **kwargs)
            except Exception as ex:
                click.echo(f"Error: {ex}", err=True)
                sys.exit(1)

        asyncio.run(do_async())

    return wrapper


@click.group("transceiver")
def transceiver_group():
    """Transceiver commands."""
    pass


@transceiver_group.command()
def browse():
    """List transceivers discovered using mDNS and USB"""

    class EchoDistinctListener(zeroconf.ServiceListener):
        def __init__(self):
            self._found_servers = set()

        def add_service(self, zc: zeroconf.Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)

            if info.port != 7645:
                server = f"{info.server}:{info.port}"
            else:
                server = f"{info.server}"

            if server not in self._found_servers:
                click.echo(server)
                self._found_servers.add(server)

    for serial in USBTransport.list_devices():
        print(f"usb:{serial}")

    with zeroconf.Zeroconf() as zc:
        listener = EchoDistinctListener()
        zeroconf.ServiceBrowser(zc, "_revibe-anura._tcp.local.", listener)
        time.sleep(60.0)


@transceiver_group.command()
@with_transceiver_client
@click.argument("address", nargs=-1)
async def set_assigned_nodes(client: TransceiverClient, address: list[str]):
    """Set assigned nodes"""
    nodes = []
    for arg in address:
        try:
            nodes.append(BluetoothAddrLE.parse(arg))
        except ValueError:
            click.echo(f"Invalid node address {arg}")

    await client.set_assigned_nodes(nodes)


@transceiver_group.command()
@with_transceiver_client
async def get_assigned_nodes(client: TransceiverClient):
    """Get assigned nodes."""
    for node in (await client.get_assigned_nodes()).nodes:
        click.echo(f"{node.address}")


@transceiver_group.command()
@with_transceiver_client
async def get_connected_nodes(client: TransceiverClient):
    """Get connected nodes."""
    for node in (await client.get_connected_nodes()).nodes:
        click.echo(f"{node.address} RSSI: {node.rssi}")


@transceiver_group.command()
@with_transceiver_client
@click.option(
    "--test-data",
    "mode",
    flag_value="test",
    default=True,
    help="Measure transfer speed using artifical test data.",
)
@click.option(
    "--snippet-reports",
    "mode",
    flag_value="snippets",
    help="Measure transfer speed for snippet reports.",
)
@click.option(
    "--duration", default=1, help="Duration of artificial test data. Use with '--test'."
)
async def avss_throughput(client: TransceiverClient, mode: str, duration: int):
    """Measure concurrent AVSS throughput."""

    async def test_throughput(addr):
        async with ProxyAVSSClient(client, addr) as node:
            with node.reports(parse=False) as reports:
                if mode == "test":
                    click.echo(f"{addr}: Starting {duration} s throughput test...")
                    await node.test_throughput(duration=duration * 1000)
                elif mode == "snippets":
                    click.echo(f"{addr}: Requesting snippet reports...")
                    await node.report_snippets(count=None, auto_resume=True)

                async for test in reports:
                    if test.transfer_info.elapsed_time > 0:
                        throughput = (
                            test.transfer_info.num_bytes
                            / test.transfer_info.elapsed_time
                            / 1000
                        )
                    else:
                        throughput = "??"

                    click.echo(
                        f"{addr}: Received {test.transfer_info.num_bytes} B "
                        f"over {test.transfer_info.num_segments} segments "
                        f"in {test.transfer_info.elapsed_time:.2f} s "
                        f"({throughput:.2f} kB/s)"
                    )

                    if mode == "test":
                        # Only one test report is generated per call to "test_throughput"
                        break

    assigned_nodes_resp = await client.get_assigned_nodes()
    if len(assigned_nodes_resp.nodes) == 0:
        click.echo(
            "No nodes are assigned to the transceiver. Assign nodes and try again."
        )
        return

    try:
        async with asyncio.TaskGroup() as tg:
            for node in assigned_nodes_resp.nodes:
                tg.create_task(test_throughput(node.address))
    except ExceptionGroup as ex_group:
        for ex in ex_group.exceptions:
            click.echo(ex)


@transceiver_group.command()
@with_transceiver_client
async def get_device_info(client: TransceiverClient):
    """Get device info."""
    click.echo(await client.get_device_info())


@transceiver_group.command()
@with_transceiver_client
async def get_device_status(client: TransceiverClient):
    """Get device status."""
    click.echo(await client.get_device_status())


@transceiver_group.command()
@with_transceiver_client
async def get_firmware_info(client: TransceiverClient):
    """Get firmware info."""
    click.echo(await client.get_firmware_info())


@transceiver_group.command()
@with_transceiver_client
async def get_ptp_status(client: TransceiverClient):
    """Get Precision Time Protocol (PTP) status."""
    click.echo(await client.get_ptp_status())


@transceiver_group.command()
@with_transceiver_client
async def get_time(client: TransceiverClient):
    """Get current time from a transceiver."""
    click.echo(await client.get_time())


@transceiver_group.command()
@with_transceiver_client
@click.option("--time", "time_", metavar="TIMESTAMP", help="Time in seconds")
async def set_time(client: TransceiverClient, time_):
    """Set the time of a transceiver.

    A set time comand will be sent with the specified time
    or current time if no time is specified. If the transceiver
    is acting in a PTP slave role, the set time command has
    no lasting result."""
    if time_ is not None:
        time_ns = int(time_) * 1000000000
    else:
        time_ns = time.time_ns()
    click.echo(f"Setting time to {time_ns} ns")
    await client.set_time(time=time_ns)


@transceiver_group.command()
@with_transceiver_client
async def reset(client: TransceiverClient):
    """Reset a transceiver."""
    await client.reboot()
    click.echo("Resetting shortly.")


@transceiver_group.command()
@click.option(
    "--host", metavar="HOST", required=True, help="Hostname, IP address or usb:<serial>"
)
@click.option(
    "--port", metavar="PORT", default=7645, show_default=True, help="TCP port number"
)
@click.option("--file", metavar="FILE", help="Path to firmware image.")
@click.option("--confirm-only", is_flag=True, help="Run only the confirm step.")
def upgrade(host, port, file, confirm_only):
    """Upgrade transceiver firmware"""

    if not confirm_only and not file:
        click.echo(
            "Error: At least one of options '--file' and '--confirm-only' must be given.",
            err=True,
        )
        sys.exit(1)

    if file:
        image = Path(file).read_bytes()

    async def do():
        try:
            if not confirm_only:
                async with TransceiverClient(host, port) as trx:
                    await trx.dfu_prepare(size=len(image))
                    await trx.dfu_write_image(image)
                    await trx.dfu_apply()

                click.echo(
                    "Waiting for transceiver to reboot with new firmware image..."
                )
                # Wait at last 5 seconds to make sure we don't find the device
                # before it has actually rebooted and started swapping images.
                await asyncio.sleep(5)

            wait_end = time.time() + 55
            while time.time() < wait_end:
                try:
                    async with TransceiverClient(host, port) as trx:
                        click.echo("Confirming new image")
                        await trx.dfu_confirm()
                        return
                except TimeoutError:
                    pass
            click.echo("Timed out")
        except Exception as ex:
            click.echo(f"Error: {ex}", err=True)
            sys.exit(1)

    asyncio.run(do())


@transceiver_group.command()
@with_transceiver_client
async def scan(client: TransceiverClient):
    """Scan for nodes using a transceiver."""
    try:
        with client.notifications() as notifications:
            await client.scan_nodes()

            async for msg in notifications:
                if isinstance(msg, ScanNodesReceivedEvent):
                    click.echo(f"Found {msg.address} RSSI: {msg.rssi} dBm")
    finally:
        await client.scan_nodes_stop()
