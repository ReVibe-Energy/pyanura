import logging

from .models import APIError

__all__ = [
    "TransceiverConnectionError",
    "TransceiverError",
    "TransceiverRequestError",
]

logger = logging.getLogger("anura.transceiver")


class TransceiverError(Exception):
    """Base class for exceptions raised by TransceiverClient."""


class TransceiverConnectionError(TransceiverError):
    """Raised when the transceiver connection has broken."""


class TransceiverMethodNotFoundError(TransceiverError):
    """Raised when an API request returns an error."""

    def __init__(self, method):
        super().__init__(f"Method '{method}' not found.")


class TransceiverRequestError(TransceiverError):
    """Raised when an API request returns an error."""

    def __init__(self, method, error: APIError):
        self.error: APIError = error
        super().__init__(f'Request "{method}" returned an error response: {error}')
