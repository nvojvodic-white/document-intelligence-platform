"""Datasource providers.

One provider today (S3, pointed at LocalStack in compose and at real S3 by
changing an endpoint URL). Google Drive and Azure Blob are named cuts.

The seam is here rather than the implementations: everything above this package
speaks in ListedObject and the four methods on Provider, so adding a provider
means writing one class, not touching the sync engine. That is worth the small
abstraction now precisely because the other providers are cut - it keeps the
cut reversible.
"""
from core.storage.base import ListedObject, Provider, ProviderError
from core.storage.factory import get_provider

__all__ = ["ListedObject", "Provider", "ProviderError", "get_provider"]
