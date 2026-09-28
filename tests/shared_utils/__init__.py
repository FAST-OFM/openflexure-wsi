"""Shared utilities for all test suites."""

import functools


def get_method_name(method):
    """Reliably get a methods name even if wrapped."""
    while isinstance(method, functools.partial):
        method = method.func
    return getattr(method, "__name__", type(method).__name__)
