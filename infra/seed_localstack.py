"""Upload the committed corpus into LocalStack S3.

Runs once at boot as a compose service, then exits. Idempotent: re-running
re-uploads the same bytes to the same keys, which is a no-op as far as the
platform is concerned because identity is the sha256 of content.

Keys mirror the corpus tree, so corpus/tolkien/alice/lore/mithril.md becomes
s3://tolkien-corpus/alice/lore/mithril.md.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

BUCKET = os.getenv("S3_BUCKET", "tolkien-corpus")
ENDPOINT = os.getenv("S3_ENDPOINT_URL", "http://localstack:4566")
CORPUS = Path(os.getenv("CORPUS_DIR", "/app/corpus/tolkien"))
WAIT_SECONDS = int(os.getenv("SEED_WAIT_SECONDS", "60"))


def client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        config=Config(s3={"addressing_style": "path"}),
    )


def wait_for_localstack(s3) -> None:
    """Poll until S3 answers.

    compose's depends_on only waits for the container, not for the service
    inside it, and LocalStack takes a few seconds to become useful. Without
    this the seed races startup and fails on a cold machine.
    """
    deadline = time.time() + WAIT_SECONDS
    last = None
    while time.time() < deadline:
        try:
            s3.list_buckets()
            return
        except (ClientError, BotoCoreError, OSError) as e:
            last = e
            time.sleep(1)
    raise SystemExit(f"LocalStack did not become ready in {WAIT_SECONDS}s: {last}")


def main() -> int:
    if not CORPUS.is_dir():
        raise SystemExit(f"corpus directory not found: {CORPUS}")

    s3 = client()
    wait_for_localstack(s3)

    try:
        s3.create_bucket(Bucket=BUCKET)
        print(f"created bucket {BUCKET}")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
        print(f"bucket {BUCKET} already exists")

    count = 0
    for path in sorted(CORPUS.rglob("*.md")):
        key = path.relative_to(CORPUS).as_posix()
        s3.put_object(Bucket=BUCKET, Key=key, Body=path.read_bytes())
        count += 1
        print(f"  s3://{BUCKET}/{key}")

    print(f"seeded {count} objects into {BUCKET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
