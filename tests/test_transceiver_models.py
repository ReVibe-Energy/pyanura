from anura.marshalling import unmarshal
from anura.transceiver.models import GetFirmwareInfoResult


def test_unmarshal_GetFirmwareInfoResult_firmware_pn():
    fields = {0: 0, 1: 0x1A090100, 2: "v26.9.1", 3: 0x1A090100, 4: "v26.9.1"}
    assert unmarshal(GetFirmwareInfoResult, fields).firmware_pn is None
    info = unmarshal(GetFirmwareInfoResult, {**fields, 5: "70020"})
    assert info.firmware_pn == "70020"
