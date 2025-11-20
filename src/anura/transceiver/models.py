import binascii
import ipaddress
import re
import types
from dataclasses import dataclass
from uuid import UUID

from anura.marshalling import cbor_field, unmarshal

msg_type = types.SimpleNamespace()
msg_type.Request = 0
msg_type.Response = 1
msg_type.Notification = 2


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


@dataclass
class APIError:
    code: int = cbor_field(0)
    internal_code: int = cbor_field(1)
    message: str = cbor_field(2)


@dataclass
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

    def _marshal(self) -> tuple[int, bytes]:
        return (self.type, self.address)

    @classmethod
    def _unmarshal(cls, value) -> "BluetoothAddrLE":
        match value:
            case type_, address:
                return cls(type_, address)
            case _:
                raise ValueError(f"'{repr(value)}' not decodable as BluetoothAddrLE")


@dataclass
class AssignedNode:
    address: BluetoothAddrLE = cbor_field(0)


@dataclass
class SetAssignedNodesArgs:
    nodes: list[AssignedNode] = cbor_field(0)


@dataclass
class GetAssignedNodesResult:
    nodes: list[AssignedNode] = cbor_field(0)


@dataclass
class ConnectedNode:
    address: BluetoothAddrLE = cbor_field(0)
    rssi: int = cbor_field(1)


@dataclass
class GetConnectedNodesResult:
    nodes: list[ConnectedNode] = cbor_field(0)


@dataclass
class AVSSRequestArgs:
    address: BluetoothAddrLE = cbor_field(0)
    data: bytes = cbor_field(1)


@dataclass
class AVSSProgramWriteArgs:
    address: BluetoothAddrLE = cbor_field(0)
    data: bytes = cbor_field(1)


@dataclass
class GetDeviceInfoResult:
    board: str = cbor_field(0)
    hw_rev: int = cbor_field(1)
    device_id: bytes = cbor_field(2)
    app_version: str = cbor_field(3)
    app_build_version: str = cbor_field(4)
    serial_number: str = cbor_field(5)
    hostname: str = cbor_field(6)
    mac_address: bytes = cbor_field(7)
    ip_addresses: list[ipaddress.IPv4Address] = cbor_field(8)


@dataclass
class GetDeviceStatusResult:
    uptime: int = cbor_field(0)
    reboot_count: int = cbor_field(1)
    reset_cause: int = cbor_field(2)


@dataclass
class GetFirmwareInfoResult:
    dfu_status: int = cbor_field(0)
    app_version: int = cbor_field(1)
    app_build_version: str = cbor_field(2)
    net_version: int = cbor_field(3)
    net_build_version: str = cbor_field(4)


@dataclass
class GetPtpStatusResult:
    port_state: str = cbor_field(0)
    offset: int = cbor_field(1)
    delay: int = cbor_field(2)
    offset_histogram: list[int] = cbor_field(3)


@dataclass
class DfuPrepareArgs:
    size: int = cbor_field(0)


@dataclass
class DfuWriteArgs:
    offset: int = cbor_field(0)
    data: int = cbor_field(1)


@dataclass
class DfuApplyArgs:
    permanent: int = cbor_field(0)


@dataclass
class SetTimeArgs:
    time: int = cbor_field(0)


@dataclass
class GetTimeResult:
    time: int = cbor_field(0)


@dataclass
class NodeConnectedEvent(Notification):
    address: BluetoothAddrLE = cbor_field(0)


@dataclass
class NodeDisconnectedEvent(Notification):
    address: BluetoothAddrLE = cbor_field(0)


@dataclass
class NodeServiceDiscoveredEvent(Notification):
    address: BluetoothAddrLE = cbor_field(0)
    uuid: UUID = cbor_field(1)


@dataclass
class AVSSReportNotifiedEvent(Notification):
    address: BluetoothAddrLE = cbor_field(0)
    value: bytes = cbor_field(1)


@dataclass
class AVSSProgramNotifiedEvent(Notification):
    address: BluetoothAddrLE = cbor_field(0)
    value: bytes = cbor_field(1)


@dataclass
class ScanNodesReceivedEvent(Notification):
    address: BluetoothAddrLE = cbor_field(0)
    rssi: int = cbor_field(1)
    data: bytes = cbor_field(2)


class UnknownNotification(Notification):
    def __init__(self, notification_type, argument):
        self.notification_type = notification_type
        self.argument = argument
