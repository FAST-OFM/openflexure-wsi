"""Utilities for testing and debugging.

At the top level are some basic testing functions. More specific testing utilities are
provided by modules inside the package.
"""

from collections.abc import Hashable
from typing import Iterable, Protocol


class SizedIterableHashable(Iterable[Hashable], Protocol):
    """A protocol for sized iterable of hashable objects."""

    def __len__(self) -> int:
        """Add a len function to protocol so Python knows the object is sized."""


def assert_unique_of_length(data: SizedIterableHashable, length: int) -> None:
    """Assert that a list (or other iterable) has unique contents of a given length.

    :param data: A list or other sized iterable of hashable objects. To be checked
    for unique contents and length.
    :param length: The expected length of data
    """
    assert len(data) == len(set(data))
    assert len(data) == length
