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
    get_args,
    get_origin,
    get_type_hints,
    overload,
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


def _is_optional(tp: Any) -> bool:
    """True for ``X | None`` field types. Such fields are omitted from the
    encoded map when unset, so that a peer whose schema predates the field
    (or that expects a value rather than nil) still accepts the message."""
    return isinstance(tp, types.UnionType) and types.NoneType in get_args(tp)


def marshal(obj: Any) -> dict | list | Any:
    """Convert an object representation of a message or data type to a
    structure consisting of dicts, lists and primitive types."""
    if obj is None:
        # None stands for the absence of a value, which is expressed by leaving
        # an optional field out, never by encoding. A type that has a meaning
        # for CBOR null on the wire gets its own sentinel with a codec.
        raise TypeError("None cannot be marshalled")
    elif codec := _codecs.get(type(obj)):
        return codec.marshal(obj)
    elif is_dataclass(obj) and not isinstance(obj, type):
        out = {}
        for name, (key, field_type) in _field_keys(type(obj)).items():
            value = getattr(obj, name)
            if value is None and _is_optional(field_type):
                continue  # unset optional field: leave the key out
            out[key] = marshal(value)
        return out
    elif isinstance(obj, list):
        return [marshal(v) for v in obj]
    elif isinstance(obj, dict):
        return {marshal(k): marshal(v) for k, v in obj.items()}
    else:
        return obj


@overload
def unmarshal(cls: type[T], struct: Any) -> T: ...
@overload
def unmarshal(cls: types.UnionType | types.GenericAlias, struct: Any) -> Any: ...
def unmarshal(cls: Any, struct: Any) -> Any:
    """Decode ``struct`` as ``cls``.

    ``cls`` is a class, a union such as ``int | Unlimited``, or a
    parameterised ``list``/``dict``. Unions and generic aliases are not
    ``type`` objects, so they get their own overload and return ``Any``.
    """
    if codec := _codecs.get(cls):
        return codec.unmarshal(struct)
    elif isinstance(cls, type) and is_dataclass(cls):
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
        return cls(**attributes)
    elif isinstance(cls, types.UnionType):
        return _unmarshal_union(get_args(cls), struct)
    elif isinstance(cls, types.GenericAlias):
        origin = get_origin(cls)
        if origin is list:
            item_cls = get_args(cls)[0]
            return [unmarshal(item_cls, v) for v in struct]
        elif origin is dict:
            key_cls, val_cls = get_args(cls)
            return {
                unmarshal(key_cls, k): unmarshal(val_cls, v) for k, v in struct.items()
            }
        else:
            raise ValueError("Unsupported generic type.")
    else:
        if not isinstance(struct, cls):
            raise TypeError(f"{struct!r} not decodable as type {cls}")
        return struct


def _unmarshal_union(members: tuple[type, ...], struct: Any) -> Any:
    """Decode as the first member type that accepts the value.

    Members are tried in declaration order, so put the more specific ones
    first when they overlap. A ``None`` member only marks the field as
    optional; a null on the wire is not accepted for it.
    """
    for member in members:
        if member is types.NoneType:
            continue
        try:
            return unmarshal(member, struct)
        except (TypeError, ValueError):
            continue
    names = " | ".join(getattr(m, "__name__", repr(m)) for m in members)
    raise TypeError(f"{struct!r} not decodable as type {names}")


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
