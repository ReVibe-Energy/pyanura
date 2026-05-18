# pyanura Package

The pyanura package contains classes and command line utilities for interfacing
the ReVibe Anura sensors and transceivers.

## Installing the package for programmatic use

The package is installable using `pip3` by pointing to the top level directory (the one containing this README file).
First you should set up and actiavte a suitable virtual environment for your project.
After that you can install the pyanura package using `pip3`.

Example (assuming the package is located in the Downloads directory):

    pip3 install ~/Downloads/pyanura

## Installing command-line interface

The command-line interface lives in a separate `pyanura-cli` distribution
under `packages/pyanura-cli`. If you just want to install the `anura`
command-line utility and make it available on your `PATH` the best option
is likely to install `pipx` using your system's package manager and then
install `pyanura-cli` using `pipx`.

    pipx install ~/Downloads/pyanura/packages/pyanura-cli

Using this method you don't have to manually set up a virtual environment as `pipx`
will create one for you. Additionally it will add a script to your `PATH` that will
launch the command-line in the appropriate virtual environment.

At this point, you should be able to run CLI commands from your terminal - for example:
```
anura transceiver browse
```
to search for transceivers on the local network.


## Installing libusb
`libusb` must be manually installed to use USB transceivers in Windows.
1. Download `libusb` binaries, e.g. from the [Github releases page of `libusb`](https://github.com/libusb/libusb/releases)
2. Extract them to a directory suitable for keeping the files long-term
3. Add the `VS2022\MS64\dll` subfolder to your PATH environment variable. Example path: `C:\Users\felix\libusb-1.0.27\VS2022\MS64\dll`
4. Restart terminal/IDE in which you're invoking the CLI so that the new entry in PATH is loaded
5. Run pyanura


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
