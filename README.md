# pyanura

`pyanura` is a Python library for interfacing with ReVibe Anura sensors and
transceivers. A companion command-line utility, `anura`, is distributed
separately as the `pyanura-cli` package.

## Installing the package for programmatic use

Released versions of `pyanura` are published to ReVibe Energy's public
Cloudsmith index. Set up and activate a suitable virtual environment for
your project, then install `pyanura` from the index:

    pip3 install pyanura --index-url https://dl.cloudsmith.io/public/revibe-energy/public/python/simple/

## Installing command-line interface

The command-line interface is distributed as a separate `pyanura-cli`
package. The recommended way to install it is via
[`pipx`](https://pipx.pypa.io/), which manages its own virtual environment
and exposes the `anura` script on your `PATH`:

    pipx install --index-url https://dl.cloudsmith.io/public/revibe-energy/public/python/simple/ pyanura-cli

At this point, you should be able to run CLI commands from your terminal - for example:
```
anura transceiver browse
```
to search for transceivers on the local network.


## USB transceiver setup

### Windows: install libusb
`libusb` must be manually installed to use USB transceivers on Windows.
1. Download `libusb` binaries, e.g. from the [Github releases page of `libusb`](https://github.com/libusb/libusb/releases)
2. Extract them to a directory suitable for keeping the files long-term
3. Add the `VS2022\MS64\dll` subfolder to your PATH environment variable. Example path: `C:\libusb-1.0.27\VS2022\MS64\dll`
4. Restart terminal/IDE in which you're invoking the CLI so that the new entry in PATH is loaded
5. Run pyanura

### Linux: install udev rules
On most Linux distributions `libusb` is available from the package
manager (e.g. `apt install libusb-1.0-0`). To allow non-root users to
access the USB transceiver, install the udev rules file shipped with
this repository:

    sudo cp 99-anura.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    sudo udevadm trigger

The rules grant access to the `plugdev` group, so make sure your user
is a member of it (`sudo usermod -aG plugdev "$USER"` if not, then log
out and back in). Replug the transceiver after installing the rules.


## Development setup

The repository is a [`uv`](https://docs.astral.sh/uv/) workspace containing
both the `pyanura` library and the `pyanura-cli` package. From the repository
root, sync the workspace to create a virtual environment with all
dependencies installed:

    uv sync

After that you should be able to launch the `anura` command-line interface with the following command:

    uv run anura


## Running an example

Each project under `examples/` is a self-contained `uv` project. To run the
forwarder example:

    cd examples/forwarder
    uv run python -m forwarder --config example-config.json

## License

`pyanura` is available under the [Apache License, Version 2.0](LICENSE).
