from dataclasses import dataclass

from anura.marshalling import unmarshal, cbor_field


def test_unmarshal_dataclass_unknown_key():
    @dataclass
    class ClassWithAField:
        field_with_key_0: int = cbor_field(0)

    # An unknown key (1) is present
    unmarshal(ClassWithAField, {0: 0, 1: 0})
