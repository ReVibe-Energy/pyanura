import importlib
import subprocess
import sys
import textwrap

import pytest

import pyanura.avss
import pyanura.avss.client


def test_alias_resolves_to_same_modules():
    with pytest.warns(DeprecationWarning, match="pyanura"):
        importlib.reload(importlib.import_module("anura"))

    import anura.avss.client  # pyright: ignore[reportMissingImports]
    from anura.avss import AVSSClient  # pyright: ignore[reportMissingImports]

    assert anura.avss is pyanura.avss
    assert anura.avss.client is pyanura.avss.client
    assert AVSSClient is pyanura.avss.AVSSClient
    assert pyanura.avss.__spec__.name == "pyanura.avss"


def test_alias_in_fresh_interpreter():
    # Importing the old name first must not leave pyanura with alias specs.
    code = textwrap.dedent(
        """
        import warnings
        warnings.simplefilter("error")
        try:
            import anura.dfu.bundle
        except DeprecationWarning:
            pass
        warnings.simplefilter("ignore")
        import anura.dfu.bundle
        import anura
        import pyanura.dfu.bundle
        assert anura.dfu.bundle is pyanura.dfu.bundle
        assert pyanura.dfu.bundle.__spec__.name == "pyanura.dfu.bundle"
        assert anura.marshalling is __import__("pyanura.marshalling").marshalling
        try:
            anura.nonexistent
        except AttributeError:
            pass
        else:
            raise AssertionError
        """
    )
    subprocess.run([sys.executable, "-c", code], check=True)
