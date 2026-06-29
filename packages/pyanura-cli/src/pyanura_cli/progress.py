from collections.abc import Callable, Iterator
from contextlib import contextmanager

import click


@contextmanager
def upload_progress(length: int, label: str) -> Iterator[Callable[[int], None]]:
    """Yield a progress callback that drives a click progress bar.

    The callback expects the cumulative number of bytes transferred so far,
    matching the ``progress`` callbacks on ``dfu_write_image`` and
    ``program_transfer``.

    Usage:
        with upload_progress(len(image), "Uploading") as on_progress:
            await client.program_transfer(image, progress=on_progress)
    """
    with click.progressbar(length=length, label=label) as bar:
        sent = 0

        def on_progress(total_sent: int) -> None:
            nonlocal sent
            bar.update(total_sent - sent)
            sent = total_sent

        yield on_progress
