import logging

from anura.marshalling import unmarshal

from .models import APIError

__all__ = [
    "TransceiverError",
    "TransceiverConnectionError",
    "TransceiverRequestError",
]

logger = logging.getLogger("anura.transceiver")


class TransceiverError(Exception):
    """Base class for exceptions raised by TransceiverClient."""


class TransceiverConnectionError(TransceiverError):
    """Raised when the transceiver connection has broken."""


class TransceiverRequestError(TransceiverError):
    """Raised when an API request returns an error."""

    def __init__(self, method, error: dict):
        self.error = error
        super().__init__(f'Request "{method}" returned an error response: {error}')

    def api_error(self) -> APIError | None:
        try:
            return unmarshal(APIError, self.error)
        except Exception:
            logger.exception("wtflol")
            return None
