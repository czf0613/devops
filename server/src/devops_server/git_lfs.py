"""Git LFS Batch + Basic transfer, using live Git OAuth and repository ACLs."""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictInt, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from devops_server.dependencies import Authenticated, Database
from devops_server.git_oauth import authenticate_git
from devops_server.models import Repository, User
from devops_server.repositories import repository_access, visible_repository
from devops_server.repository_code import repository_directory

router = APIRouter(tags=["git-lfs"])
MEDIA_TYPE = "application/vnd.git-lfs+json"
MAX_OBJECT_BYTES = 5 * 1024**3
OID_PATTERN = r"[0-9a-f]{64}"
PREFIX = "/git/{owner}/{name}.git/info/lfs"
MACHINE_PATH = re.compile(
    rf"/git/[a-z][a-z0-9_.-]{{2,31}}/[a-z0-9][a-z0-9._-]{{0,99}}\.git/info/lfs/"
    rf"(objects/batch|objects/{OID_PATTERN}(?:/verify)?|locks/verify)"
)


def machine_request(request: Request) -> bool:
    match = MACHINE_PATH.fullmatch(request.url.path)
    return bool(
        match
        and (
            (
                request.method == "POST"
                and (match[1] == "objects/batch" or match[1].endswith("/verify"))
            )
            or (request.method == "PUT" and re.fullmatch(rf"objects/{OID_PATTERN}", match[1]))
        )
    )


class LFSError(HTTPException):
    pass


class ObjectSpec(BaseModel):
    oid: Annotated[str, Field(pattern=rf"^{OID_PATTERN}$")]
    size: Annotated[StrictInt, Field(ge=0, le=MAX_OBJECT_BYTES)]


class Batch(BaseModel):
    operation: Literal["upload", "download"]
    transfers: list[str] = ["basic"]
    objects: Annotated[list[ObjectSpec], Field(min_length=1, max_length=100)]
    hash_algo: Literal["sha256"] = "sha256"


async def json_body(request: Request) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != MEDIA_TYPE:
        raise LFSError(415, "Expected application/vnd.git-lfs+json")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise LFSError(413, "LFS request is too large")
    try:
        import json

        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except ValueError, UnicodeError:
        raise LFSError(422, "Invalid LFS JSON") from None


JSONBody = Annotated[dict, Depends(json_body)]


