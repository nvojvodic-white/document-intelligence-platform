"""Build a provider from a stored datasource row."""
from __future__ import annotations

from typing import Any

from core.storage.base import Provider, ProviderError
from core.storage.s3 import S3Provider

KINDS = ("s3",)


def get_provider(datasource: dict[str, Any]) -> Provider:
    """Instantiate the provider for a datasource row.

    Takes the whole row rather than a kind plus config so callers cannot
    assemble a provider for settings that were never persisted against a user's
    datasource.
    """
    kind = datasource.get("kind")
    if kind != "s3":
        raise ProviderError(
            f"unsupported datasource kind {kind!r}; supported: {', '.join(KINDS)}"
        )
    config = datasource.get("config") or {}
    bucket = config.get("bucket")
    if not bucket:
        raise ProviderError("datasource config is missing 'bucket'")
    return S3Provider(
        bucket=bucket,
        endpoint_url=config.get("endpoint_url"),
        region=config.get("region", "us-east-1"),
        secret_ref=datasource.get("secret_ref"),
    )
