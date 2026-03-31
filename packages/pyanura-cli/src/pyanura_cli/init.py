import logging

import click

from . import avss_commands, transceiver_commands

logging.basicConfig(
    format="[%(asctime)s.%(msecs)03d] <%(levelname)s> %(module)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@click.group()
def cli():
    pass


cli.add_command(avss_commands.avss_group)
cli.add_command(transceiver_commands.transceiver_group)
