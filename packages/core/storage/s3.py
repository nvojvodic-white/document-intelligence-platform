"""S3 provider.

Talks to LocalStack in compose and to real S3 unchanged - only the endpoint URL
and credentials differ, which is the point of using S3 for the demo rather than
a mock provider reading local files.

Credentials are never stored in the database. `secret_ref` on the datasource
row names an environment variable; it is resolved here, at use time, in the
process that needs it.
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
        """Resolve credentials by reference, from the environment.

        A datasource row records the NAME of a credential, never its value, so
        a database dump - or an API response that accidentally serialises a
        datasource - carries no secret. When no ref is set, boto3's own chain
        applies (instance role, shared config, ambient env).
        """
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
                # Path addressing: LocalStack does not serve virtual-host style
                # bucket URLs on localhost.
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
        """Immediate child prefixes, for browsing one level at a time.

        Uses Delimiter so a bucket with a large tree is not listed in full just
        to render one level of a picker.
        """
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
                    # Directory placeholder objects, not documents.
                    if key.endswith("/"):
                        continue
                    mtime = obj.get("LastModified")
                    out.append(
                        ListedObject(
                            key=key,
                            # Quotes are part of the wire format, not the value.
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
    """Trim a leading slash and ensure a trailing one.

    S3 keys have no leading slash, but people type paths with one. A prefix
    that does not end in '/' would also match sibling keys by string prefix -
    'alice/lo' would match 'alice/lore-backup/' - so registering a directory
    could silently pull in a neighbour's objects.
    """
    prefix = (prefix or "").lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix
