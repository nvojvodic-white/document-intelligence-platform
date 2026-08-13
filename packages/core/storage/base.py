"""The provider contract the sync engine is written against."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """Any failure reaching or reading from a datasource."""


@dataclass(frozen=True)
class ListedObject:
    """One object as the provider describes it, pre-download.

    etag/size/mtime together are the cheap-path fingerprint: all three matching
    the recorded row means the object is never downloaded.
    """

    key: str
    etag: str | None
    size: int | None
    mtime: float | None


class Provider(Protocol):
    def check(self) -> None:
        """Raise ProviderError if the datasource is unreachable or denied."""

    def list_directories(self, prefix: str = "") -> list[str]:
        """Immediate child prefixes of `prefix`, for browsing."""

    def list_objects(self, prefix: str) -> list[ListedObject]:
        """Every object under `prefix`, recursively."""

    def fetch(self, key: str) -> bytes:
        """Download one object's raw bytes."""