def authorize(
    request: Request, db: Session, owner: str, name: str, *, write=False, lock=False
) -> Repository:
    try:
        grant = authenticate_git(request, db)
    except HTTPException as exc:
        headers = dict(exc.headers or {})
        if exc.status_code == 401:
            headers["LFS-Authenticate"] = 'Basic realm="DevOps Git LFS"'
        raise LFSError(exc.status_code, exc.detail, headers=headers) from None
    statement = (
        select(Repository)
        .join(User, User.id == Repository.owner_id)
        .where(User.username == owner, Repository.name == name, Repository.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(of=Repository)
    repository = db.scalar(statement)
    access = repository_access(db, repository, grant.user_id) if repository else None
    if access is None:
        raise LFSError(404, "Repository not found")
    if grant.client_id == "devops-ci":
        from devops_server.ci_service import authorize_task

        authorize_task(db, grant, repository.id, write=write)
    if f"git:{'write' if write else 'read'}" not in grant.scope.split() or (
        write and access != "write"
    ):
        raise LFSError(403, "Repository is read-only or authorization lacks the required scope")
    return repository


def object_path(root: str, repository_id: int, oid: str, *, create=False) -> Path:
    if not re.fullmatch(OID_PATTERN, oid):
        raise LFSError(422, "Invalid LFS object ID")
    directory = repository_directory(root, repository_id)
    try:
        for part in ("lfs", "objects", oid[:2], oid[2:4]):
            directory = directory / part
            if directory.is_symlink():
                raise LFSError(503, "LFS storage unavailable")
            if create:
                directory.mkdir(mode=0o750, exist_ok=True)
        target = directory / oid
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise LFSError(503, "LFS storage unavailable")
        return target
    except OSError:
        raise LFSError(503, "LFS storage unavailable") from None


def lfs_response(value: dict, status=200) -> JSONResponse:
    return JSONResponse(value, status_code=status, media_type=MEDIA_TYPE)


@router.post(PREFIX + "/objects/batch")
def batch(owner: str, name: str, body: JSONBody, request: Request, db: Database):
    try:
        spec = Batch.model_validate(body)
    except ValidationError:
        raise LFSError(422, "Invalid LFS batch; maximum 100 objects, 5 GiB per object") from None
    repository = authorize(request, db, owner, name, write=spec.operation == "upload")
    if "basic" not in spec.transfers:
        raise LFSError(422, "Only the basic transfer adapter is supported")
    # Uvicorn deliberately ignores proxy headers. Choose a configured origin whose
    # host matches the request; never build credential-bearing actions from a forged host.
    origin = next(
        (
            origin.rstrip("/")
            for origin in request.app.state.settings.allowed_origins
            if urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()
        ),
        None,
    )
    if origin is None:
        raise LFSError(400, "Unrecognized LFS host")
    base = f"{origin}/git/{owner}/{name}.git/info/lfs/objects"
    objects = []
    for item in spec.objects:
        target = object_path(
            request.app.state.settings.git_repositories_root, repository.id, item.oid
        )
        entry = item.model_dump()
        exists = target.is_file()
        if exists and target.stat().st_size != item.size:
            entry["error"] = {"code": 422, "message": "Object size does not match"}
        elif spec.operation == "download" and not exists:
            entry["error"] = {"code": 404, "message": "Object not found"}
        elif spec.operation == "download":
            entry["actions"] = {"download": {"href": f"{base}/{item.oid}"}}
        elif not exists:
            entry["actions"] = {
                "upload": {"href": f"{base}/{item.oid}?size={item.size}"},
                "verify": {"href": f"{base}/{item.oid}/verify"},
            }
        # No embedded token or signed URL: the client reuses GCM credentials and
        # every action revalidates expiry, revocation and current repository access.
        objects.append(entry)
    return lfs_response({"transfer": "basic", "objects": objects, "hash_algo": "sha256"})


@router.get(PREFIX + "/objects/{oid}")
def download(owner: str, name: str, oid: str, request: Request, db: Database):
    repository = authorize(request, db, owner, name)
    return file_response(request, repository.id, oid)


def file_response(request: Request, repository_id: int, oid: str) -> FileResponse:
    target = object_path(request.app.state.settings.git_repositories_root, repository_id, oid)
    if not target.is_file():
        raise LFSError(404, "LFS object not found")
    return FileResponse(target, media_type="application/octet-stream", filename=oid)


@router.get("/api/repositories/{repository_id}/lfs/{oid}")
def browser_download(
    repository_id: int, oid: str, request: Request, actor: Authenticated, db: Database
):
    visible_repository(db, repository_id, actor.user.id)
    return file_response(request, repository_id, oid)


def begin_upload(
    owner: str, name: str, oid: str, request: Request, db: Database
) -> tuple[int, Path]:
    repository = authorize(request, db, owner, name, write=True)
    target = object_path(
        request.app.state.settings.git_repositories_root, repository.id, oid, create=True
    )
    repository_id = repository.id
    # Avoid retaining a DB connection during a potentially long streaming upload.
    db.commit()
    return repository_id, target


Upload = Annotated[tuple[int, Path], Depends(begin_upload)]


@router.put(PREFIX + "/objects/{oid}")
async def upload(
    owner: str,
    name: str,
    oid: str,
    request: Request,
    db: Database,
    destination: Upload,
    size: Annotated[int, Query(ge=0, le=MAX_OBJECT_BYTES)],
):
    repository_id, target = destination
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".upload-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            checksum, count = hashlib.sha256(), 0
            async for chunk in request.stream():
                count += len(chunk)
                if count > size:
                    raise LFSError(422, "Uploaded size exceeds declared size")
                checksum.update(chunk)
                await run_in_threadpool(stream.write, chunk)
            if count != size or checksum.hexdigest() != oid:
                raise LFSError(422, "LFS object size or SHA-256 mismatch")
            await run_in_threadpool(stream.flush)
            await run_in_threadpool(os.fsync, stream.fileno())

        def finalize():
            # Check again after upload, under the same repository lock used by
            # archive/transfer/member removal. A stale grant cannot publish data.
            db.expire_all()
            current = authorize(request, db, owner, name, write=True, lock=True)
            if current.id != repository_id:
                raise LFSError(409, "Repository changed during upload")
            os.replace(temporary, target)

        await run_in_threadpool(finalize)
    except OSError:
        raise LFSError(507, "Unable to store LFS object") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return Response(status_code=200)


@router.post(PREFIX + "/objects/{oid}/verify")
def verify(owner: str, name: str, oid: str, body: JSONBody, request: Request, db: Database):
    repository = authorize(request, db, owner, name, write=True)
    try:
        spec = ObjectSpec.model_validate(body)
    except ValidationError:
        raise LFSError(422, "Invalid LFS object") from None
    target = object_path(request.app.state.settings.git_repositories_root, repository.id, oid)
    if spec.oid != oid or not target.is_file() or target.stat().st_size != spec.size:
        raise LFSError(404, "Verified LFS object not found")
    return lfs_response({})


@router.post(PREFIX + "/locks/verify")
def locks(owner: str, name: str, request: Request, db: Database):
    authorize(request, db, owner, name, write=True)
    raise LFSError(501, "LFS file locking is not supported")
