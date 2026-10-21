"""Deprecated alias for :mod:`pyanura`.

The library used to be imported as ``anura``, which clashes with an
unrelated package of the same name on PyPI. ``import anura.avss`` and the
like keep working for now, but resolve to the very same module objects as
``pyanura.avss``, so classes, exceptions and monkeypatching behave
identically under either name. This shim will be removed in a future
release.
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types
import warnings

warnings.warn(
    "The 'anura' import name is deprecated and will be removed in a future"
    " release; import 'pyanura' instead.",
    DeprecationWarning,
    stacklevel=2,
)


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        module = importlib.import_module(self._target)
        self._spec = module.__spec__
        return module

    def exec_module(self, module: types.ModuleType) -> None:
        # The import system overwrites __spec__ on the module returned by
        # create_module; put back the real one so pyanura is unaffected.
        module.__spec__ = self._spec


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith(__name__ + "."):
            return None
        alias = "pyanura" + fullname[len(__name__) :]
        return importlib.util.spec_from_loader(fullname, _AliasLoader(alias))


if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())


def __getattr__(name: str) -> types.ModuleType:
    if importlib.util.find_spec(f"pyanura.{name}") is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return importlib.import_module(f"{__name__}.{name}")
