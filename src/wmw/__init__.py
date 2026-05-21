"""wmw — Wild Microbiome Watch."""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.5.1"

try:
    from importlib.metadata import version, PackageNotFoundError
    __version__ = version("wmw")
except Exception:
    pass  # fall back to the hardcoded value above
