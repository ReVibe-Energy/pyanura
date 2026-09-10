"""High-level AVSS procedures.

The :class:`~anura.avss.client.AVSSClient` methods map one-to-one onto the
protocol: one command, characteristic write sequence or transfer loop each.
This package composes them into complete transactions — multi-step flows with
negotiation and fallback. Each procedure lives in its own module; only the
procedures themselves, and the result types they return, are part of the
public interface.
"""

from ._update_settings import UpdateSettingsResult, update_settings
from ._upload_firmware import upload_firmware

__all__ = [
    "UpdateSettingsResult",
    "update_settings",
    "upload_firmware",
]
