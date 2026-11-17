"""Native docker login and live repository ACL at the existing Gateway."""

import base64
import binascii
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field, StrictBool, field_validator, model_validator
from sqlalchemy import func, or_, select, update

from devops_server.dependencies import Authenticated, Database
from devops_server.models import Repository, User
from devops_server.registry_client import RegistryClient
from devops_server.registry_models import RegistryCredential, RegistryNamespace
from devops_server.repositories import (
    repository_access,
    repository_out,
    require_owner,
    visible_repositories,
    visible_repository,
)
from devops_server.schemas import Input
from devops_server.services import audit

router = APIRouter(tags=["registry"])
# Docker repository names use path components, not hostnames: periods, single /
# double underscores and repeated dashes are valid separators in the first
# component too. The namespace must accept the same grammar as any image path.
COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
NAMESPACE = COMPONENT
IMAGE = rf"{NAMESPACE}/{COMPONENT}(?:/{COMPONENT})*"
DIGEST = r"sha256:[a-f0-9]{64}"
REFERENCE = rf"(?:{DIGEST}|[A-Za-z0-9_][A-Za-z0-9_.-]{{0,127}})"
TOKEN = re.compile(r"dr_[A-Za-z0-9_-]{64}")
CHALLENGE = {"WWW-Authenticate": 'Basic realm="DevOps Registry", charset="UTF-8"'}


def enabled(request: Request):
    if not request.app.state.settings.registry_enabled:
        raise HTTPException(403, "镜像仓库尚未启用")


def registry_host(request: Request) -> str:
    origins = request.app.state.settings.allowed_origins
    origin = next(
        (item for item in origins if urlsplit(item).netloc == request.headers.get("host")),
        origins[0],
    )
    return urlsplit(origin).netloc


def client(request: Request) -> RegistryClient:
    enabled(request)
    return RegistryClient(request.app.state.settings.registry_internal_url)


def live_repository(db: Database, repository_id: int, user_id: int, *, owner=False, lock=False):
    repo, _ = visible_repository(db, repository_id, user_id, lock=lock)
    if owner:
        require_owner(repo, user_id)
        if repo.archived:
            raise HTTPException(403, "归档仓库不能修改镜像或 namespace")
    return repo


class CredentialInput(Input):
    name: str = Field(min_length=1, max_length=80)
    access: Literal["read", "write"] = "read"
    days: int | None = Field(default=90, ge=1, le=365)
    acknowledge_permanent: StrictBool = False

    @model_validator(mode="after")
    def confirm_permanent(self):
        if self.days is None and not self.acknowledge_permanent:
            raise ValueError("创建永久令牌前，请确认了解令牌不会自动过期的风险")
        return self

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("请输入令牌名称")
        return value.strip()


def unexpired_credential(now):
    return or_(RegistryCredential.expires_at.is_(None), RegistryCredential.expires_at > now)


def credential_out(value: RegistryCredential):
    return {
        key: getattr(value, key)
        for key in (
            "id",
            "name",
            "access",
            "created_at",
            "expires_at",
            "revoked_at",
            "last_used_at",
        )
    }


@router.get("/api/me/registry-credentials")
def credentials(request: Request, actor: Authenticated, db: Database):
    rows = db.scalars(
        select(RegistryCredential)
        .where(RegistryCredential.user_id == actor.user.id, RegistryCredential.revoked_at.is_(None))
        # Always expose all active credentials so expired history cannot hide revocation controls.
        .order_by(
            unexpired_credential(datetime.now(UTC)).desc(),
            RegistryCredential.id.desc(),
        )
        .limit(100)
    )
    return {
        "enabled": request.app.state.settings.registry_enabled,
        "host": registry_host(request),
        "username": actor.user.username,
        "items": [credential_out(row) for row in rows],
    }


