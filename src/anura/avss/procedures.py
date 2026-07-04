"""High-level AVSS procedures.

The :class:`~anura.avss.client.AVSSClient` methods map one-to-one onto the
protocol: one command, characteristic write sequence or transfer loop each.
This module composes them into complete transactions — multi-step flows with
negotiation and fallback — and is the home for future ones, e.g. a full
settings replacement.
"""

import hashlib
import logging
from collections.abc import Callable

from .client import AVSSClient
from .exceptions import AVSSOpCodeUnsupportedError

logger = logging.getLogger(__name__)


async def upload_firmware(
    client: AVSSClient,
    binary: bytes,
    *,
    image: int,
    att_mtu: int = 243,
    progress: Callable[[int], None] | None = None,
    prepare_timeout: float = 30.0,
) -> None:
    """Prepare the node and upload a firmware image.

    Negotiates the windowed transfer procedure (Prepare Upgrade V2), which
    resumes an interrupted upload of the same image from the first byte the
    node has not received. Nodes without support are detected via the Opcode
    Unsupported response and served with the legacy prepare and
    unsynchronized transfer instead.

    Applying and confirming the upgrade are left to the caller.

    Args:
        client:   The AVSS client to operate on.
        binary:   Raw firmware binary.
        image:    Index of the firmware image to be uploaded.
        att_mtu:  ATT MTU for the connection.
        progress: Optional callback invoked with the cumulative number of
                  bytes transferred so far. For a windowed transfer this is
                  the number of bytes acknowledged by the node, which starts
                  beyond zero when a transfer is resumed.
        prepare_timeout: Timeout for the prepare step, which erases the
                  upgrade slot and can take several seconds.

    Raises:
        AVSSProgramTransferError: If a windowed transfer is aborted by the
            node or stalls without making progress.
    """
    try:
        params = await client.prepare_upgrade_v2(
            image,
            len(binary),
            hashlib.sha256(binary).digest(),
            timeout=prepare_timeout,
        )
    except AVSSOpCodeUnsupportedError:
        logger.info(
            "Windowed transfer not supported by node, using unsynchronized transfer"
        )
        await client.prepare_upgrade(image, len(binary), timeout=prepare_timeout)
        await client.program_transfer(binary, att_mtu, progress)
        return

    await client.program_transfer_windowed(binary, params, att_mtu, progress)
