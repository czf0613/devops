"""Explicit, audited namespace administration; does not grant image access."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import and_, func, or_, select

from devops_server import registry_gc_service as gc
from devops_server.admin_verification import verify_admin_operation
from devops_server.dependencies import Administrator, Database
from devops_server.models import Repository, User
from devops_server.registry import enabled, namespace_out, registry_host
from devops_server.registry_models import RegistryGCRun, RegistryGCWorker, RegistryNamespace
from devops_server.schemas import AdminVerification
from devops_server.services import audit

router = APIRouter(prefix="/api/admin/registry", tags=["registry administration"])


@router.get("/gc")
def gc_runs(actor: Administrator, db: Database, page: int = Query(1, ge=1)):
    return {
        "pending_namespaces": db.scalar(
            select(func.count())
            .select_from(RegistryNamespace)
            .where(
                RegistryNamespace.deleted_at.is_not(None),
                RegistryNamespace.purged_at.is_(None),
            )
        ),
        "worker_available": gc.worker_available(db.get(RegistryGCWorker, 1)),
        "total": db.scalar(select(func.count()).select_from(RegistryGCRun)),
        "page": page,
        "page_size": 10,
        "active": gc.run_out(active)
        if (active := db.scalar(select(RegistryGCRun).where(RegistryGCRun.state.in_(gc.ACTIVE))))
        else None,
        "items": [
            gc.run_out(run)
            for run in db.scalars(
                select(RegistryGCRun)
                .order_by(RegistryGCRun.id.desc())
                .offset((page - 1) * 10)
                .limit(10)
            )
        ],
    }


class GCInput(AdminVerification):
    maintenance_confirmed: Literal[True]


@router.post("/gc", status_code=202)
def start_gc(body: GCInput, request: Request, actor: Administrator, db: Database):
    enabled(request)
    worker = db.scalar(select(RegistryGCWorker).where(RegistryGCWorker.id == 1).with_for_update())
    if not gc.worker_available(worker):
        raise HTTPException(503, "空间回收进程未就绪，请联系维护人员检查后重试")
    if db.scalar(select(RegistryGCRun.id).where(RegistryGCRun.state.in_(gc.ACTIVE))):
        raise HTTPException(409, "已有空间回收任务，请等待其结束")
    verify_admin_operation(db, request, actor, body.code)
    run = RegistryGCRun(actor_id=actor.user.id, session_id=actor.session.id)
    db.add(run)
    db.flush()
    audit(
        db,
        request,
        "registry.gc_requested",
        actor_id=actor.user.id,
        target_type="registry_gc",
        target_id=run.id,
        details={"maintenance_confirmed": True, "delete_untagged": True},
    )
    return gc.run_out(run)


@router.get("")
def namespaces(
    request: Request,
    actor: Administrator,
    db: Database,
    q: str = Query("", max_length=100),
    state: Literal["all", "bound", "unbound", "deleted"] = "all",
    page: int = Query(1, ge=1),
):
    full_name = User.username + "/" + Repository.name
    active = and_(RegistryNamespace.enabled.is_(True), Repository.deleted_at.is_(None))
    unbound = or_(RegistryNamespace.enabled.is_(False), Repository.deleted_at.is_not(None))
    query = (
        select(RegistryNamespace, Repository, full_name.label("full_name"))
        .join(Repository, Repository.id == RegistryNamespace.repository_id)
        .join(User, User.id == Repository.owner_id)
        .where(
            RegistryNamespace.deleted_at.is_not(None)
            if state == "deleted"
            else RegistryNamespace.deleted_at.is_(None)
        )
    )
    if q.strip():
        query = query.where(
            or_(
                RegistryNamespace.name.icontains(q.strip(), autoescape=True),
                full_name.icontains(q.strip(), autoescape=True),
            )
        )
    if state in {"bound", "unbound"}:
        query = query.where(active if state == "bound" else unbound)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(RegistryNamespace.name).offset((page - 1) * 20).limit(20))
    return {
        "enabled": request.app.state.settings.registry_enabled,
        "host": registry_host(request),
        "page": page,
        "page_size": 20,
        "total": total,
        "items": [
            {
                **namespace_out(ns),
                "bound": ns.enabled and repo.deleted_at is None,
                "deleted_at": ns.deleted_at,
                "purged_at": ns.purged_at,
                "repository": {
                    "id": repo.id,
                    "full_name": name,
                    "archived": repo.archived,
                    "deleted": repo.deleted_at is not None,
                },
            }
            for ns, repo, name in rows
        ],
    }


@router.get("/repositories")
def repositories(
    actor: Administrator,
    db: Database,
    q: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
):
    # Only expose the identity needed to choose a destination, never repo content or members.
    full_name = User.username + "/" + Repository.name
    query = (
        select(Repository.id, full_name.label("full_name"))
        .join(User, User.id == Repository.owner_id)
        .where(Repository.deleted_at.is_(None), Repository.archived.is_(False))
    )
    if q.strip():
        query = query.where(full_name.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "page": page,
        "page_size": 20,
        "total": total,
        "items": [
            dict(row._mapping)
            for row in db.execute(
                query.order_by(full_name, Repository.id).offset((page - 1) * 20).limit(20)
            )
        ],
    }


class BindingInput(AdminVerification):
    repository_id: int | None = Field(gt=0)
    expected_version: int = Field(ge=1)


@router.put("/namespaces/{namespace_id}/binding")
def binding(
    namespace_id: int,
    body: BindingInput,
    request: Request,
    actor: Administrator,
    db: Database,
):
    enabled(request)
    snapshot = db.get(RegistryNamespace, namespace_id)
    if snapshot is None or snapshot.deleted_at is not None:
        raise HTTPException(404, "namespace 不存在")
    source_id = snapshot.repository_id
    # Same lock order as repo-owner mutations and repository lifecycle changes.
    repo_ids = {source_id}
    if body.repository_id is not None:
        repo_ids.add(body.repository_id)
    repos = {
        repo.id: repo
        for repo in db.scalars(
            select(Repository)
            .where(Repository.id.in_(repo_ids))
            .order_by(Repository.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    value = db.scalar(
        select(RegistryNamespace)
        .where(RegistryNamespace.id == namespace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        value.deleted_at is not None
        or value.version != body.expected_version
        or value.repository_id != source_id
    ):
        raise HTTPException(409, "namespace 已被其他操作修改，请刷新后重试")
    if body.repository_id is not None:
        target = repos.get(body.repository_id)
        if target is None or target.deleted_at is not None or target.archived:
            raise HTTPException(409, "目标 repo 不存在、已归档或已删除，请重新选择")
    previous_enabled = value.enabled
    verify_admin_operation(db, request, actor, body.code)
    # Retain the last owner and name while unbound, so ordinary users cannot claim old images.
    if body.repository_id is not None:
        value.repository_id = body.repository_id
    value.enabled = body.repository_id is not None
    value.version += 1
    audit(
        db,
        request,
        "registry.admin_bound" if value.enabled else "registry.admin_unbound",
        actor_id=actor.user.id,
        target_type="registry_namespace",
        target_id=value.id,
        details={
            "namespace": value.name,
            "previous_repository_id": source_id,
            "previous_enabled": previous_enabled,
            "repository_id": body.repository_id,
            "version": value.version,
        },
    )
    return namespace_out(value)


class DeleteNamespace(AdminVerification):
    expected_version: int = Field(ge=1)
    confirm_name: str = Field(min_length=1, max_length=63)


@router.delete("/namespaces/{namespace_id}")
def delete_namespace(
    namespace_id: int,
    body: DeleteNamespace,
    request: Request,
    actor: Administrator,
    db: Database,
):
    enabled(request)
    snapshot = db.get(RegistryNamespace, namespace_id)
    if snapshot is None or snapshot.deleted_at is not None:
        raise HTTPException(404, "namespace 不存在")
    source_id = snapshot.repository_id
    db.scalar(
        select(Repository)
        .where(Repository.id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    value = db.scalar(
        select(RegistryNamespace)
        .where(RegistryNamespace.id == namespace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        value.deleted_at is not None
        or value.version != body.expected_version
        or value.repository_id != source_id
    ):
        raise HTTPException(409, "namespace 已被其他操作修改，请刷新后重试")
    if body.confirm_name != value.name:
        raise HTTPException(409, "namespace 名称不匹配")
    verify_admin_operation(db, request, actor, body.code)
    # Revoke access atomically. The GC worker removes all tags in the next confirmed
    # maintenance window, after in-flight writes have stopped, then reclaims the graph.
    value.enabled = False
    value.deleted_at = datetime.now(UTC)
    value.version += 1
    audit(
        db,
        request,
        "registry.admin_deleted",
        actor_id=actor.user.id,
        target_type="registry_namespace",
        target_id=value.id,
        details={
            "namespace": value.name,
            "previous_repository_id": source_id,
            "version": value.version,
        },
    )
    return {
        "message": "namespace 及全部镜像已停止访问，"
        "下次维护窗口 GC 将清理全部镜像数据；名称继续保留"
    }
