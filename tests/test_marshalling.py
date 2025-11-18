from dataclasses import dataclass

import pytest

from anura.marshalling import cbor_field, unmarshal


def test_unmarshal_dataclass_unknown_key():
    @dataclass
    class ClassWithAField:
        field_with_key_0: int = cbor_field(0)

    # An unknown key (1) is present
    unmarshal(ClassWithAField, {0: 0, 1: 0})


def test_unmarshal_field_order_independence():
    """
    Test that unmarshal works independent of dataclass field definition order.
    """

    @dataclass
    class FooBar:
        foo: int = cbor_field(0)
        bar: bool = cbor_field(1)

    @dataclass
    class BarFoo:
        bar: bool = cbor_field(1)
        foo: int = cbor_field(0)

    test_data = {
        0: 100,
        1: True,
    }
    a = unmarshal(FooBar, test_data)
    b = unmarshal(BarFoo, test_data)

    assert a.foo == b.foo
    assert a.bar == b.bar


def test_unmarshal_dataclass_requires_dict():
    """
    Test that unmarshal of a dataclass requires a dict as input.
    """

    @dataclass
    class Foo:
        pass

    # No error
    unmarshal(Foo, {})

    with pytest.raises(ValueError):
        unmarshal(Foo, [])


def test_unmarshal_dataclass_optional_field():
    @dataclass
    class ClassWithOptionalField:
        optional: int = cbor_field(0, default=100)

    # Optional field gets default value when unspecified.
    assert unmarshal(ClassWithOptionalField, {}).optional == 100

    # Optional field gets specified value if given.
    assert unmarshal(ClassWithOptionalField, {0: 200}).optional == 200


def test_unmarshal_dataclass_required_field():
    @dataclass
    class ClassWithRequiredField:
        required: int = cbor_field(0)

    # No error
    unmarshal(ClassWithRequiredField, {0: 100})

    with pytest.raises(TypeError):
        unmarshal(ClassWithRequiredField, {})
