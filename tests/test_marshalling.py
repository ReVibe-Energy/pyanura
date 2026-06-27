from dataclasses import dataclass
from typing import Annotated

import pytest

from anura.marshalling import CborKey, unmarshal


def test_unmarshal_dataclass_unknown_key():
    @dataclass
    class ClassWithAField:
        field_with_key_0: Annotated[int, CborKey(0)]

    # An unknown key (1) is present
    unmarshal(ClassWithAField, {0: 0, 1: 0})


def test_unmarshal_field_order_independence():
    """
    Test that unmarshal works independent of dataclass field definition order.
    """

    @dataclass
    class FooBar:
        foo: Annotated[int, CborKey(0)]
        bar: Annotated[bool, CborKey(1)]

    @dataclass
    class BarFoo:
        bar: Annotated[bool, CborKey(1)]
        foo: Annotated[int, CborKey(0)]

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
        optional: Annotated[int, CborKey(0)] = 100

    # Optional field gets default value when unspecified.
    assert unmarshal(ClassWithOptionalField, {}).optional == 100

    # Optional field gets specified value if given.
    assert unmarshal(ClassWithOptionalField, {0: 200}).optional == 200


def test_unmarshal_dataclass_required_field():
    @dataclass
    class ClassWithRequiredField:
        required: Annotated[int, CborKey(0)]

    # No error
    unmarshal(ClassWithRequiredField, {0: 100})

    with pytest.raises(TypeError):
        unmarshal(ClassWithRequiredField, {})


def test_unmarshal_dataclass_recursive():

    @dataclass
    class InnerClass:
        a: Annotated[int, CborKey(0)]

    @dataclass
    class OuterClass:
        inner: Annotated[InnerClass, CborKey(0)]

    outer = unmarshal(OuterClass, {0: {0: 1}})

    assert isinstance(outer.inner, InnerClass)
