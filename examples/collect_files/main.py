import argparse
import asyncio
import csv
import logging
import numpy as np
import os
from pathlib import Path
import time

import anura.avss as avss
from anura.transceiver import TransceiverClient
from anura.transceiver.proxy_avss_client import ProxyAVSSClient

logging.basicConfig(
    format="[%(asctime)s] <%(levelname)s> %(module)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

def write_csv(filename, acceleration):
    with open(filename, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerows(acceleration)

async def connect_node(transceiver, output_dir, addr):
    node_dir = Path(output_dir, str(addr).replace(":", "-").replace("/", "-"))
    os.makedirs(node_dir, exist_ok=True)

    logger.info("Started task for node %s", addr)
    try:
        async with ProxyAVSSClient(transceiver, addr) as node:
            version = await node.get_version()
            logger.info("%s version: %s (build: %s)", addr, version.version, version.build_version)

            with node.reports() as reports:
                logger.info("Requesting settings from %s", addr)
                await node.report_settings()
                logger.info("Enabling health reports from %s", addr)
                await node.report_health()
                logger.info("Enabling snippet reports from %s", addr)
                await node.report_snippets(count=None, auto_resume=True)

                async for msg in reports:
                    if isinstance(msg, avss.HealthReport):
                        logger.info("%s: Health report: %s", addr, msg)
                    elif isinstance(msg, avss.SnippetReport):
                        logger.info("%s: Snippet report: start_time=%s", addr, msg.start_time)
                        filename = f"snippet_{msg.start_time}.csv"
                        x = np.frombuffer(msg.samples[0], dtype=np.dtype("<h")) * 16.0 / 32768
                        y = np.frombuffer(msg.samples[1], dtype=np.dtype("<h")) * 16.0 / 32768
                        z = np.frombuffer(msg.samples[2], dtype=np.dtype("<h")) * 16.0 / 32768
                        accel = np.array([x,y,z]).T
                        write_csv(Path(node_dir, filename), accel)
                    elif isinstance(msg, avss.SettingsReport):
                        logger.info("%s: Settings report: %s", addr, msg)
                    else:
                        logger.info("(%s): Unknown report: %s", addr, type(msg))
    except avss.DisconnectedError as exc:
        pass
    finally:
        logger.info("Exiting task for node %s", addr)

async def connect_transceiver(host, output_dir):
    async with TransceiverClient(host) as transceiver:
        logger.info(f"Connected to transceiver at {host}")

        await transceiver.set_time(time=time.time_ns())
        logger.info(f"Updated time in transceiver {host}")

        async with asyncio.TaskGroup() as tg:
            connected_nodes_resp = await transceiver.get_connected_nodes()
            for node in connected_nodes_resp.nodes:
                tg.create_task(connect_node(transceiver, output_dir, node.address))

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--host")
    args = parser.parse_args()

    output_dir = Path(args.output)
    os.makedirs(output_dir, exist_ok=True)

    await connect_transceiver(args.host, output_dir)

if __name__ == '__main__':
    asyncio.run(main())
