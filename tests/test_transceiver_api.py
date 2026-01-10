from anura.marshalling import unmarshal
from anura.transceiver.models import (
    BluetoothAddrLE,
    ConnectedNode,
)


def test_unmarshal_connected_node_without_ready_field():
    """
    Test that ConnectedNode prior to the addition of the
    ready field can be unmarshaled.
    """
    # Old format: only address (key 0) and rssi (key 1)
    data = {
        0: [0, b"\xaa\xbb\xcc\xdd\xee\xff"],  # address
        1: -65,  # rssi
    }

    node = unmarshal(ConnectedNode, old_format_data)

    assert isinstance(node.address, BluetoothAddrLE)
    assert node.address.type == 0
    assert node.address.address == b"\xaa\xbb\xcc\xdd\xee\xff"
    assert node.rssi == -65
    assert node.ready is None  # Should default to None


def test_unmarshal_connected_node():
    """
    Test that ConnectedNode can be unmarshaled.
    """
    # Current format: address (key 0), rssi (key 1), and ready (key 2)
    new_format_data = {
        0: [0, b"\xaa\xbb\xcc\xdd\xee\xff"],  # address
        1: -65,  # rssi
        2: True,  # ready
    }

    node = unmarshal(ConnectedNode, new_format_data)

    assert isinstance(node.address, BluetoothAddrLE)
    assert node.address.type == 0
    assert node.address.address == b"\xaa\xbb\xcc\xdd\xee\xff"
    assert node.rssi == -65
    assert node.ready is True
