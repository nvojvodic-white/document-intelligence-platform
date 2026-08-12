"""Datasources, browsing, directories, and sync triggering.

Every handler here takes `user_id: str = CurrentUser` and passes it as the
first argument to a repository call. No handler reads an owner from the request
body or from a path segment - a datasource id belonging to someone else reads
as 404, not 403, so ids do not leak existence.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core import repositories as repo
from core import vectors
from core.storage import ProviderError, get_provider

from app.deps import CurrentUser

router = APIRouter()


# --- shapes -----------------------------------------------------------------


class ConnectS3Request(BaseModel):
    name: str = Field(default="S3", max_length=100)
    bucket: str = Field(..., max_length=255)
    endpoint_url: str | None = Field(default=None, max_length=500)
    region: str = Field(default="us-east-1", max_length=64)
    # The NAME of an environment variable holding the secret, never the secret.
    secret_ref: str | None = Field(default=None, max_length=128)


class DatasourceOut(BaseModel):
    id: str
    kind: str
    name: str
    config: dict
    # secret_ref is echoed because it is a variable name, not a credential.
    secret_ref: str | None
    created_at: float


class RegisterDirectoryRequest(BaseModel):
    datasource_id: str = Field(..., max_length=64)
    path: str = Field(default="", max_length=1024)


def _public_datasource(row: dict) -> DatasourceOut:
    return DatasourceOut(**{k: row[k] for k in DatasourceOut.model_fields})


def _load_datasource_or_404(user_id: str, datasource_id: str) -> dict:
    ds = repo.get_datasource(user_id, datasource_id)
    if ds is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="datasource not found"
        )
    return ds


# --- datasources ------------------------------------------------------------


@router.post("/datasources", response_model=DatasourceOut, status_code=201)
def connect_s3(req: ConnectS3Request, user_id: str = CurrentUser) -> DatasourceOut:
    """Connect an S3 datasource, verifying reachability before recording it.

    The check happens first so a typo in the bucket name surfaces here rather
    than as a failed sync run ten minutes later.
    """
    config = {
        "bucket": req.bucket,
        "endpoint_url": req.endpoint_url or os.getenv("S3_ENDPOINT_URL"),
        "region": req.region,
    }
    probe = {
        "kind": "s3",
        "config": config,
        "secret_ref": req.secret_ref or os.getenv("S3_SECRET_REF"),
    }
    try:
        get_provider(probe).check()
    except ProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    created = repo.create_datasource(
        user_id,
        kind="s3",
        name=req.name,
        config=config,
        secret_ref=probe["secret_ref"],
    )
    return _public_datasource(created)


@router.get("/datasources", response_model=list[DatasourceOut])
def list_datasources(user_id: str = CurrentUser) -> list[DatasourceOut]:
    return [_public_datasource(d) for d in repo.list_datasources(user_id)]


@router.get("/datasources/{datasource_id}/browse")
def browse(
    datasource_id: str, path: str = "", user_id: str = CurrentUser
) -> dict:
    """List immediate child directories of `path`, one level at a time.

    Browsing hits the provider live rather than a cached tree: the point of the
    screen is to show what is actually there before a directory is registered.
    """
    ds = _load_datasource_or_404(user_id, datasource_id)
    try:
        provider = get_provider(ds)
        directories = provider.list_directories(path)
        objects = provider.list_objects(path)
    except ProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)
        ) from e

    # Only the objects sitting directly at this level. list_objects is
    # recursive, so without this the browser would show a directory's whole
    # subtree as if it were its own contents.
    normalised = path.lstrip("/")
    if normalised and not normalised.endswith("/"):
        normalised += "/"
    here = [o for o in objects if "/" not in o.key[len(normalised) :]]

    return {
        "path": path,
        "directories": directories,
        "files": [
            {"key": o.key, "size": o.size, "mtime": o.mtime} for o in here[:200]
        ],
        "truncated": len(here) > 200,
    }


# --- directories ------------------------------------------------------------


@router.post("/directories", status_code=201)
def register_directory(
    req: RegisterDirectoryRequest, user_id: str = CurrentUser
) -> dict:
    """Register a directory for syncing.

    Re-registering the same path returns the existing row with created=false
    rather than erroring, so a double submit cannot fork one directory into two.
    """
    _load_datasource_or_404(user_id, req.datasource_id)
    directory, created = repo.create_directory(user_id, req.datasource_id, req.path)
    return {"directory": directory, "created": created}


@router.get("/directories")
def list_directories(user_id: str = CurrentUser) -> dict:
    directories = repo.list_directories(user_id)
    for d in directories:
        latest = repo.list_runs(user_id, d["id"], limit=1)
        d["latest_run"] = latest[0] if latest else None
        d["file_count"] = len(repo.list_files(user_id, d["id"]))
    return {"directories": directories}


@router.get("/directories/{directory_id}/files")
def list_directory_files(directory_id: str, user_id: str = CurrentUser) -> dict:
    if repo.get_directory(user_id, directory_id) is None:
        raise HTTPException(status_code=404, detail="directory not found")
    return {"files": repo.list_files(user_id, directory_id)}


# --- sync -------------------------------------------------------------------


@router.post("/directories/{directory_id}/sync", status_code=202)
def trigger_sync(directory_id: str, user_id: str = CurrentUser) -> dict:
    """Queue a sync run.

    A second POST while a run is queued or running does not start a second run:
    the partial unique index rejects it and the caller is handed the run
    already in flight, with already_in_progress=true. The response is the same
    shape either way so the UI has no special case.
    """
    if repo.get_directory(user_id, directory_id) is None:
        raise HTTPException(status_code=404, detail="directory not found")
    run, created = repo.enqueue_run(user_id, directory_id)
    return {"run": run, "already_in_progress": not created}


@router.get("/directories/{directory_id}/runs")
def list_runs(directory_id: str, limit: int = 10, user_id: str = CurrentUser) -> dict:
    if repo.get_directory(user_id, directory_id) is None:
        raise HTTPException(status_code=404, detail="directory not found")
    return {"runs": repo.list_runs(user_id, directory_id, limit=limit)}


@router.get("/runs/{run_id}")
def get_run(run_id: str, user_id: str = CurrentUser) -> dict:
    """Poll a run. Counters are read from the database, not from worker memory,
    so a refresh mid-run shows real progress and a dead worker leaves an honest
    partial count behind."""
    run = repo.get_run(user_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


# --- removal ----------------------------------------------------------------


@router.delete("/files/{file_id}")
def remove_file(file_id: str, user_id: str = CurrentUser) -> dict:
    """Remove a file from this user's knowledge base.

    Soft deletes the attribution row, then drops the content's vectors from
    this user's collection only if no other live row of theirs references the
    same bytes. The blob, its chunks, and the embedding cache all persist -
    they are content, not attribution. Answers stop citing the file at once.

    This is the same code path the sync worker uses for a file that disappeared
    at the source, so manual removal and source deletion cannot drift apart.
    """
    existing = repo.get_file(user_id, file_id)
    if existing is None or existing.get("deleted_at"):
        raise HTTPException(status_code=404, detail="file not found")

    repo.soft_delete_file(user_id, file_id)
    dropped = 0
    sha256 = existing.get("sha256")
    if sha256 and not repo.user_still_references_blob(user_id, sha256):
        dropped = vectors.drop_blob_for_user(user_id, sha256)
        repo.remove_user_blob(user_id, sha256)
    return {
        "file_id": file_id,
        "deleted": True,
        "vectors_dropped": dropped,
        "content_retained_for_other_references": bool(sha256) and dropped == 0,
    }
