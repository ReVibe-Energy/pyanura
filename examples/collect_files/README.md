# collect_files example

Example showing how to use pyanura library to connect to a transceiver and
request reports from its connected nodes. It does not assign nodes to
the transceiver or set any node settings. Those steps have to be carried
out manually before running the example, e.g. using the `anura`` cli.

Create and activate a virtual environment:

    python3 -m venv venv
    source venv/bin/activate

Install the pyanura package (relative path assuming working directory is
the collect_files example directory). The [cli] variant is not strictly
necessary but it will install the `anura` command in the virtual environment
which can be used to manually perform required setup not handled by the example.

    pip3 install ../../[cli]

Install other dependencies

    pip3 install numpy

Find your transceiver (abort with Ctrl+C):

    anura transceiver browse

Output should be similar to this:

    $ anura transceiver browse
    anura-ec9a0cb2000b.local.
    anura-ec9a0cb20018.local.
    ^C
    Aborted!

Assign a node to your transceiver:

    anura transceiver set-assigned-nodes --host anura-ec9a0cb2000b.local. EC:9A:0C:B1:00:05

Run the example to collect reports:

    python3 main.py --host anura-ec9a0cb2000b.local. --output out

Example output:

    [2024-05-20 09:32:36] <INFO> main: Connected to transceiver at anura-ec9a0cb2000b.local.
    [2024-05-20 09:32:36] <INFO> main: Updated time in transceiver anura-ec9a0cb2000b.local.
    [2024-05-20 09:32:36] <INFO> main: Started task for node EC:9A:0C:B1:00:05/public
    [2024-05-20 09:32:37] <INFO> main: EC:9A:0C:B1:00:05/public version: 24.4.1-next (build: v24.4.1-13-g40591cda1648)
    [2024-05-20 09:32:37] <INFO> main: Requesting settings from EC:9A:0C:B1:00:05/public
    [2024-05-20 09:32:38] <INFO> main: Enabling health reports from EC:9A:0C:B1:00:05/public
    [2024-05-20 09:32:40] <INFO> main: Enabling snippet reports from EC:9A:0C:B1:00:05/public
    [2024-05-20 09:32:42] <INFO> main: EC:9A:0C:B1:00:05/public: Settings report: SettingsReport(settings={0: 2048, 1: 10000, 2: 1024, 3: 60000})
    [2024-05-20 09:32:54] <INFO> main: EC:9A:0C:B1:00:05/public: Snippet report: start_time=1716190439999512616
    [2024-05-20 09:32:59] <INFO> main: EC:9A:0C:B1:00:05/public: Health report: HealthReport(uptime=1982, reboot_count=11, reset_cause=0, temperature=21.875, battery_voltage=3328, rssi=-45, eh_voltage=55, clock_sync_skew=0.9976929426193237, clock_sync_age=3, clock_sync_diff=25434719)
    [2024-05-20 09:33:04] <INFO> main: EC:9A:0C:B1:00:05/public: Snippet report: start_time=1716190449999869960
