import cbor2
import dataclasses
from io import BytesIO
from typing import List
import typing_inspect

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
            obj[key] = getattr(inst, f.name)
        encoder.encode(obj)

def _to_cbor(self):
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

@classmethod
def _from_struct(cls, obj):
    return _decode_type(cls, obj)

@classmethod
def _from_cbor(cls, s):
    return _decode_type(cls, cbor2.loads(s))

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
