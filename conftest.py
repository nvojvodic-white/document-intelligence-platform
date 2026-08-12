"""Put the two import roots on sys.path for local test runs.

The Dockerfile and CI both set PYTHONPATH, but the separator is platform
specific (':' on Linux, ';' on Windows), so a contributor running pytest
straight from a clone on Windows would get ModuleNotFoundError from an
otherwise correct checkout. Resolving the roots here means `pytest` works from
the repo root on any platform with no environment setup.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
for _root in ("services/api", "packages"):
    _path = str(ROOT / _root)
    if _path not in sys.path:
        sys.path.insert(0, _path)
