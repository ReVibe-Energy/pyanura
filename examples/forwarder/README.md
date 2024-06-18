# forwarder example

Example showing how to use `pyanura` library to manage connections to multiple
Anura transceivers and configure them to collect data from a number of sensor
nodes.


## Modify the configuration file

Edit the file `example-config.json` or create a copy which you modify according
to your needs.

The `"mqtt"` section points to a public MQTT broker meaning anyone in the world
is free to listen and observe your traffic. Use this at your own discretion or
change the configuration to point to a broker controlled by your organization.
Depending on the security requirements of your MQTT broker you may have to modify
the example code to pass the appropriate credentials to the MQTT client, as the
example currently does not support passing credentials via the configuration file.

    "mqtt": {
        "broker": "mqtt://broker.hivemq.com",
        "client_id": "a1e6d6e9-5e03-4e62-b347-a8b8d81d2ba2"
    },

In the `"transceivers"` section you must specify at least one transceiver that
the example code should connect to. The keys of the `"transceivers"` dictionary
are handles that you are free to choose. Here we picked `"L"` for "left":

	"transceivers": {
		"L": {
			"host": "anura-ec9a0cb20018.local."
		}
	},

In the `"nodes"` section you may specify any number of nodes. Just like with
`"transceivers"`, you are free to choose the keys of the `"nodes"` object freely.
We picked `"FL"` and `"DL"` for "feed left" and "discharge left" respectively.
Each node specification must include an `"address"` property containing the
Bluetooth ID of the node, and a `"transceiver"` property pointing to one of
the handles in the `"transceivers"` dictionary. A node specification may
optionally contain a `"settings"` dictionary specifying the settings to write.
The available settings are documented separately.

	"nodes": {
		"FL": {
            "address": "ec:9a:0c:b1:00:47"
			"transceiver": "L",
			"settings": {
				"base_sample_rate_hz": 512,
				"snippet_length": 1024,
				"snippet_interval_ms": 60000
			}
		},
        "DL": {
            "address": "ec:9a:0c:b1:00:48",
			"transceiver": "L",
			"settings": {
				"base_sample_rate_hz": 512,
				"snippet_length": 1024,
				"snippet_interval_ms": 60000
			}
        }
	}

## Running the example

Assuming a virtual environment with all necessary dependencies has been installed.
Run the following command from the `pyanura` repository root directory:

    python3 -m examples.forwarder --config examples/forwarder/example-config.json

Example output:

    [2024-07-03 16:41:52] <INFO> forwarder: Loading config from /home/abxy/work/revibe/pyanura/examples/forwarder/example-config.json
    [2024-07-03 16:41:52] <INFO> forwarder: Connecting to MQTT broker broker.hivemq.com:1883...
    [2024-07-03 16:41:52] <INFO> forwarder: Connected to MQTT broker
    [2024-07-03 16:41:52] <INFO> forwarder: Connecting to anura-ec9a0cb20018.local.
    [2024-07-03 16:41:53] <INFO> forwarder: Connected to anura-ec9a0cb20018.local.
    [2024-07-03 16:41:53] <INFO> forwarder: Started task for node FL
    [2024-07-03 16:41:53] <INFO> forwarder: Waiting for node to become available
    [2024-07-03 16:41:53] <INFO> forwarder: Started task for node DL
    [2024-07-03 16:41:53] <INFO> forwarder: Waiting for node to become available
    [2024-07-03 16:41:54] <INFO> forwarder: Node is available: GetVersionResponse(version='24.4.1-next', build_version='v24.4.1-19-gdc304611250b')
    [2024-07-03 16:41:55] <INFO> forwarder: Write settings
    [2024-07-03 16:41:55] <INFO> forwarder: Node is available: GetVersionResponse(version='24.6.0', build_version='v24.4.1-36-g6692f5374c53')
    [2024-07-03 16:41:57] <INFO> forwarder: Write settings
    [2024-07-03 16:41:57] <WARNING> forwarder: 1 unhandled settings in write to node
    [2024-07-03 16:41:57] <INFO> forwarder: Apply settings
    [2024-07-03 16:41:59] <INFO> forwarder: Apply settings
    [2024-07-03 16:41:59] <INFO> forwarder: Request health reports
    [2024-07-03 16:42:01] <INFO> forwarder: Request health reports
    [2024-07-03 16:42:01] <INFO> forwarder: Request snippet reports
    [2024-07-03 16:42:02] <INFO> forwarder: Request snippet reports
    [2024-07-03 16:42:07] <INFO> forwarder: Health report: HealthReport(uptime=85859, reboot_count=871, reset_cause=2, temperature=28.375, battery_voltage=3408, rssi=-51, eh_voltage=2728, clock_sync_skew=1.0000382661819458, clock_sync_age=7, clock_sync_diff=662)
    [2024-07-03 16:42:07] <INFO> forwarder: Health report: HealthReport(uptime=85859, reboot_count=8067, reset_cause=18, temperature=26.875, battery_voltage=3651, rssi=-55, eh_voltage=5181, clock_sync_skew=1.0000349283218384, clock_sync_age=1, clock_sync_diff=385)
    [2024-07-03 16:42:24] <INFO> forwarder: Snippet report: start_time=28199998746084
