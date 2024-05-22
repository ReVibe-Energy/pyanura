# pyanura Package

The pyanura package contains classes and command line utilities for interfacing
the ReVibe Anura sensors and transceivers.

## Installing the package for programmatic use

The package is installable using `pip3` by pointing to the top level directory (the one containing this README file).
First you should set up and actiavte a suitable virtual environment for your project.
After that you can install the pyanura package using `pip3`.

Example (assuming the package is located in the Downloads directory):

    pip3 install ~/Downloads/pyanura

Or with optional CLI dependencies included:

    pip3 install ~/Downloads/pyanura[cli]

## Installing command-line interface

If you just want to install the `anura` command-line utility and make it available
on your `PATH` the best option is likely to install `pipx` using your system's package
manager and then install `pyanura` using `pipx`.

    pipx install ~/Downloads/pyanura[cli]

Using this method you don't have to manually set up a virtual environment as `pipx`
will create one for you. Additionally it will add a script to your `PATH` that will
launch the command-line in the appropriate virtual environment.


## Development setup

For development in the  `pyanura` repository you should setup a virtual environment in which you will install the dependencies of `pyanura` but not the `pyanura` package itself.

Assuming you have activated a suitable a virtual environment, install the dendencies as follows:

    pip3 install -r requirements.txt

After that you should be able to launch the `anura` command-line insterface with the following command:

    python3 -m anura.cli
