"""High-level AVSS procedures.

The :class:`~anura.avss.client.AVSSClient` methods map one-to-one onto the
protocol: one command, characteristic write sequence or transfer loop each.
This package composes them into complete transactions — multi-step flows with
negotiation and fallback. Each procedure lives in its own module; only the
procedures themselves are part of the public interface.
"""

from ._upload_firmware import upload_firmware

__all__ = [
    "upload_firmware",
]