@router.post("/api/me/registry-credentials", status_code=201)
def create_credential(body: CredentialInput, request: Request, actor: Authenticated, db: Database):
    enabled(request)
    # Serialize quota and credential creation for this account.
    db.scalar(select(User).where(User.id == actor.user.id).with_for_update())
    now = datetime.now(UTC)
    count = db.scalar(
        select(func.count())
        .select_from(RegistryCredential)
        .where(
            RegistryCredential.user_id == actor.user.id,
            RegistryCredential.revoked_at.is_(None),
            unexpired_credential(now),
        )
    )
    if count >= 50:
        raise HTTPException(409, "最多保留 50 个有效令牌，请先撤销不再使用的令牌")
    raw = "dr_" + secrets.token_urlsafe(48)
    value = RegistryCredential(
        user_id=actor.user.id,
        name=body.name,
        access=body.access,
        token_digest=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=now + timedelta(days=body.days) if body.days is not None else None,
    )
    db.add(value)
    db.flush()
    audit(
        db,
        request,
        "registry.credential_created",
        actor_id=actor.user.id,
        target_type="registry_credential",
        target_id=value.id,
        details={"access": value.access, "days": body.days},
    )
    return {
        **credential_out(value),
        "token": raw,
        "host": registry_host(request),
        "username": actor.user.username,
    }


@router.delete("/api/me/registry-credentials/{credential_id}", status_code=204)
def revoke_credential(credential_id: int, request: Request, actor: Authenticated, db: Database):
    value = db.scalar(
        select(RegistryCredential)
        .where(RegistryCredential.id == credential_id, RegistryCredential.user_id == actor.user.id)
        .with_for_update()
    )
    if value is None:
        raise HTTPException(404, "令牌不存在")
    if value.revoked_at is None:
        value.revoked_at = datetime.now(UTC)
        audit(
            db,
            request,
            "registry.credential_revoked",
            actor_id=actor.user.id,
            target_type="registry_credential",
            target_id=value.id,
        )


class NamespaceInput(Input):
    name: str = Field(min_length=1, max_length=63, pattern=rf"^{NAMESPACE}$")


class NamespaceState(Input):
    enabled: StrictBool
    expected_version: int | None = Field(default=None, ge=1)


class BindInput(Input):
    namespace_id: int = Field(gt=0)
    expected_repository_id: int = Field(gt=0)
    expected_version: int | None = Field(default=None, ge=1)


def namespace_out(ns: RegistryNamespace):
    return {
        key: getattr(ns, key)
        for key in ("id", "name", "repository_id", "enabled", "version", "created_at")
    }


@router.get("/api/me/registry/namespaces")
def my_namespaces(
    request: Request,
    actor: Authenticated,
    db: Database,
    q: str = Query(default="", max_length=100),
    state: Literal["all", "bound", "unbound"] = "all",
    page: int = Query(default=1, ge=1, le=100000),
):
    statement = (
        select(RegistryNamespace, Repository)
        .join(Repository, RegistryNamespace.repository_id == Repository.id)
        .join(User, Repository.owner_id == User.id)
        .where(
            RegistryNamespace.deleted_at.is_(None),
            Repository.id.in_(visible_repositories(actor.user.id).with_only_columns(Repository.id)),
        )
    )
    if q.strip():
        term = q.strip().lower()
        statement = statement.where(
            or_(
                RegistryNamespace.name.contains(term, autoescape=True),
                (User.username + "/" + Repository.name).contains(term, autoescape=True),
            )
        )
    if state != "all":
        statement = statement.where(RegistryNamespace.enabled.is_(state == "bound"))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.execute(statement.order_by(RegistryNamespace.name).offset((page - 1) * 20).limit(20))
    return {
        "enabled": request.app.state.settings.registry_enabled,
        "host": registry_host(request),
        "total": total,
        "page": page,
        "page_size": 20,
        "items": [
            {**namespace_out(ns), "repository": repository_out(db, repo, actor.user.id)}
            for ns, repo in rows
        ],
    }


@router.get("/api/me/registry/repositories")
def my_registry_repositories(
    actor: Authenticated,
    db: Database,
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1, le=100000),
):
    statement = select(Repository).where(
        Repository.owner_id == actor.user.id,
        Repository.deleted_at.is_(None),
        Repository.archived.is_(False),
        Repository.name.contains(q.strip().lower(), autoescape=True),
    )
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.scalars(statement.order_by(Repository.name).offset((page - 1) * 20).limit(20))
    return {
        "items": [repository_out(db, repo, actor.user.id) for repo in rows],
        "total": total,
        "page": page,
        "page_size": 20,
    }


