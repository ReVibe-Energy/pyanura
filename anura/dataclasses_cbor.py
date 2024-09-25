import cbor2
import dataclasses
from io import BytesIO
from typing import List
import typing_inspect
import ipaddress

def _make_default_encoder(string_keys=False):
    def _default_encoder(encoder, inst):
        struct = type(inst)._dataclass_cbor_struct
        if struct == "array":
            indices = []
            for f in dataclasses.fields(inst):
                index = f.metadata.get("cbor_key", f.name)
                indices.append(index)
            max_index = max(indices)
            arr = [None] * (max_index + 1)
            for f in dataclasses.fields(inst):
                index = f.metadata.get("cbor_key", f.name)
                arr[index] = getattr(inst, f.name)
            encoder.encode(arr)
        else:
            obj = {}
            for f in dataclasses.fields(inst):
                key = f.metadata.get("cbor_key", f.name)
                key = str(key) if string_keys else key
                obj[key] = getattr(inst, f.name)
            encoder.encode(obj)
    
    return _default_encoder

def _to_cbor(self, string_keys=False):
    _default_encoder = _make_default_encoder(string_keys)
    with BytesIO() as fp:
        cbor2.CBOREncoder(
            fp,
            default=_default_encoder,
        ).encode(self)
        return fp.getvalue()

def _to_struct(self):
    # TODO: Consider changing this so _to_cbor is defined in
    # terms of _to_struct instead of the other way around
    return cbor2.loads(_to_cbor(self))

def _decode_type(type, obj):
    if typing_inspect.get_origin(type) is list:
        type_args = typing_inspect.get_args(type)
        return [_decode_type(type_args[0], x) for x in obj]

    if not dataclasses.is_dataclass(type):
        return obj

    init_args = {}
    for f in dataclasses.fields(type):
        key = f.metadata.get("cbor_key", f.name)
        if isinstance(obj, list):
            value = _decode_type(f.type, obj[key])
        else:
            value = _decode_type(f.type, obj.get(key))
        init_args[f.name] = value
    return type(**init_args)

# Tag hook for tags used throughout the Anura CBOR protocols
def _tag_hook(decoder, tag, shareable_index=None):
    # Tag 52 is a IANA registered tag but cbor2 does not handle it by default
    # so we provide an implementation here. Note that this implementation is
    # only partial but enough for our current needs.
    if tag.tag == 52:
        if isinstance(tag.value, bytes):
            return ipaddress.IPv4Address(tag.value)
        else:
            return tag.value
    return tag

@classmethod
def _from_struct(cls, obj):
    return _decode_type(cls, cbor2.loads(cbor2.dumps(obj), tag_hook=_tag_hook))

@classmethod
def _from_cbor(cls, s):
    return _decode_type(cls, cbor2.loads(s, tag_hook=_tag_hook))

def field(cbor_key):
    return dataclasses.field(metadata={"cbor_key": cbor_key})

def dataclass_cbor(struct = "map"):
    if struct != "map" and struct != "array":
        raise ValueError(f"invalid struct: {struct}")
    def decorator(cls):
        cls.to_cbor = _to_cbor
        cls.to_struct = _to_struct
        cls.from_cbor = _from_cbor
        cls.from_struct = _from_struct
        cls._dataclass_cbor_struct = struct
        return cls
    return decorator
