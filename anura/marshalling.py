import dataclasses
import ipaddress
import logging
import types
import typing
from dataclasses import is_dataclass
from typing import Type, TypeVar

import cbor2

T = TypeVar("T")

logger = logging.getLogger(__name__)


def cbor_field(cbor_key, /, **kwargs):
    metadata = kwargs.setdefault("metadata", {})
    metadata["cbor_key"] = cbor_key
    return dataclasses.field(**kwargs)


def marshal(obj):
    """
    Convert an object representation of a message or data type to
    a structure consisting of dicts, lists and primitive types

    """
    if hasattr(obj, "_marshal"):
        return obj._marshal()
    elif is_dataclass(obj):
        return {
            field.metadata["cbor_key"]: marshal(getattr(obj, field.name))
            for field in dataclasses.fields(obj)
        }
    elif isinstance(obj, list):
        return [marshal(v) for v in obj]
    elif isinstance(obj, dict):
        return {marshal(k): marshal(v) for k, v in obj.items()}
    else:
        return obj


def unmarshal(cls: type[T], struct) -> T:
    if hasattr(cls, "_unmarshal"):
        return getattr(cls, "_unmarshal")(struct)
    elif hook := _unmarshal_hooks.get(cls, None):
        return hook(cls, struct)
    elif is_dataclass(cls):
        field_by_key = {
            field.metadata["cbor_key"]: field for field in dataclasses.fields(cls)
        }
        attributes = [
            unmarshal(field.type, v)
            for k, v in struct.items()
            if (field := field_by_key.get(k, None))
        ]
        return cls(*attributes)
    elif isinstance(cls, types.UnionType):
        match typing.get_args(cls):
            case inner_cls, types.NoneType:
                return unmarshal(inner_cls, struct)
            case _:
                # In principle this could be extended, but `SomeClass | None`
                # is enough for our use case.
                raise ValueError(f"Unsupported union type: {cls}")
    elif isinstance(cls, types.GenericAlias):
        origin = typing.get_origin(cls)
        if origin is list:
            item_cls = typing.get_args(cls)[0]
            return [unmarshal(item_cls, v) for v in struct]
        elif origin is dict:
            key_cls, val_cls = typing.get_args(cls)
            return {
                unmarshal(key_cls, k): unmarshal(val_cls, v) for k, v in struct.items()
            }
        else:
            raise ValueError("Unsupported generic type.")
    else:
        if not isinstance(struct, cls):
            raise TypeError(f"{repr(struct)} not decodable as type {cls}")
        return struct


def _unmarshal_ipv4address(cls: type[T], struct) -> T:
    if not isinstance(struct, cbor2.CBORTag):
        raise TypeError(f"{repr(struct)} not decodable as type {cls}")
    if struct.tag != 52:
        raise ValueError(f"Expected tag 52 but got {struct.tag}")
    return ipaddress.IPv4Address(struct.value)


_unmarshal_hooks = {
    ipaddress.IPv4Address: _unmarshal_ipv4address
}
