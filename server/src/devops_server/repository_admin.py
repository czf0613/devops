"""Audited administrative metadata mutations, separate from repository content ACL."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devops_server.admin_verification import verify_admin_operation
from devops_server.dependencies import Administrator, Database
from devops_server.models import Repository, RepositoryMember, User
from devops_server.repositories import (
    AddMember,
    ArchiveRepository,
    ConfirmRepository,
    TransferRepository,
    UpdateMember,
    confirm_repository,
    find_candidates,
    membership,
    repository_members,
    transfer_ownership,
)
from devops_server.schemas import AdminVerification
from devops_server.services import audit

router = APIRouter(prefix="/api/admin/repositories", tags=["repository administration"])


class VerifiedChange(AdminVerification):
    expected_updated_at: AwareDatetime


class AdminAddMember(VerifiedChange, AddMember):
    pass


class AdminUpdateMember(VerifiedChange, UpdateMember):
    pass


class AdminTransfer(VerifiedChange, TransferRepository):
    pass


class AdminArchive(VerifiedChange, ArchiveRepository):
    pass


class AdminDelete(VerifiedChange, ConfirmRepository):
    pass


def get_repository(db: Session, repository_id: int, *, lock=False) -> Repository:
    statement = select(Repository).where(Repository.id == repository_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    repository = db.scalar(statement)
    if repository is None:
        raise HTTPException(404, "仓库不存在")
    return repository


def metadata(db: Session, repository: Repository) -> dict:
    owner = db.get(User, repository.owner_id)
    return {
        "id": repository.id,
        "name": repository.name,
        "full_name": f"{owner.username}/{repository.name}",
        "owner": {"id": owner.id, "username": owner.username, "display_name": owner.display_name},
        "archived": repository.archived,
        "deleted_at": repository.deleted_at,
        "updated_at": repository.updated_at,
        "created_at": repository.created_at,
        "member_count": 1
        + db.scalar(
            select(func.count())
            .select_from(RepositoryMember)
            .where(RepositoryMember.repository_id == repository.id)
        ),
    }


def change_repository(db, repository_id, body, request, actor):
    repository = get_repository(db, repository_id, lock=True)
    if repository.deleted_at is not None:
        raise HTTPException(409, "仓库已删除，不能继续修改")
    if repository.updated_at != body.expected_updated_at:
        raise HTTPException(409, "仓库或成员已被其他操作修改，请刷新后重试")
    verify_admin_operation(db, request, actor, body.code)
    return repository


def record(db, request, actor, repository, action, details):
    repository.updated_at = datetime.now(UTC)
    audit(
        db,
        request,
        "repository.admin_" + action,
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository.id,
        details={"forced": True, **details},
    )
    db.flush()
    return {**metadata(db, repository), "members": repository_members(db, repository)}


@router.get("")
def list_repositories(
    actor: Administrator,
    db: Database,
    q: str = Query("", max_length=133),
    state: Literal["active", "archived", "deleted", "all"] = "active",
    page: int = Query(1, ge=1, le=100000),
):
    query = select(Repository).join(User, User.id == Repository.owner_id)
    if q.strip():
        query = query.where(
            (User.username + "/" + Repository.name).icontains(q.strip(), autoescape=True)
        )
    if state == "deleted":
        query = query.where(Repository.deleted_at.is_not(None))
    elif state != "all":
        query = query.where(
            Repository.deleted_at.is_(None), Repository.archived.is_(state == "archived")
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "items": [
            metadata(db, repo)
            for repo in db.scalars(
                query.order_by(Repository.id.desc()).offset((page - 1) * 20).limit(20)
            )
        ],
        "total": total,
        "page": page,
        "page_size": 20,
    }


@router.get("/{repository_id}")
def detail(repository_id: int, actor: Administrator, db: Database):
    repository = get_repository(db, repository_id)
    return {**metadata(db, repository), "members": repository_members(db, repository)}


@router.get("/{repository_id}/candidates")
def candidates(
    repository_id: int,
    actor: Administrator,
    db: Database,
    q: str = Query(min_length=2, max_length=100),
    purpose: Literal["member", "transfer"] = "member",
):
    repository = get_repository(db, repository_id)
    if repository.deleted_at is not None:
        raise HTTPException(409, "仓库已删除")
    return find_candidates(db, repository, q, exclude_members=purpose == "member")


@router.post("/{repository_id}/members", status_code=201)
def add_member(
    repository_id: int, body: AdminAddMember, request: Request, actor: Administrator, db: Database
):
    repository = change_repository(db, repository_id, body, request, actor)
    user = db.get(User, body.user_id)
    if user is None or user.status != "active":
        raise HTTPException(422, "所选用户不存在或已停用")
    if body.user_id == repository.owner_id or membership(db, repository.id, body.user_id):
        raise HTTPException(409, "所选用户已是仓库拥有者或成员")
    values = body.model_dump(include={"user_id", "access", "role", "is_admin"})
    db.add(RepositoryMember(repository_id=repository.id, **values))
    return record(db, request, actor, repository, "member_added", values)


def managed_member(db, repository, user_id):
    if user_id == repository.owner_id:
        raise HTTPException(409, "请通过移交所有权更换拥有者，不能直接修改或移除拥有者")
    member = membership(db, repository.id, user_id)
    if member is None:
        raise HTTPException(404, "仓库成员不存在")
    return member


@router.patch("/{repository_id}/members/{user_id}")
def update_member(
    repository_id: int,
    user_id: int,
    body: AdminUpdateMember,
    request: Request,
    actor: Administrator,
    db: Database,
):
    repository = change_repository(db, repository_id, body, request, actor)
    member = managed_member(db, repository, user_id)
    before = {key: getattr(member, key) for key in ("access", "role", "is_admin")}
    updates = body.model_dump(include={"access", "role", "is_admin"}, exclude_unset=True)
    if not updates:
        raise HTTPException(422, "请至少修改一项成员设置")
    for key, value in updates.items():
        setattr(member, key, value)
    return record(
        db,
        request,
        actor,
        repository,
        "member_updated",
        {"user_id": user_id, "before": before, "after": updates},
    )


@router.delete("/{repository_id}/members/{user_id}")
def remove_member(
    repository_id: int,
    user_id: int,
    body: VerifiedChange,
    request: Request,
    actor: Administrator,
    db: Database,
):
    repository = change_repository(db, repository_id, body, request, actor)
    member = managed_member(db, repository, user_id)
    before = {key: getattr(member, key) for key in ("access", "role", "is_admin")}
    db.delete(member)
    return record(
        db, request, actor, repository, "member_removed", {"user_id": user_id, "before": before}
    )


@router.post("/{repository_id}/transfer")
def transfer(
    repository_id: int, body: AdminTransfer, request: Request, actor: Administrator, db: Database
):
    repository = change_repository(db, repository_id, body, request, actor)
    confirm_repository(db, repository, body.confirm_name)
    previous_owner_id = transfer_ownership(db, repository, body.new_owner_id)
    return record(
        db,
        request,
        actor,
        repository,
        "transferred",
        {"previous_owner_id": previous_owner_id, "new_owner_id": body.new_owner_id},
    )


@router.put("/{repository_id}/archive")
def archive(
    repository_id: int, body: AdminArchive, request: Request, actor: Administrator, db: Database
):
    repository = change_repository(db, repository_id, body, request, actor)
    before = repository.archived
    repository.archived = body.archived
    return record(
        db,
        request,
        actor,
        repository,
        "archived" if body.archived else "unarchived",
        {"previous_archived": before},
    )


@router.delete("/{repository_id}")
def delete(
    repository_id: int, body: AdminDelete, request: Request, actor: Administrator, db: Database
):
    repository = change_repository(db, repository_id, body, request, actor)
    confirm_repository(db, repository, body.confirm_name)
    repository.deleted_at = datetime.now(UTC)
    return record(db, request, actor, repository, "deleted", {"full_name": body.confirm_name})
