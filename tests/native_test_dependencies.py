"""Scoped Windows dependency search for explicitly selected native test modules.

Test-only: no PATH mutation, recursive search, SDK initialization or module fallback.
The directory handle stays open for the whole case, including worker cleanup.
"""

from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path


@contextmanager
def native_test_dll_directory(path: str):
    module = Path(path).resolve(strict=True)
    if not module.is_file():
        raise ValueError("explicit native test module must be a file")
    if os.name == "nt":
        with os.add_dll_directory(str(module.parent)):
            yield
    else:
        yield


def explicit_native_dependencies(case):
    @wraps(case)
    def scoped(path: str, *args, **kwargs):
        with native_test_dll_directory(path):
            return case(path, *args, **kwargs)

    return scoped
