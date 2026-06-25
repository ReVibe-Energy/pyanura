import binascii
import enum
import ipaddress
import re
import types
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from anura.marshalling import CborKey, register_codec, unmarshal

msg_type = types.SimpleNamespace()
msg_type.Request = 0
msg_type.Response = 1
msg_type.Notification = 2


class APIErrorCode(enum.IntEnum):
    """API error codes."""

    RESERVED = 0
    """Reserved."""

    ARGUMENT_DECODE = 1
    """Decoding the argument failed."""

    VALUE_RANGE = 2
    """An argument value was out of range."""

    INVALID_STATE = 3
    """Internal state prohibits the requested operation."""

    OPERATION_FAILED = 4
    """The requested operation was attempted, but failed."""

    OUT_OF_ORDER = 5
    """Operation attempted out of the required order."""

    OUT_OF_BOUNDS = 6
    """Operation would exceed bounds."""

    NODE_UNAVAILABLE = 7
    """Node is not connected or not fully initialized."""

    RESPONSE_ENCODE = 8
    """Failed to encode the response."""


@dataclass
class APIError:
    """API error response.

    Attributes:
        code: Error code indicating the type of failure. See `APIErrorCode` enum
            for defined values.
        internal_code: Internal troubleshooting code. Not suitable for
            external use or API stability guarantees.
        message: Optional human-readable error description. Currently
            unused but reserved for future use.
    """

    code: Annotated[int, CborKey(0)]
    internal_code: Annotated[int | None, CborKey(1)] = None
    message: Annotated[str | None, CborKey(2)] = None


class Notification:
    @staticmethod
    def parse(notification_type, argument):
        event_classes = {
            "node_connected": NodeConnectedEvent,
            "node_disconnected": NodeDisconnectedEvent,
            "node_service_discovered": NodeServiceDiscoveredEvent,
            "avss_report_notified": AVSSReportNotifiedEvent,
            "avss_program_notified": AVSSProgramNotifiedEvent,
            "scan_nodes_received": ScanNodesReceivedEvent,
        }
        if event_class := event_classes.get(notification_type):
            return unmarshal(event_class, argument)
        else:
            return UnknownNotification(notification_type, argument)


@dataclass(frozen=True)
class BluetoothAddrLE:
    type: int
    address: bytes

    def __str__(self):
        if self.type == 0:
            type_str = "public"
        else:
            type_str = "random"
        return f"{self.address_str()}/{type_str}"

    def __repr__(self):
        return f'BluetoothAddrLE("{self.__str__()}")'

    def address_str(self) -> str:
        return binascii.hexlify(self.address, ":").decode("utf-8").upper()

    @staticmethod
    def parse(str):
        """Parse a Bluetooth address argument.

        >>> BluetoothAddrLE.parse("ff-ff-ff-ff-ff-ff/public")
        [0, b'\\xff\\xff\\xff\\xff\\xff\\xff']
        >>> BluetoothAddrLE.parse("a1:b2:c3:d4:e5:f6/random")
        [1, b'\\xa1\\xb2\\xc3\\xd4\\xe5\\xf6']
        >>> BluetoothAddrLE.parse("00:00:00:00:00:00")
        [0, b'\\x00\\x00\\x00\\x00\\x00\\x00']
        """

        word_pattern = re.compile(r"([^\/]+)(\/(random|public))?", re.IGNORECASE)
        match = word_pattern.fullmatch(str)

        if not match:
            raise ValueError()

        addr_raw = match.group(1)
        addr_type = 0

        if match.group(3) == "random":
            addr_type = 1

        addr_str = addr_raw.replace(":", "").replace("-", "")
        return BluetoothAddrLE(type=addr_type, address=bytes.fromhex(addr_str))


def _marshal_bluetooth_addr(addr: BluetoothAddrLE) -> tuple[int, bytes]:
    return (addr.type, addr.address)


def _unmarshal_bluetooth_addr(value) -> BluetoothAddrLE:
    match value:
        case type_, address:
            return BluetoothAddrLE(type_, address)
        case _:
            raise ValueError(f"'{value!r}' not decodable as BluetoothAddrLE")


register_codec(
    BluetoothAddrLE,
    marshal=_marshal_bluetooth_addr,
    unmarshal=_unmarshal_bluetooth_addr,
)


