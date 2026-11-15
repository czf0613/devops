from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from devops_server.auth import verify_code
from devops_server.dependencies import Administrator, Authenticated, Database, role_codes
from devops_server.models import AuditEvent, Role, User, UserRole, UserSession
from devops_server.schemas import (
    AdminUserPatch,
    AuditOut,
    ClearPasswordRequest,
    CreateUserRequest,
    LoginRequest,
    PasswordRequest,
    ProfilePatch,
    RoleAssignment,
    UserOut,
)
from devops_server.security import clear_session_cookie
from devops_server.services import (
    advisory_lock,
    audit,
    create_member,
    locked_user,
    protect_last_admin,
    revoke_sessions,
    session_out,
    set_password,
    user_out,
)

router = APIRouter(prefix="/api", tags=["users"])
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("/me", response_model=UserOut)
def me(db: Database, actor: Authenticated):
    return user_out(db, actor.user)


def apply_profile(db, user, values):
    if values.get("username") == "admin" and user.username != "admin":
        raise HTTPException(422, "admin 用户名仅允许通过数据库创建")
    for key, value in values.items():
        setattr(user, key, value)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "邮箱或用户名已被使用") from None


@router.patch("/me", response_model=UserOut)
def update_me(body: ProfilePatch, request: Request, db: Database, actor: Authenticated):
    user = locked_user(db, actor.user.id)
    values = body.model_dump(exclude_unset=True)
    apply_profile(db, user, values)
    audit(
        db,
        request,
        "user.profile_updated",
        actor_id=actor.user.id,
        target_type="user",
        target_id=user.id,
        details={"fields": sorted(values)},
    )
    return user_out(db, user)


@router.post("/me/email")
def change_email(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Database,
    actor: Authenticated,
):
    verify_code(db, request, body.email, "change_email", body.code, actor.user.id)
    user = locked_user(db, actor.user.id)
    apply_profile(db, user, {"email": body.email})
    revoke_sessions(db, user.id, "email_changed")
    audit(
        db, request, "user.email_changed", actor_id=user.id, target_type="user", target_id=user.id
    )
    clear_session_cookie(response, request.app.state.settings)
    return {"message": "邮箱已更新，请使用新邮箱重新登录"}


@router.put("/me/legacy-password")
def my_password(
    body: PasswordRequest,
    request: Request,
    db: Database,
    actor: Authenticated,
):
    email = actor.user.email
    verify_code(db, request, email, "legacy_password", body.code, actor.user.id)
    user = locked_user(db, actor.user.id)
    db.refresh(actor.session)
    if (
        user.email != email
        or user.status != "active"
        or actor.session.revoked_at is not None
        or actor.session.expires_at <= datetime.now(UTC)
        or actor.session.password_version != user.password_version
    ):
        raise HTTPException(401, "登录状态已变更，请重新登录")
    set_password(db, request, user, body.password)
    audit(
        db,
        request,
        "user.legacy_password_changed",
        actor_id=user.id,
        target_type="user",
        target_id=user.id,
        details={"cleared": body.password is None},
    )
    return {
        "message": "兼容密码已清除" if body.password is None else "兼容密码已保存",
        "user": user_out(db, user),
    }


