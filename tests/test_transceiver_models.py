import pytest

from pyanura.marshalling import unmarshal
from pyanura.transceiver.models import GetDeviceInfoResult, GetFirmwareInfoResult


def test_unmarshal_GetFirmwareInfoResult_firmware_pn():
    fields = {0: 0, 1: 0x1A090100, 2: "v26.9.1", 3: 0x1A090100, 4: "v26.9.1"}
    assert unmarshal(GetFirmwareInfoResult, fields).firmware_pn is None
    info = unmarshal(GetFirmwareInfoResult, {**fields, 5: "70020"})
    assert info.firmware_pn == "70020"


@pytest.mark.parametrize(
    "dfu_status, confirmed", [(0, True), (1, False), (0b10, True), (0b11, False)]
)
def test_GetFirmwareInfoResult_app_confirmed(dfu_status, confirmed):
    fields = {0: dfu_status, 1: 0x1A090100, 2: "v26.9.1", 3: 0x1A090100, 4: "v26.9.1"}
    info = unmarshal(GetFirmwareInfoResult, fields)
    assert info.app_confirmed is confirmed


def test_unmarshal_GetDeviceInfoResult_device_pn():
    fields = {
        0: "anura_transceiver",
        1: 1,
        2: bytes(8),
        3: "v26.9.1",
        4: "v26.9.1",
        5: "AN10070A1001-00007",
        6: "anura-tx",
        7: bytes(6),
        8: [],
    }
    assert unmarshal(GetDeviceInfoResult, fields).device_pn is None
    info = unmarshal(GetDeviceInfoResult, {**fields, 9: "10070"})
    assert info.device_pn == "10070"
