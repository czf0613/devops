"""Private repositories. Management rank, Git access and job role are independent."""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StrictBool, StringConstraints, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from devops_server.auth import issue_email_code
from devops_server.dependencies import Database
from devops_server.dependencies import RepositoryAuthenticated as Authenticated
from devops_server.models import Repository, RepositoryMember, User
from devops_server.operation_verification import verify_repository_operation
from devops_server.repository_storage import RepositoryStorageError, create_bare_repository
from devops_server.schemas import Code, EmailCodeRequest, Input
from devops_server.services import audit

router = APIRouter(prefix="/api/repositories", tags=["repositories"])
Access = Literal["read", "write"]
JobRole = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class CreateRepository(Input):
    name: str
    description: Description = ""

    @field_validator("name")
    @classmethod
    def name_format(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", value) or value.endswith(".git"):
            raise ValueError(
                "仓库名为 1–100 位字母、数字、点、下划线或短横线；以字母或数字开头，不以 .git 结尾"
            )
        return value


class UpdateRepository(Input):
    description: Description


class ConfirmRepository(Input):
    confirm_name: Annotated[str, StringConstraints(max_length=133)]
    code: Code


class TransferRepository(ConfirmRepository):
    new_owner_id: Annotated[int, Field(gt=0)]


class ArchiveRepository(Input):
    archived: StrictBool
    code: Code


class RepositoryProtection(Input):
    protect_main: StrictBool
    protect_push: StrictBool


class AddMember(Input):
    user_id: Annotated[int, Field(gt=0)]
    access: Access = "read"
    role: JobRole = ""
    is_admin: StrictBool = False


class UpdateMember(Input):
    access: Access | None = None
    role: JobRole | None = None
    is_admin: StrictBool | None = None

    @field_validator("access", "role", "is_admin")
    @classmethod
    def no_explicit_null(cls, value):
        if value is None:
            raise ValueError("成员设置不可为 null")
        return value


def membership(db: Session, repository_id: int, user_id: int) -> RepositoryMember | None:
    return db.scalar(
        select(RepositoryMember)
        .where(RepositoryMember.repository_id == repository_id, RepositoryMember.user_id == user_id)
        .execution_options(populate_existing=True)
    )


def repository_access(db: Session, repository: Repository, user_id: int) -> str | None:
    if repository.deleted_at is not None:
        return None
    if repository.owner_id == user_id:
        return "read" if repository.archived else "write"
    member = membership(db, repository.id, user_id)
    return ("read" if repository.archived else member.access) if member else None


def visible_repositories(user_id: int):
    """Apply private repository ACL before aggregation, ordering or pagination."""
    member = select(RepositoryMember.id).where(
        RepositoryMember.repository_id == Repository.id, RepositoryMember.user_id == user_id
    )
    return select(Repository).where(
        Repository.deleted_at.is_(None),
        or_(Repository.owner_id == user_id, member.exists()),
    )


def visible_repository(db: Session, repository_id: int, user_id: int, *, lock=False):
    statement = select(Repository).where(
        Repository.id == repository_id, Repository.deleted_at.is_(None)
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    repository = db.scalar(statement)
    member = membership(db, repository_id, user_id) if repository else None
    if repository is None or (repository.owner_id != user_id and member is None):
        raise HTTPException(404, "仓库不存在或你没有访问权限")
    return repository, member


def require_manager(repository: Repository, member: RepositoryMember | None, user_id: int):
    if repository.owner_id != user_id and not (member and member.is_admin):
        raise HTTPException(403, "需要仓库拥有者或仓库管理员权限")


def require_owner(repository: Repository, user_id: int):
    if repository.owner_id != user_id:
        raise HTTPException(403, "只有仓库拥有者可以执行此操作")


def confirm_repository(db: Session, repository: Repository, name: str):
    owner = db.get(User, repository.owner_id)
    if name != f"{owner.username}/{repository.name}":
        raise HTTPException(409, "仓库名称不匹配，请输入当前完整仓库名")


def repository_out(db: Session, repository: Repository, user_id: int) -> dict:
    owner = db.get(User, repository.owner_id)
    member = membership(db, repository.id, user_id)
    is_owner = repository.owner_id == user_id
    if repository.deleted_at is not None or (not is_owner and member is None):
        raise HTTPException(404, "仓库不存在或你没有访问权限")
    return {
        "id": repository.id,
        "name": repository.name,
        "description": repository.description,
        "archived": repository.archived,
        "protect_main": repository.protect_main,
        "protect_push": repository.protect_push,
        "owner": {"id": owner.id, "username": owner.username, "display_name": owner.display_name},
        "full_name": f"{owner.username}/{repository.name}",
        "clone_path": f"/git/{owner.username}/{repository.name}.git",
        "default_branch": "main",
        "created_at": repository.created_at,
        "updated_at": repository.updated_at,
        "member_count": 1
        + db.scalar(
            select(func.count())
            .select_from(RepositoryMember)
            .where(RepositoryMember.repository_id == repository.id)
        ),
        "permissions": {
            "is_owner": is_owner,
            "is_admin": bool(member and member.is_admin),
            "can_manage_members": is_owner or bool(member and member.is_admin),
            "access": repository_access(db, repository, user_id),
            "role": member.role if member else "",
        },
    }


def member_out(user: User, member: RepositoryMember | None) -> dict:
    return {
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "status": user.status,
        "is_owner": member is None,
        "is_admin": bool(member and member.is_admin),
        "access": member.access if member else "write",
        "role": member.role if member else "",
    }


@router.get("")
def list_repositories(
    actor: Authenticated,
    db: Database,
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    q: Annotated[str, Query(max_length=100)] = "",
):
    statement = visible_repositories(actor.user.id)
    if q.strip():
        statement = statement.where(Repository.name.contains(q.strip().lower(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    repositories = db.scalars(
        statement.order_by(Repository.id.desc()).offset((page - 1) * 20).limit(20)
    )
    return {
        "items": [repository_out(db, repo, actor.user.id) for repo in repositories],
        "total": total,
        "page": page,
        "page_size": 20,
    }


@router.post("", status_code=201)
def create_repository(body: CreateRepository, request: Request, actor: Authenticated, db: Database):
    repository = Repository(owner_id=actor.user.id, **body.model_dump())
    try:
        with db.begin_nested():
            db.add(repository)
            db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig.diag, "constraint_name", None) == "uq_repositories_owner_name":
            raise HTTPException(409, "你已拥有同名仓库，请使用其他名称") from None
        raise
    try:
        settings = request.app.state.settings
        # Only configured origins may enter the README, never an arbitrary Host header.
        origin = next(
            (
                origin
                for origin in settings.allowed_origins
                if urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()
            ),
            settings.allowed_origins[0],
        ).rstrip("/")
        full_name = f"{actor.user.username}/{repository.name}"
        create_bare_repository(
            settings.git_repositories_root,
            repository.id,
            full_name,
            f"{origin}/git/{full_name}.git",
        )
    except RepositoryStorageError:
        raise HTTPException(503, "仓库存储暂时不可用，请联系管理员后重试") from None
    audit(
        db,
        request,
        "repository.created",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository.id,
        details={"name": repository.name},
    )
    return repository_out(db, repository, actor.user.id)


@router.get("/by-name/{owner}/{name}")
def get_repository_by_name(owner: str, name: str, actor: Authenticated, db: Database):
    repository = db.scalar(
        select(Repository)
        .join(User, User.id == Repository.owner_id)
        .where(User.username == owner, Repository.name == name, Repository.deleted_at.is_(None))
    )
    if repository is None:
        raise HTTPException(404, "仓库不存在或你没有访问权限")
    return repository_out(db, repository, actor.user.id)


@router.get("/{repository_id}")
def get_repository(repository_id: int, actor: Authenticated, db: Database):
    repository, _ = visible_repository(db, repository_id, actor.user.id)
    return repository_out(db, repository, actor.user.id)


@router.post("/{repository_id}/operation-code", status_code=202)
def repository_operation_code(
    repository_id: int, request: Request, actor: Authenticated, db: Database
):
    repository, _ = visible_repository(db, repository_id, actor.user.id, lock=True)
    require_owner(repository, actor.user.id)
    return issue_email_code(
        EmailCodeRequest(email=actor.user.email, purpose="repository_operation"), request, db, actor
    )


@router.post("/{repository_id}/transfer")
def transfer_repository(
    repository_id: int,
    body: TransferRepository,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repository, _ = visible_repository(db, repository_id, actor.user.id, lock=True)
    require_owner(repository, actor.user.id)
    confirm_repository(db, repository, body.confirm_name)
    verify_repository_operation(db, request, actor, body.code)
    previous_owner_id = transfer_ownership(db, repository, body.new_owner_id)
    audit(
        db,
        request,
        "repository.transferred",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository.id,
        details={"previous_owner_id": previous_owner_id, "new_owner_id": body.new_owner_id},
    )
    return repository_out(db, repository, actor.user.id)


def transfer_ownership(db: Session, repository: Repository, new_owner_id: int) -> int:
    """Apply an already-authorized transfer under the repository lock."""
    if new_owner_id == repository.owner_id:
        raise HTTPException(422, "请选择其他用户作为新拥有者")
    new_owner = db.scalar(
        select(User)
        .where(User.id == new_owner_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if new_owner is None or new_owner.status != "active":
        raise HTTPException(422, "所选用户不存在或已停用")
    previous_owner_id = repository.owner_id
    try:
        with db.begin_nested():
            new_owner_member = membership(db, repository.id, new_owner.id)
            if new_owner_member:
                db.delete(new_owner_member)
            db.add(
                RepositoryMember(
                    repository_id=repository.id,
                    user_id=previous_owner_id,
                    access="write",
                    role="",
                    is_admin=True,
                )
            )
            repository.owner_id = new_owner.id
            db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig.diag, "constraint_name", None) == "uq_repositories_owner_name":
            raise HTTPException(409, "新拥有者已有同名仓库，无法移交") from None
        raise
    return previous_owner_id


@router.put("/{repository_id}/archive")
def archive_repository(
    repository_id: int,
    body: ArchiveRepository,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repository, _ = visible_repository(db, repository_id, actor.user.id, lock=True)
    require_owner(repository, actor.user.id)
    verify_repository_operation(db, request, actor, body.code)
    if repository.archived != body.archived:
        repository.archived = body.archived
        audit(
            db,
            request,
            "repository.archived" if body.archived else "repository.unarchived",
            actor_id=actor.user.id,
            target_type="repository",
            target_id=repository.id,
        )
        db.flush()
    return repository_out(db, repository, actor.user.id)


@router.delete("/{repository_id}")
def delete_repository(
    repository_id: int,
    body: ConfirmRepository,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repository, _ = visible_repository(db, repository_id, actor.user.id, lock=True)
    require_owner(repository, actor.user.id)
    confirm_repository(db, repository, body.confirm_name)
    verify_repository_operation(db, request, actor, body.code)
    # Preserve the ID-backed storage and membership history. All public lookup and
    # Git authorization paths reject tombstones; a reused name gets a fresh ID.
    repository.deleted_at = datetime.now(UTC)
    audit(
        db,
        request,
        "repository.deleted",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository.id,
        details={"full_name": body.confirm_name},
    )
    return {"message": "仓库已删除，网页和后续 Git 请求将无法访问"}


@router.put("/{repository_id}/protection")
def update_protection(
    repository_id: int,
    body: RepositoryProtection,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repository, _ = visible_repository(db, repository_id, actor.user.id, lock=True)
    require_owner(repository, actor.user.id)
    repository.protect_main = body.protect_main
    repository.protect_push = body.protect_push
    audit(
        db,
        request,
        "repository.protection.updated",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository.id,
        details=body.model_dump(),
    )
    db.flush()
    return repository_out(db, repository, actor.user.id)


@router.patch("/{repository_id}")
def update_repository(
    repository_id: int, body: UpdateRepository, request: Request, actor: Authenticated, db: Database
):
    repository, member = visible_repository(db, repository_id, actor.user.id, lock=True)
    require_manager(repository, member, actor.user.id)
    repository.description = body.description
    audit(
        db,
        request,
        "repository.updated",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository.id,
    )
    db.flush()
    return repository_out(db, repository, actor.user.id)


@router.get("/{repository_id}/members")
def list_members(repository_id: int, actor: Authenticated, db: Database):
    repository, _ = visible_repository(db, repository_id, actor.user.id)
    return repository_members(db, repository)


def repository_members(db: Session, repository: Repository):
    rows = db.execute(
        select(User, RepositoryMember)
        .join(RepositoryMember, RepositoryMember.user_id == User.id)
        .where(RepositoryMember.repository_id == repository.id)
        .order_by(RepositoryMember.is_admin.desc(), User.username)
    )
    return [
        member_out(db.get(User, repository.owner_id), None),
        *(member_out(user, member) for user, member in rows),
    ]


@router.get("/{repository_id}/member-candidates")
def member_candidates(
    repository_id: int,
    actor: Authenticated,
    db: Database,
    q: Annotated[str, Query(min_length=2, max_length=100)],
):
    repository, member = visible_repository(db, repository_id, actor.user.id)
    require_manager(repository, member, actor.user.id)
    return find_candidates(db, repository, q, exclude_members=True)


@router.get("/{repository_id}/transfer-candidates")
def transfer_candidates(
    repository_id: int,
    actor: Authenticated,
    db: Database,
    q: Annotated[str, Query(min_length=2, max_length=100)],
):
    repository, _ = visible_repository(db, repository_id, actor.user.id)
    require_owner(repository, actor.user.id)
    return find_candidates(db, repository, q, exclude_members=False)


def find_candidates(db: Session, repository: Repository, q: str, *, exclude_members: bool):
    query = q.strip().lstrip("@")
    if len(query) < 2:
        return []
    statement = (
        select(User)
        .where(
            User.status == "active",
            User.id != repository.owner_id,
            or_(
                User.username.contains(query.lower(), autoescape=True),
                User.display_name.contains(query, autoescape=True),
            ),
        )
        .order_by(User.username)
        .limit(20)
    )
    if exclude_members:
        statement = statement.where(
            User.id.not_in(
                select(RepositoryMember.user_id).where(
                    RepositoryMember.repository_id == repository.id
                )
            )
        )
    return [
        {"id": user.id, "username": user.username, "display_name": user.display_name}
        for user in db.scalars(statement)
    ]


@router.post("/{repository_id}/members", status_code=201)
def add_member(
    repository_id: int, body: AddMember, request: Request, actor: Authenticated, db: Database
):
    repository, manager = visible_repository(db, repository_id, actor.user.id, lock=True)
    require_manager(repository, manager, actor.user.id)
    if body.user_id == repository.owner_id:
        raise HTTPException(409, "拥有者已具有仓库权限，无需添加为成员")
    if body.is_admin and repository.owner_id != actor.user.id:
        raise HTTPException(403, "只有仓库拥有者可以任命管理员")
    user = db.get(User, body.user_id)
    if user is None or user.status != "active":
        raise HTTPException(422, "所选用户不存在或已停用")
    if membership(db, repository.id, user.id):
        raise HTTPException(409, "该用户已是仓库成员")
    member = RepositoryMember(repository_id=repository.id, **body.model_dump())
    db.add(member)
    repository.updated_at = datetime.now(UTC)
    audit(
        db,
        request,
        "repository.member_added",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository.id,
        details=body.model_dump(),
    )
    return member_out(user, member)


def editable_member(db: Session, repository: Repository, user_id: int, actor_id: int):
    if user_id == repository.owner_id:
        raise HTTPException(403, "不能修改或移除仓库拥有者")
    member = membership(db, repository.id, user_id)
    if member is None:
        raise HTTPException(404, "仓库成员不存在")
    if member.is_admin and repository.owner_id != actor_id:
        raise HTTPException(403, "只有仓库拥有者可以管理仓库管理员")
    return member


@router.patch("/{repository_id}/members/{user_id}")
def update_member(
    repository_id: int,
    user_id: int,
    body: UpdateMember,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repository, manager = visible_repository(db, repository_id, actor.user.id, lock=True)
    require_manager(repository, manager, actor.user.id)
    member = editable_member(db, repository, user_id, actor.user.id)
    updates = body.model_dump(exclude_unset=True)
    if "is_admin" in updates and repository.owner_id != actor.user.id:
        raise HTTPException(403, "只有仓库拥有者可以任免管理员")
    for key, value in updates.items():
        setattr(member, key, value)
    repository.updated_at = datetime.now(UTC)
    audit(
        db,
        request,
        "repository.member_updated",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository.id,
        details={"user_id": user_id, **updates},
    )
    return member_out(db.get(User, user_id), member)


@router.delete("/{repository_id}/members/{user_id}")
def remove_member(
    repository_id: int, user_id: int, request: Request, actor: Authenticated, db: Database
):
    repository, manager = visible_repository(db, repository_id, actor.user.id, lock=True)
    require_manager(repository, manager, actor.user.id)
    member = editable_member(db, repository, user_id, actor.user.id)
    db.delete(member)
    repository.updated_at = datetime.now(UTC)
    audit(
        db,
        request,
        "repository.member_removed",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repository.id,
        details={"user_id": user_id},
    )
    return {"message": "成员已移除，后续 Git 请求将重新检查访问权限"}