@router.get("/api/repositories/{repository_id}/registry")
def namespaces(repository_id: int, request: Request, actor: Authenticated, db: Database):
    repo = live_repository(db, repository_id, actor.user.id)
    rows = db.scalars(
        select(RegistryNamespace)
        .where(RegistryNamespace.repository_id == repo.id, RegistryNamespace.deleted_at.is_(None))
        .order_by(RegistryNamespace.name)
    )
    return {
        "enabled": request.app.state.settings.registry_enabled,
        "host": registry_host(request),
        "username": actor.user.username,
        "items": [namespace_out(row) for row in rows],
    }


@router.post("/api/repositories/{repository_id}/registry/namespaces", status_code=201)
def create_namespace(
    repository_id: int, body: NamespaceInput, request: Request, actor: Authenticated, db: Database
):
    enabled(request)
    live_repository(db, repository_id, actor.user.id, owner=True, lock=True)
    # A global name lock prevents concurrent claims, including names retained by deleted repos.
    from sqlalchemy import text

    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"),
        {"name": "registry:" + body.name},
    )
    if db.scalar(select(RegistryNamespace.id).where(RegistryNamespace.name == body.name)):
        raise HTTPException(409, "此 namespace 名称已被保留，请使用其他名称")
    value = RegistryNamespace(name=body.name, repository_id=repository_id)
    db.add(value)
    db.flush()
    audit(
        db,
        request,
        "registry.namespace_created",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository_id,
        details={"namespace": value.name},
    )
    return namespace_out(value)


@router.get("/api/repositories/{repository_id}/registry/candidates")
def candidates(repository_id: int, actor: Authenticated, db: Database):
    live_repository(db, repository_id, actor.user.id, owner=True)
    rows = db.execute(
        select(RegistryNamespace, Repository.name)
        .join(Repository, RegistryNamespace.repository_id == Repository.id)
        .where(
            Repository.owner_id == actor.user.id,
            Repository.deleted_at.is_(None),
            Repository.archived.is_(False),
            Repository.id != repository_id,
            RegistryNamespace.deleted_at.is_(None),
        )
        .order_by(RegistryNamespace.name)
        .limit(200)
    )
    return [{**namespace_out(ns), "repository_name": name} for ns, name in rows]


@router.post("/api/repositories/{repository_id}/registry/bind")
def bind_namespace(
    repository_id: int, body: BindInput, request: Request, actor: Authenticated, db: Database
):
    enabled(request)
    for repo_id in sorted({repository_id, body.expected_repository_id}):
        live_repository(db, repo_id, actor.user.id, owner=True, lock=True)
    value = db.scalar(
        select(RegistryNamespace)
        .where(RegistryNamespace.id == body.namespace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        value is None
        or value.deleted_at is not None
        or value.repository_id != body.expected_repository_id
    ):
        raise HTTPException(409, "namespace 归属已变化，请刷新后重试")
    if body.expected_version is not None and value.version != body.expected_version:
        raise HTTPException(409, "namespace 已变化，请刷新后重试")
    value.repository_id, value.enabled = repository_id, True
    value.version += 1
    audit(
        db,
        request,
        "registry.namespace_bound",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository_id,
        details={"namespace": value.name, "previous_repository_id": body.expected_repository_id},
    )
    return namespace_out(value)


@router.put("/api/repositories/{repository_id}/registry/namespaces/{namespace_id}")
def namespace_state(
    repository_id: int,
    namespace_id: int,
    body: NamespaceState,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    enabled(request)
    live_repository(db, repository_id, actor.user.id, owner=True, lock=True)
    value = db.scalar(
        select(RegistryNamespace)
        .where(
            RegistryNamespace.id == namespace_id, RegistryNamespace.repository_id == repository_id
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if value is None or value.deleted_at is not None:
        raise HTTPException(404, "namespace 不存在")
    if body.expected_version is not None and value.version != body.expected_version:
        raise HTTPException(409, "namespace 已变化，请刷新后重试")
    value.enabled = body.enabled
    value.version += 1
    audit(
        db,
        request,
        "registry.namespace_enabled" if body.enabled else "registry.namespace_unbound",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository_id,
        details={"namespace": value.name},
    )
    return namespace_out(value)


def image_namespace(db, repository_id, namespace_id, user_id, *, owner=False):
    repo = live_repository(db, repository_id, user_id, owner=owner, lock=owner)
    value = db.scalar(
        select(RegistryNamespace).where(
            RegistryNamespace.id == namespace_id, RegistryNamespace.repository_id == repo.id
        )
    )
    if value is None or value.deleted_at is not None:
        raise HTTPException(404, "namespace 不存在")
    if not value.enabled:
        raise HTTPException(403, "请先重新绑定此 namespace")
    return value


def image_name(ns, image):
    name = ns.name + "/" + image
    if len(name) > 255 or not re.fullmatch(IMAGE, name):
        raise HTTPException(422, "镜像名称无效")
    return name


@router.get("/api/repositories/{repository_id}/registry/namespaces/{namespace_id}/images")
def images(
    repository_id: int,
    namespace_id: int,
    request: Request,
    actor: Authenticated,
    db: Database,
    page: int = Query(1, ge=1),
):
    ns = image_namespace(db, repository_id, namespace_id, actor.user.id)
    names = client(request).images(ns.name)
    return {
        "total": len(names),
        "page": page,
        "page_size": 20,
        "items": [
            {"name": name[len(ns.name) + 1 :], "reference": registry_host(request) + "/" + name}
            for name in names[(page - 1) * 20 : page * 20]
        ],
    }


@router.get("/api/repositories/{repository_id}/registry/namespaces/{namespace_id}/tags")
def tags(
    repository_id: int,
    namespace_id: int,
    image: str,
    request: Request,
    actor: Authenticated,
    db: Database,
    page: int = Query(1, ge=1),
):
    ns = image_namespace(db, repository_id, namespace_id, actor.user.id)
    name, upstream = image_name(ns, image), client(request)
    values = upstream.tags(name)
    items = []
    for tag in values[(page - 1) * 20 : page * 20]:
        try:
            items.append({"tag": tag, **upstream.manifest(name, tag)})
        except HTTPException as error:
            if error.status_code != 404:
                raise
            # A concurrent deletion must not make the remaining tags unavailable.
            items.append({"tag": tag, "missing": True})
    return {"total": len(values), "page": page, "page_size": 20, "items": items}


class DeleteManifest(Input):
    digest: str = Field(pattern=rf"^{DIGEST}$")


@router.delete(
    "/api/repositories/{repository_id}/registry/namespaces/{namespace_id}/manifest", status_code=204
)
def delete_manifest(
    repository_id: int,
    namespace_id: int,
    image: str,
    body: DeleteManifest,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    ns = image_namespace(db, repository_id, namespace_id, actor.user.id, owner=True)
    name = image_name(ns, image)
    client(request).delete(name, body.digest)
    audit(
        db,
        request,
        "registry.manifest_deleted",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository_id,
        details={"image": name, "digest": body.digest},
    )


def protocol_operations(method: str, raw_uri: str) -> list[tuple[str, str]]:
    """Parse the *original* request once; never authorize an Nginx-normalized alias."""
    if len(raw_uri) > 8192 or not raw_uri.startswith("/v2/") or "#" in raw_uri:
        raise HTTPException(403, "Registry 路径无效")
    path, _, query = raw_uri.partition("?")
    if "%" in path or "\\" in path:
        raise HTTPException(403, "Registry 路径无效")
    try:
        pairs = parse_qsl(
            query, keep_blank_values=True, strict_parsing=True, max_num_fields=10, errors="strict"
        )
    except ValueError, UnicodeError:
        raise HTTPException(403, "Registry 参数无效") from None
    params = dict(pairs)
    if len(params) != len(pairs):
        raise HTTPException(403, "Registry 参数重复")
    if path == "/v2/" and method in {"GET", "HEAD"} and not params:
        return []
    patterns = [
        (rf"/v2/(?P<name>{IMAGE})/tags/list", {"GET": "read", "HEAD": "read"}, {"n", "last"}),
        (
            rf"/v2/(?P<name>{IMAGE})/manifests/(?P<ref>{REFERENCE})",
            {"GET": "read", "HEAD": "read", "PUT": "write", "DELETE": "delete"},
            set(),
        ),
        (rf"/v2/(?P<name>{IMAGE})/blobs/{DIGEST}", {"GET": "read", "HEAD": "read"}, set()),
        (rf"/v2/(?P<name>{IMAGE})/blobs/uploads/", {"POST": "write"}, {"mount", "from", "digest"}),
        (
            rf"/v2/(?P<name>{IMAGE})/blobs/uploads/[a-zA-Z0-9_-]+",
            {"GET": "write", "HEAD": "write", "PATCH": "write", "PUT": "write", "DELETE": "write"},
            {"digest", "_state"},
        ),
        (rf"/v2/(?P<name>{IMAGE})/referrers/{DIGEST}", {"GET": "read"}, {"artifactType"}),
    ]
    for pattern, methods, allowed in patterns:
        match = re.fullmatch(pattern, path)
        if not match:
            continue
        if method not in methods or params.keys() - allowed:
            break
        operation = methods[method]
        if operation == "delete" and not re.fullmatch(DIGEST, match["ref"]):
            break
        if "digest" in params and not re.fullmatch(DIGEST, params["digest"]):
            break
        result = [(match["name"], operation)]
        if "from" in params or "mount" in params:
            if not re.fullmatch(IMAGE, params.get("from", "")) or not re.fullmatch(
                DIGEST, params.get("mount", "")
            ):
                break
            result.append((params["from"], "read"))
        return result
    raise HTTPException(403, "不允许此 Registry 操作")


def authenticate(request: Request, db: Database):
    try:
        scheme, value = request.headers.get("authorization", "").split(" ", 1)
        if scheme.lower() != "basic" or len(value) > 512:
            raise ValueError
        username, raw = base64.b64decode(value, validate=True).decode("ascii").split(":", 1)
        if not TOKEN.fullmatch(raw):
            raise ValueError
    except ValueError, UnicodeError, binascii.Error:
        raise HTTPException(401, "需要 Docker 专用令牌", headers=CHALLENGE) from None
    row = db.execute(
        select(RegistryCredential, User)
        .join(User, User.id == RegistryCredential.user_id)
        .where(
            RegistryCredential.token_digest == hashlib.sha256(raw.encode()).hexdigest(),
            User.username == username,
        )
        .execution_options(populate_existing=True)
    ).first()
    now = datetime.now(UTC)
    if (
        row is None
        or row[0].revoked_at is not None
        or (row[0].expires_at is not None and row[0].expires_at <= now)
        or row[1].status != "active"
    ):
        raise HTTPException(401, "Docker 令牌已失效", headers=CHALLENGE)
    credential, user = row
    request.state.user_id = user.id
    return credential, user


@router.get("/_internal/registry/authorize", include_in_schema=False)
def authorize(request: Request, db: Database):
    enabled(request)
    credential, user = authenticate(request, db)
    operations = protocol_operations(
        request.headers.get("x-original-method", ""), request.headers.get("x-original-uri", "")
    )
    upload_only = False
    for index, (name, operation) in enumerate(operations):
        if len(name) > 255:
            raise HTTPException(403, "镜像名称无效")
        row = db.execute(
            select(RegistryNamespace, Repository)
            .join(Repository, Repository.id == RegistryNamespace.repository_id)
            .where(
                RegistryNamespace.name == name.split("/", 1)[0],
                RegistryNamespace.enabled.is_(True),
                RegistryNamespace.deleted_at.is_(None),
            )
        ).first()
        repo = row[1] if row else None
        access = repository_access(db, repo, user.id) if repo else None
        if (
            access is None
            or (operation != "read" and (credential.access != "write" or access != "write"))
            or (operation == "delete" and repo.owner_id != user.id)
        ):
            if index == 1:
                # Mount is an optional optimization. The destination was authorized
                # above; strip mount/from before the upstream sees this request so
                # Buildx can upload its own bytes without reading an inaccessible source.
                upload_only = True
                continue
            raise HTTPException(403, "没有镜像操作权限")
        if operation != "read":
            audit(
                db,
                request,
                "registry." + operation,
                actor_id=user.id,
                target_type="repository",
                target_id=repo.id,
                details={
                    "image": name,
                    "credential_id": credential.id,
                    "method": request.headers.get("x-original-method"),
                },
            )
    now = datetime.now(UTC)
    if credential.last_used_at is None or credential.last_used_at <= now - timedelta(minutes=1):
        db.execute(
            update(RegistryCredential)
            .where(RegistryCredential.id == credential.id)
            .values(last_used_at=now)
        )
    return Response(
        status_code=204, headers={"X-Registry-Upload-Only": "1"} if upload_only else None
    )
