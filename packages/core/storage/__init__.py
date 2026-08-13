"""Datasource providers. S3 today; Drive and Azure Blob are cuts.

Everything above this package speaks in ListedObject and the four Provider
methods, so adding a provider means writing one class rather than touching the
sync engine - which is what keeps the cut reversible.
"""
from core.storage.base import ListedObject, Provider, ProviderError
from core.storage.factory import get_provider

__all__ = ["ListedObject", "Provider", "ProviderError", "get_provider"]
