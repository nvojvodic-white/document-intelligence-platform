"""The provider contract the sync engine is written against."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """Any failure reaching or reading from a datasource."""


@dataclass(frozen=True)
class ListedObject:
    """One object as the provider describes it, before anything is downloaded.

    etag / size / mtime together are the cheap-path fingerprint. When all three
    still match the recorded file row, the object is unchanged and is never
    downloaded - which is what makes a re-sync of an unchanged directory cost
    one LIST call and nothing else.
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
