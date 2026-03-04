from abc import ABC, abstractmethod
from typing import Callable


class AVSSTransport(ABC):
    """Abstract base class for AVSS transport layer.

    A transport handles the low-level communication for the AVSS protocol,
    including connection management, request/response handling, and
    notification dispatching.

    The transport lifecycle is managed explicitly via open() and close() methods,
    allowing for flexible retry logic and connection state management.
    """

    @abstractmethod
    async def open(self) -> None:
        """Make transport operational for AVSS communication.

        This method should be idempotent or raise an error if already open.
        """

    @abstractmethod
    async def close(self) -> None:
        """Stop AVSS communication and cleanup resources.

        This method should be safe to call multiple times.
        """

    @abstractmethod
    async def control_point_request(self, req: bytes) -> bytes:
        """Send a control point request and return the response.

        Args:
            req: The request bytes to send
            timeout: Maximum time to wait for response in seconds

        Returns:
            Response bytes from the control point

        Raises:
            TimeoutError: If response not received within timeout
            AVSSConnectionError: If transport is not open or connection lost
        """

    @abstractmethod
    async def program_write(self, value: bytes) -> None:
        """Write data to the program characteristic.

        Args:
            value: The data bytes to write

        Raises:
            AVSSConnectionError: If transport is not open or connection lost
        """

    @abstractmethod
    def set_report_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register callback for report characteristic notifications.

        Args:
            callback: Function to call when report notifications arrive.
                Should accept bytes as the only argument.
        """

    @abstractmethod
    def set_program_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register callback for program characteristic notifications.

        Args:
            callback: Function to call when program notifications arrive.
                Should accept bytes as the only argument.
        """

    @abstractmethod
    def set_closed_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called when the transport closes.

        Args:
            callback: Function to call when the transport closes
                (either cleanly or due to error).
        """
