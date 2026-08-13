"""S3 provider. Talks to LocalStack in compose and to real S3 unchanged - only
the endpoint and credentials differ.

Credentials never touch the database: secret_ref names an environment variable,
resolved here at use time.
"""
from __future__ import annotations

import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from core.storage.base import ListedObject, ProviderError


class S3Provider:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        secret_ref: str | None = None,
    ):
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.region = region
        self._secret_ref = secret_ref
        self._client = None

    # --- credentials --------------------------------------------------------

    def _credentials(self) -> dict[str, str]:
        """Resolve credentials by name from the environment, so a database dump
        carries no secret. Without a ref, boto3's own chain applies."""
        if not self._secret_ref:
            return {}
        secret = os.getenv(self._secret_ref)
        if not secret:
            raise ProviderError(
                f"datasource references credential {self._secret_ref!r}, but "
                "that variable is not set in this process"
            )
        access_key = os.getenv(f"{self._secret_ref}_ID") or os.getenv(
            "AWS_ACCESS_KEY_ID"
        )
        if not access_key:
            raise ProviderError(
                f"no access key id found for credential {self._secret_ref!r}"
            )
        return {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret,
        }

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                region_name=self.region,
                # LocalStack does not serve virtual-host style bucket URLs.
                config=Config(
                    s3={"addressing_style": "path"},
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=5,
                    read_timeout=30,
                ),
                **self._credentials(),
            )
        return self._client

    # --- provider contract --------------------------------------------------

    def check(self) -> None:
        try:
            self._get_client().head_bucket(Bucket=self.bucket)
        except (ClientError, BotoCoreError) as e:
            raise ProviderError(f"cannot reach bucket {self.bucket!r}: {e}") from e

    def list_directories(self, prefix: str = "") -> list[str]:
        """Immediate child prefixes. Delimiter keeps a large bucket from being
        listed in full just to render one level."""
        prefix = _normalise_prefix(prefix)
        try:
            paginator = self._get_client().get_paginator("list_objects_v2")
            out: list[str] = []
            for page in paginator.paginate(
                Bucket=self.bucket, Prefix=prefix, Delimiter="/"
            ):
                for cp in page.get("CommonPrefixes", []):
                    out.append(cp["Prefix"])
            return sorted(out)
        except (ClientError, BotoCoreError) as e:
            raise ProviderError(f"listing {prefix!r} failed: {e}") from e

    def list_objects(self, prefix: str) -> list[ListedObject]:
        prefix = _normalise_prefix(prefix)
        try:
            paginator = self._get_client().get_paginator("list_objects_v2")
            out: list[ListedObject] = []
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Directory placeholders, not documents.
                    if key.endswith("/"):
                        continue
                    mtime = obj.get("LastModified")
                    out.append(
                        ListedObject(
                            key=key,
                            # Quotes are wire format, not value.
                            etag=(obj.get("ETag") or "").strip('"') or None,
                            size=obj.get("Size"),
                            mtime=mtime.timestamp() if mtime else None,
                        )
                    )
            return sorted(out, key=lambda o: o.key)
        except (ClientError, BotoCoreError) as e:
            raise ProviderError(f"listing {prefix!r} failed: {e}") from e

    def fetch(self, key: str) -> bytes:
        try:
            return self._get_client().get_object(Bucket=self.bucket, Key=key)[
                "Body"
            ].read()
        except (ClientError, BotoCoreError) as e:
            raise ProviderError(f"fetching {key!r} failed: {e}") from e


def _normalise_prefix(prefix: str) -> str:
    """Trim a leading slash, ensure a trailing one.

    Without the trailing slash a prefix matches siblings by string - 'alice/lo'
    would match 'alice/lore-backup/' and pull in a neighbour's objects.
    """
    prefix = (prefix or "").lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix
