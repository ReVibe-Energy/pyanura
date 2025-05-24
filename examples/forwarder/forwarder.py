import argparse
import asyncio
import json
import logging
import threading
from pathlib import Path
from urllib.parse import urlparse

import paho.mqtt.client as mqtt

import anura.avss as avss
from anura.transceiver import BluetoothAddrLE, TransceiverClient
from anura.transceiver.proxy_avss_client import ProxyAVSSClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class AnuraSupervisor:
    def __init__(self, config: dict) -> None:
        self.config = config

    async def on_avss_open(self, node: avss.AVSSClient, node_id: str):
        pass

    async def on_avss_report(self, node: avss.AVSSClient, node_id: str):
        pass

    async def on_transceiver_connect(self, transceiver: TransceiverClient):
        pass

    async def _node_task(self, transceiver, node_id):
        logger.info("Started task for node %s", node_id)

        node_config = self.config["nodes"][node_id]
        node_addr = BluetoothAddrLE.parse(node_config["address"])

        while True:
            try:
                async with ProxyAVSSClient(transceiver, node_addr) as node:
                    logger.info("Waiting for node to become available")
                    # Probe for node availability by sending periodic requests.
                    while True:
                        try:
                            version = await node.get_version()
                            break
                        except Exception:
                            await asyncio.sleep(1.0)

                    logger.info("Node is available: %s", version)

                    with node.reports() as reports:
                        await self.on_avss_open(node, node_id)

                        async for report in reports:
                            await self.on_avss_report(node, report, node_id)
            except asyncio.CancelledError:
                return
            except Exception as ex:
                logger.error("Error in node task: %s", ex)
                await asyncio.sleep(1.0)

    async def _transceiver_connect(self, host):
        while True:
            try:
                logger.info("Connecting to %s", host)
                transceiver = TransceiverClient(host)
                await transceiver.connect()
                logger.info("Connected to %s", host)
                return transceiver
            except asyncio.CancelledError:
                raise
            except:
                logger.info("Could not connect to %s", host)
                # Retry connection after a short delay
                await asyncio.sleep(1.0)

    async def _transceiver_task(self, transceiver_id):
        transceiver_config = self.config["transceivers"][transceiver_id]
        while True:
            try:
                transceiver = await self._transceiver_connect(
                    transceiver_config["host"]
                )

                async with asyncio.TaskGroup() as tg:
                    if self.on_transceiver_connect:
                        await self.on_transceiver_connect(transceiver)

                    node_addresses: list[BluetoothAddrLE] = []
                    for node_config in self.config["nodes"].values():
                        if node_config["transceiver"] == transceiver_id:
                            node_addresses.append(
                                BluetoothAddrLE.parse(node_config["address"])
                            )
                    await transceiver.set_assigned_nodes(node_addresses)

                    for node_id, node_config in self.config["nodes"].items():
                        if node_config["transceiver"] == transceiver_id:
                            tg.create_task(self._node_task(transceiver, node_id))
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.error("Error in transceiver task: %s", ex)
                await asyncio.sleep(1.0)

    async def run_async(self):
        async with asyncio.TaskGroup() as tg:
            for transceiver_id in self.config["transceivers"]:
                tg.create_task(self._transceiver_task(transceiver_id))


class Forwarder(AnuraSupervisor):
    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.mqtt_client: mqtt.Client
        self.mqtt_port: int
        self.mqtt_host: str
        self.mqtt_client_id: str
        self._parse_config()

    def _parse_config(self):
        # Parse MQTT broker URL
        broker_url = urlparse(
            self.config["mqtt"]["broker"], scheme="mqtt", allow_fragments=False
        )
        if broker_url.scheme == "mqtt":
            default_port = 1883
        elif broker_url.scheme == "mqtts":
            default_port = 8883
        else:
            raise ValueError("Only URL schemes 'mqtt' and 'mqtts' are allowed.")

        self.mqtt_port = (
            broker_url.port if broker_url.port is not None else default_port
        )
        self.mqtt_host = broker_url.hostname
        self.mqtt_client_id = self.config["mqtt"]["client_id"]

    def _connect_mqtt(self, host, port, client_id) -> mqtt.Client:
        connack_event = threading.Event()
        mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)

        # Handler for MQTT CONNACK
        def on_connect(client, userdata, connect_flags, reason_code, properties):
            connack_event.set()
            if reason_code != 0:
                logger.error(f"MQTT connect failed: {reason_code}")

        mqtt_client.on_connect = on_connect

        # Client.connect() is blocking
        logger.info(f"Connecting to MQTT broker {host}:{port}...")
        try:
            mqtt_client.connect(host, port)
        except Exception as ex:
            logger.error(f"MQTT connect failed: {ex}")
            return None

        # Start the MQTT client network loop on a separate thread
        mqtt_client.loop_start()

        connack_event.wait()
        if not mqtt_client.is_connected():
            return None

        logger.info("Connected to MQTT broker")

        return mqtt_client

    def _start_mqtt(self):
        self.mqtt_client = self._connect_mqtt(
            self.mqtt_host, self.mqtt_port, self.mqtt_client_id
        )
        if not self.mqtt_client:
            raise RuntimeError("Could not connect to broker")

    def _publish(self, topic, value):
        if isinstance(value, str):
            value = value.encode("utf-8")

        if not isinstance(value, bytes):
            value = bytes(value)

        self.mqtt_client.publish(f"{self.mqtt_client_id}/{topic}", value)

    def run(self):
        self._start_mqtt()
        asyncio.run(self.run_async())

    async def on_avss_open(self, node: avss.AVSSClient, node_id):
        version = await node.get_version()
        self._publish(f"node/{node_id}/version", version.version)

        if settings := self.config["nodes"][node_id].get("settings"):
            logger.info("Write settings")
            # Response introduced in version v24.4.1
            resp = await node.write_settings(settings)
            if resp and resp.num_unhandled:
                logger.warning(
                    "%d unhandled settings in write to node", resp.num_unhandled
                )

            logger.info("Apply settings")
            # Response introduced in version v24.6.0
            resp = await node.apply_settings(persist=True)
            if resp and resp.will_reboot:
                logger.info("Node will reboot to apply settings")

        logger.info("Request health reports")
        await node.report_health()
        logger.info("Request snippet reports")
        await node.report_snippets(count=None, auto_resume=True)

    async def on_avss_report(self, node, report, node_id):
        if isinstance(report, avss.HealthReport):
            logger.info("Health report: %s", report)
            # self._publish(f"node/{node_id}/health/_cbor", report.to_cbor())
            self._publish(f"node/{node_id}/health/battery", str(report.battery_voltage))
            self._publish(f"node/{node_id}/health/temperature", str(report.temperature))
        elif isinstance(report, avss.SnippetReport):
            logger.info("Snippet report: start_time=%d", report.start_time)
            # self._publish(f"node/{node_id}/snippet/_cbor", report.to_cbor())
        else:
            logger.info("%s", type(report))


def main():
    logging.basicConfig(
        format="[%(asctime)s] <%(levelname)s> %(module)s: %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", help="Configuration file path (JSON).", required=True
    )
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config).absolute()
    logger.info(f"Loading config from {config_path}")
    config = json.loads(config_path.read_text())

    forwarder = Forwarder(config)
    forwarder.run()


if __name__ == "__main__":
    main()
