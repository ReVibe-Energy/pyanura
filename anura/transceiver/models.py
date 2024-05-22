import binascii
from dataclasses import dataclass
import ipaddress
from anura.dataclasses_cbor import (dataclass_cbor, field)
import re
import types
from uuid import UUID

msg_type = types.SimpleNamespace()
msg_type.Request = 0
msg_type.Response = 1
msg_type.Notification = 2

class Notification:
    def parse(notification_type, argument):
        event_classes = {
            "node_connected": NodeConnectedEvent,
            "node_disconnected": NodeDisconnectedEvent,
            "node_service_discovered": NodeServiceDiscoveredEvent,
            "node_service_discovered": NodeServiceDiscoveredEvent,
            "avss_report_notified": AVSSReportNotifiedEvent,
            "avss_program_notified": AVSSProgramNotifiedEvent,
            "scan_nodes_received": ScanNodesReceivedEvent,
        }
        if event_class := event_classes.get(notification_type):
            return event_class.from_struct(argument)
        else:
            return UnknownNotification(notification_type, argument)

@dataclass_cbor()
@dataclass
class APIError:
    code : int = field(0)
    internal_code : int = field(1)
    message : str = field(2)

@dataclass_cbor(struct="array")
@dataclass
class BluetoothAddrLE:
    type: int = field(0)
    address: bytes = field(1)

    def __str__(self):
        if self.type == 0:
            type_str = "public"
        else:
            type_str = "random"
        addr_str = binascii.hexlify(self.address, ":").decode("utf-8").upper()
        return f"{addr_str}/{type_str}"

    def __repr__(self):
        return f"BluetoothAddrLE(\"{self.__str__()}\")"

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

        addr_str = addr_raw.replace(':', '').replace('-', '')
        return BluetoothAddrLE(type=addr_type, address=bytes.fromhex(addr_str))

@dataclass_cbor()
@dataclass
class AssignedNode:
    address: BluetoothAddrLE = field(0)

@dataclass_cbor()
@dataclass
class SetAssignedNodesArgs:
    nodes: list[AssignedNode] = field(0)

@dataclass_cbor()
@dataclass
class GetAssignedNodesResult:
    nodes: list[AssignedNode] = field(0)

@dataclass_cbor()
@dataclass
class ConnectedNode:
    address: BluetoothAddrLE = field(0)
    rssi: int = field(1)

@dataclass_cbor()
@dataclass
class GetConnectedNodesResult:
    nodes: list[ConnectedNode] = field(0)

@dataclass_cbor()
@dataclass
class AVSSRequestArgs():
    address: BluetoothAddrLE = field(0)
    data: bytes = field(1)

@dataclass_cbor()
@dataclass
class AVSSProgramWriteArgs():
    address: BluetoothAddrLE = field(0)
    data: bytes = field(1)

@dataclass_cbor()
@dataclass
class GetDeviceInfoResult():
    board: str = field(0)
    hw_rev: int = field(1)
    device_id: bytes = field(2)
    app_version: str = field(3)
    app_build_version: str = field(4)
    serial_number: str = field(5)
    hostname: str = field(6)
    mac_address: bytes = field(7)
    ip_addresses: list[ipaddress.IPv4Address] = field(8)

@dataclass_cbor()
@dataclass
class GetDeviceStatusResult():
    uptime: int = field(0)
    reboot_count: int = field(1)
    reset_cause: int = field(2)

@dataclass_cbor()
@dataclass
class GetPtpStatusResult():
    port_state: str = field(0)
    offset: str = field(1)
    delay: str = field(2)
    offset_histogram: [int] = field(3)

@dataclass_cbor()
@dataclass
class DfuPrepareArgs():
    size: int = field(0)

@dataclass_cbor()
@dataclass
class DfuWriteArgs():
    offset: int = field(0)
    data: int = field(1)

@dataclass_cbor()
@dataclass
class DfuApplyArgs():
    permanent: int = field(0)

@dataclass_cbor()
@dataclass
class SetTimeArgs():
    time: int = field(0)

@dataclass_cbor()
@dataclass
class GetTimeResult():
    time: int = field(0)

@dataclass_cbor()
@dataclass
class NodeConnectedEvent(Notification):
    address: BluetoothAddrLE = field(0)

@dataclass_cbor()
@dataclass
class NodeDisconnectedEvent(Notification):
    address: BluetoothAddrLE = field(0)

@dataclass_cbor()
@dataclass
class NodeServiceDiscoveredEvent(Notification):
    address: BluetoothAddrLE = field(0)
    uuid: UUID = field(1)

@dataclass_cbor()
@dataclass
class AVSSReportNotifiedEvent(Notification):
    address: BluetoothAddrLE = field(0)
    value: bytes = field(1)

@dataclass_cbor()
@dataclass
class AVSSProgramNotifiedEvent(Notification):
    address: BluetoothAddrLE = field(0)
    value: bytes = field(1)

@dataclass_cbor()
@dataclass
class ScanNodesReceivedEvent(Notification):
    address: BluetoothAddrLE = field(0)
    rssi: int = field(1)
    data: bytes = field(2)

class UnknownNotification(Notification):
    def __init__(self, notification_type, argument):
        self.notification_type = notification_type
        self.argument = argument