def list_sessions(db, user, current_id, page, page_size):
    now = datetime.now(UTC)
    conditions = (
        UserSession.user_id == user.id,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > now,
        UserSession.password_version == user.password_version,
        user.status == "active",
    )
    count = db.scalar(select(func.count()).select_from(UserSession).where(*conditions))
    sessions = db.scalars(
        select(UserSession)
        .where(*conditions)
        .order_by(UserSession.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [session_out(item, current_id, user, now=now) for item in sessions],
        "total": count,
        "page": page,
        "page_size": page_size,
    }


@router.get("/me/sessions")
def my_sessions(db: Database, actor: Authenticated, page: Page = 1, page_size: PageSize = 20):
    return list_sessions(db, actor.user, actor.session.id, page, page_size)


def revoke_one(db, request, user, session_id, actor_id):
    session = db.scalar(
        select(UserSession)
        .where(
            UserSession.id == session_id,
            UserSession.user_id == user.id,
        )
        .with_for_update()
    )
    if session is None:
        raise HTTPException(404, "会话不存在")
    if session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        session.revoke_reason = "manually_revoked"
        audit(
            db,
            request,
            "session.revoked",
            actor_id=actor_id,
            target_type="session",
            target_id=session.id,
        )


@router.delete("/me/sessions/{session_id}")
def revoke_my_session(
    session_id: int,
    request: Request,
    response: Response,
    db: Database,
    actor: Authenticated,
):
    user = locked_user(db, actor.user.id)
    revoke_one(db, request, user, session_id, actor.user.id)
    if session_id == actor.session.id:
        clear_session_cookie(response, request.app.state.settings)
    return {"message": "会话已吊销"}


@router.delete("/me/sessions")
def revoke_my_sessions(request: Request, response: Response, db: Database, actor: Authenticated):
    user = locked_user(db, actor.user.id)
    revoke_sessions(db, user.id, "all_sessions_revoked")
    audit(
        db, request, "session.all_revoked", actor_id=user.id, target_type="user", target_id=user.id
    )
    clear_session_cookie(response, request.app.state.settings)
    return {"message": "所有会话已退出"}


@router.get("/users")
def users(
    db: Database,
    actor: Administrator,
    page: Page = 1,
    page_size: PageSize = 20,
    q: Annotated[str, Query(max_length=100)] = "",
    status: Annotated[str | None, Query(pattern="^(active|disabled)$")] = None,
):
    conditions = []
    if q.strip():
        conditions.append(
            or_(
                User.email.icontains(q.strip(), autoescape=True),
                User.username.icontains(q.strip(), autoescape=True),
                User.display_name.icontains(q.strip(), autoescape=True),
            )
        )
    if status:
        conditions.append(User.status == status)
    count = db.scalar(select(func.count()).select_from(User).where(*conditions))
    rows = db.scalars(
        select(User)
        .where(*conditions)
        .order_by(User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [user_out(db, item) for item in rows],
        "total": count,
        "page": page,
        "page_size": page_size,
    }


@router.post("/users", response_model=UserOut, status_code=201)
def add_user(body: CreateUserRequest, request: Request, db: Database, actor: Administrator):
    user = create_member(db, body.model_dump(), actor.user.id)
    audit(
        db, request, "user.created", actor_id=actor.user.id, target_type="user", target_id=user.id
    )
    return user_out(db, user)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Database, actor: Administrator):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    return user_out(db, user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: AdminUserPatch,
    request: Request,
    db: Database,
    actor: Administrator,
):
    # Serialize operations that could remove the last usable admin, before taking user locks.
    advisory_lock(db, "active-admin-membership")
    user = locked_user(db, user_id)
    values = body.model_dump(exclude_unset=True)
    if values.get("status") == "disabled":
        protect_last_admin(db, user)
        revoke_sessions(db, user.id, "user_disabled")
    if "email" in values and values["email"] != user.email:
        if "admin" in role_codes(db, user.id):
            raise HTTPException(403, "管理员邮箱请本人验证后更换，或通过数据库修改")
        revoke_sessions(db, user.id, "email_changed")
    apply_profile(db, user, values)
    audit(
        db,
        request,
        "user.updated",
        actor_id=actor.user.id,
        target_type="user",
        target_id=user.id,
        details={
            "fields": sorted(values),
            **({"status": user.status} if "status" in values else {}),
        },
    )
    return user_out(db, user)


@router.put("/users/{user_id}/legacy-password")
def clear_password(
    user_id: int,
    body: ClearPasswordRequest,
    request: Request,
    db: Database,
    actor: Administrator,
):
    user = locked_user(db, user_id)
    set_password(db, request, user, None)
    audit(
        db,
        request,
        "user.legacy_password_cleared",
        actor_id=actor.user.id,
        target_type="user",
        target_id=user.id,
        details={"cleared": True},
    )
    return {"message": "兼容密码已清除，用户可验证邮箱后自行设置", "user": user_out(db, user)}


@router.get("/roles")
def roles(db: Database, actor: Administrator):
    return [
        {
            "code": role.code,
            "name": role.name,
            "description": role.description,
            "assignable": role.code != "admin",
        }
        for role in db.scalars(select(Role).order_by(Role.id))
    ]


@router.put("/users/{user_id}/roles", response_model=UserOut)
def assign_roles(
    user_id: int,
    body: RoleAssignment,
    request: Request,
    db: Database,
    actor: Administrator,
):
    if "admin" in body.roles:
        raise HTTPException(403, "管理员角色只能通过数据库设置")
    user = locked_user(db, user_id)
    roles_by_code = {role.code: role for role in db.scalars(select(Role))}
    if any(code not in roles_by_code for code in body.roles):
        raise HTTPException(422, "包含不存在的角色")
    admin_id = roles_by_code["admin"].id
    db.execute(delete(UserRole).where(UserRole.user_id == user.id, UserRole.role_id != admin_id))
    for code in set(body.roles):
        db.add(UserRole(user_id=user.id, role_id=roles_by_code[code].id, assigned_by=actor.user.id))
    db.flush()
    audit(
        db,
        request,
        "user.roles_updated",
        actor_id=actor.user.id,
        target_type="user",
        target_id=user.id,
        details={"roles": sorted(set(body.roles))},
    )
    return user_out(db, user)


@router.get("/users/{user_id}/sessions")
def user_sessions(
    user_id: int,
    db: Database,
    actor: Administrator,
    page: Page = 1,
    page_size: PageSize = 20,
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    return list_sessions(db, user, actor.session.id, page, page_size)


@router.delete("/users/{user_id}/sessions/{session_id}")
def revoke_user_session(
    user_id: int,
    session_id: int,
    request: Request,
    response: Response,
    db: Database,
    actor: Administrator,
):
    user = locked_user(db, user_id)
    revoke_one(db, request, user, session_id, actor.user.id)
    if session_id == actor.session.id:
        clear_session_cookie(response, request.app.state.settings)
    return {"message": "会话已吊销"}


@router.delete("/users/{user_id}/sessions")
def revoke_user_sessions(
    user_id: int,
    request: Request,
    response: Response,
    db: Database,
    actor: Administrator,
):
    user = locked_user(db, user_id)
    revoke_sessions(db, user.id, "admin_revoked")
    audit(
        db,
        request,
        "session.all_revoked",
        actor_id=actor.user.id,
        target_type="user",
        target_id=user.id,
    )
    if user.id == actor.user.id:
        clear_session_cookie(response, request.app.state.settings)
    return {"message": "该用户全部会话已吊销"}


@router.get("/audit-events")
def audit_events(
    db: Database,
    actor: Administrator,
    page: Page = 1,
    page_size: PageSize = 20,
    action: Annotated[str, Query(max_length=80)] = "",
):
    conditions = [AuditEvent.action == action] if action else []
    count = db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions))
    items = db.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [AuditOut.model_validate(item) for item in items],
        "total": count,
        "page": page,
        "page_size": page_size,
    }
