import dataclasses
import functools
import ipaddress
import types
from collections.abc import Callable
from dataclasses import is_dataclass
from typing import (
    Annotated,
    Any,
    TypeVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

import cbor2

T = TypeVar("T")


class CborKey:
    """Annotation marking the CBOR integer key for a dataclass field.

    Used as ``Annotated[int, CborKey(0)]`` so the field keeps its real type
    for static checkers while carrying its wire key as metadata.
    """

    __slots__ = ("key",)

    def __init__(self, key: int):
        self.key = key


@dataclasses.dataclass(frozen=True)
class Codec:
    """A custom (un)marshaller for a type whose wire shape isn't the default
    record rule."""

    marshal: Callable[[Any], Any]
    unmarshal: Callable[[Any], Any]


# The only place the core knows about specific types. Starts empty; modules
# register their exceptions at import time via `register_codec`.
_codecs: dict[type, Codec] = {}


def register_codec(
    tp: type,
    *,
    marshal: Callable[[Any], Any],
    unmarshal: Callable[[Any], Any],
) -> None:
    """Register a custom codec for a type whose wire shape differs from the
    default dataclass record rule (e.g. a leaf type, or a dataclass encoded as
    a positional array)."""
    _codecs[tp] = Codec(marshal, unmarshal)


@functools.cache
def _field_keys(cls: type) -> dict[str, tuple[int, Any]]:
    """Map ``{field_name: (cbor_key, resolved_type)}`` for a model dataclass.

    Resolves annotations (so string/forward-ref types work) and reads the
    `CborKey` out of each `Annotated` field. Cached per class.
    """
    annotated = get_type_hints(cls, include_extras=True)
    resolved = get_type_hints(cls)  # Annotated stripped — the type to recurse on
    out: dict[str, tuple[int, Any]] = {}
    for field in dataclasses.fields(cls):
        hint = annotated.get(field.name)
        if get_origin(hint) is Annotated:
            key = next(
                (m.key for m in get_args(hint)[1:] if isinstance(m, CborKey)), None
            )
            if key is not None:
                out[field.name] = (key, resolved[field.name])
    return out


def marshal(obj: Any) -> dict | list | Any:
    """Convert an object representation of a message or data type to a
    structure consisting of dicts, lists and primitive types."""
    if codec := _codecs.get(type(obj)):
        return codec.marshal(obj)
    elif is_dataclass(obj) and not isinstance(obj, type):
        return {
            key: marshal(getattr(obj, name))
            for name, (key, _) in _field_keys(type(obj)).items()
        }
    elif isinstance(obj, list):
        return [marshal(v) for v in obj]
    elif isinstance(obj, dict):
        return {marshal(k): marshal(v) for k, v in obj.items()}
    else:
        return obj


def unmarshal(cls: type[T], struct: Any) -> T:
    if codec := _codecs.get(cls):
        return codec.unmarshal(struct)
    elif is_dataclass(cls):
        if not isinstance(struct, dict):
            raise ValueError(
                f"Expected dict for dataclass {cls.__name__}, "
                f"got {type(struct).__name__}"
            )
        attributes = {
            name: unmarshal(field_type, struct[key])
            for name, (key, field_type) in _field_keys(cls).items()
            if key in struct
        }
        return cast(T, cls(**attributes))
    elif isinstance(cls, types.UnionType):
        match get_args(cls):
            case inner_cls, types.NoneType:
                return cast(T, unmarshal(inner_cls, struct))
            case _:
                # In principle this could be extended, but `SomeClass | None`
                # is enough for our use case.
                raise ValueError(f"Unsupported union type: {cls}")
    elif isinstance(cls, types.GenericAlias):
        origin = get_origin(cls)
        if origin is list:
            item_cls = get_args(cls)[0]
            return cast(T, [unmarshal(item_cls, v) for v in struct])
        elif origin is dict:
            key_cls, val_cls = get_args(cls)
            return cast(
                T,
                {unmarshal(key_cls, k): unmarshal(val_cls, v) for k, v in struct.items()},
            )
        else:
            raise ValueError("Unsupported generic type.")
    else:
        if not isinstance(struct, cls):
            raise TypeError(f"{struct!r} not decodable as type {cls}")
        return struct


def _marshal_ipv4address(addr: ipaddress.IPv4Address) -> cbor2.CBORTag:
    return cbor2.CBORTag(52, addr.packed)


def _unmarshal_ipv4address(struct: Any) -> ipaddress.IPv4Address:
    if not isinstance(struct, cbor2.CBORTag):
        raise TypeError(f"{struct!r} not decodable as IPv4Address")
    if struct.tag != 52:
        raise ValueError(f"Expected tag 52 but got {struct.tag}")
    return ipaddress.IPv4Address(struct.value)


register_codec(
    ipaddress.IPv4Address,
    marshal=_marshal_ipv4address,
    unmarshal=_unmarshal_ipv4address,
)
