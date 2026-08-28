from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar


class Transport(ABC):
    _registry: ClassVar[dict[str, Callable[..., "Transport"]]] = {}

    # Whether the client must keep transmitting to keep the connection open.
    # The transceiver's TCP server closes connections it has received nothing
    # on for 5 seconds; transports without such idle eviction (e.g. USB) opt
    # out. Enables the keepalive pings and, with them, the receive-silence
    # liveness check, which relies on the ping responses for traffic.
    requires_keepalive: ClassVar[bool] = True

    def __init_subclass__(cls, transport_type: str, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._registry[transport_type] = cls

    @abstractmethod
    async def open_connection(self) -> None:
        pass

    @abstractmethod
    async def send(self, payload: bytes, /) -> None:
        pass

    @abstractmethod
    async def read(self) -> bytes:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass

    @classmethod
    def create(cls, target_spec: str, *args, **kwargs):
        """Instantiate a Transport class

        Args:
            target_spec (str): A string that specifies the target device
                using the format <transport>:<target>. If <transport>: is
                omitted it defaults to `'tcp:'`.

            *args: In the case of TCP this is optionally the port
                number as a string. The arguments are passed to the
                subclass.
            *kwargs: Passed to the subclass.

        """
        if ":" in target_spec:
            transport_type, target = target_spec.split(":", 1)
        else:
            transport_type, target = "tcp", target_spec

        return cls._registry[transport_type](target, *args, **kwargs)
