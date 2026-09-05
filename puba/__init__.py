# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""puba — single-paper bibliographic resolution and markdown rendering."""
try:
    from ._version import version as __version__
except ImportError:
    from importlib.metadata import PackageNotFoundError, version as _dist_version

    try:
        __version__ = _dist_version("puba")
    except PackageNotFoundError:
        __version__ = "0.0.0.dev0+unknown"