@dataclass
class AssignedNode:
    address: Annotated[BluetoothAddrLE, CborKey(0)]


@dataclass
class SetAssignedNodesArgs:
    nodes: Annotated[list[AssignedNode], CborKey(0)]


@dataclass
class GetAssignedNodesResult:
    nodes: Annotated[list[AssignedNode], CborKey(0)]


@dataclass
class ConnectedNode:
    address: Annotated[BluetoothAddrLE, CborKey(0)]
    rssi: Annotated[int, CborKey(1)]


@dataclass
class GetConnectedNodesResult:
    nodes: Annotated[list[ConnectedNode], CborKey(0)]


@dataclass
class AVSSRequestArgs:
    address: Annotated[BluetoothAddrLE, CborKey(0)]
    data: Annotated[bytes, CborKey(1)]


@dataclass
class AVSSRequestResult:
    response: Annotated[bytes, CborKey(0)]


@dataclass
class AVSSProgramWriteArgs:
    address: Annotated[BluetoothAddrLE, CborKey(0)]
    data: Annotated[bytes, CborKey(1)]


@dataclass
class GetDeviceInfoResult:
    board: Annotated[str, CborKey(0)]
    hw_rev: Annotated[int, CborKey(1)]
    device_id: Annotated[bytes, CborKey(2)]
    app_version: Annotated[str, CborKey(3)]
    app_build_version: Annotated[str, CborKey(4)]
    serial_number: Annotated[str, CborKey(5)]
    hostname: Annotated[str, CborKey(6)]
    mac_address: Annotated[bytes, CborKey(7)]
    ip_addresses: Annotated[list[ipaddress.IPv4Address], CborKey(8)]


@dataclass
class GetDeviceStatusResult:
    uptime: Annotated[int, CborKey(0)]
    reboot_count: Annotated[int, CborKey(1)]
    reset_cause: Annotated[int, CborKey(2)]


@dataclass
class GetFirmwareInfoResult:
    dfu_status: Annotated[int, CborKey(0)]
    app_version: Annotated[int, CborKey(1)]
    app_build_version: Annotated[str, CborKey(2)]
    net_version: Annotated[int, CborKey(3)]
    net_build_version: Annotated[str, CborKey(4)]


@dataclass
class GetPtpStatusResult:
    port_state: Annotated[str, CborKey(0)]
    offset: Annotated[int, CborKey(1)]
    delay: Annotated[int, CborKey(2)]
    offset_histogram: Annotated[list[int], CborKey(3)]


@dataclass
class DfuPrepareArgs:
    size: Annotated[int, CborKey(0)]


@dataclass
class DfuWriteArgs:
    offset: Annotated[int, CborKey(0)]
    data: Annotated[int, CborKey(1)]


@dataclass
class DfuApplyArgs:
    permanent: Annotated[int, CborKey(0)]


@dataclass
class SetTimeArgs:
    time: Annotated[int, CborKey(0)]


@dataclass
class GetTimeResult:
    time: Annotated[int, CborKey(0)]


@dataclass
class NodeConnectedEvent(Notification):
    address: Annotated[BluetoothAddrLE, CborKey(0)]


@dataclass
class NodeDisconnectedEvent(Notification):
    address: Annotated[BluetoothAddrLE, CborKey(0)]


@dataclass
class NodeServiceDiscoveredEvent(Notification):
    address: Annotated[BluetoothAddrLE, CborKey(0)]
    uuid: Annotated[UUID, CborKey(1)]


@dataclass
class AVSSReportNotifiedEvent(Notification):
    address: Annotated[BluetoothAddrLE, CborKey(0)]
    value: Annotated[bytes, CborKey(1)]


@dataclass
class AVSSProgramNotifiedEvent(Notification):
    address: Annotated[BluetoothAddrLE, CborKey(0)]
    value: Annotated[bytes, CborKey(1)]


@dataclass
class ScanNodesReceivedEvent(Notification):
    address: Annotated[BluetoothAddrLE, CborKey(0)]
    rssi: Annotated[int, CborKey(1)]
    data: Annotated[bytes, CborKey(2)]


class UnknownNotification(Notification):
    def __init__(self, notification_type, argument):
        self.notification_type = notification_type
        self.argument = argument
