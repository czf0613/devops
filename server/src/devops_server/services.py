import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from devops_server.dependencies import role_codes
from devops_server.models import AuditEvent, Role, User, UserRole, UserSession
from devops_server.schemas import SessionOut, UserOut
from devops_server.security import client_ip, encrypt_password


def audit(
    db: Session,
    request: Request,
    action: str,
    *,
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    result: str = "success",
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            result=result,
            request_id=request.state.request_id,
            details={"ip": client_ip(request), **(details or {})},
        )
    )


def fail(
    db: Session,
    request: Request,
    action: str,
    status: int,
    message: str,
    *,
    actor_id: int | None = None,
    reason: str = "rejected",
) -> None:
    audit(db, request, action, actor_id=actor_id, result="failure", details={"reason": reason})
    db.commit()  # Failed code attempts and their audit records must survive the HTTP error.
    raise HTTPException(status, message)


def advisory_lock(db: Session, key: str) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


def locked_user(db: Session, user_id: int) -> User:
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise HTTPException(404, "用户不存在")
    return user


def user_out(db: Session, user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        phone=user.phone,
        status=user.status,
        roles=role_codes(db, user.id),
        has_legacy_password=bool(
            db.scalar(select(User.password_hash.is_not(None)).where(User.id == user.id))
        ),
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def create_member(db: Session, values: dict, assigned_by: int | None = None) -> User:
    if values["username"] == "admin":
        raise HTTPException(422, "admin 用户名仅允许通过数据库创建")
    user = User(**values)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "邮箱或用户名已被使用") from None
    member_id = db.scalar(select(Role.id).where(Role.code == "member"))
    if member_id is None:
        raise HTTPException(503, "成员角色尚未初始化")
    db.add(UserRole(user_id=user.id, role_id=member_id, assigned_by=assigned_by))
    db.flush()
    return user


def create_session(db: Session, request: Request, user: User) -> tuple[UserSession, str]:
    settings = request.app.state.settings
    now = datetime.now(UTC)
    for _ in range(3):
        token = secrets.token_urlsafe(48)
        session = UserSession(
            user_id=user.id,
            token=token,
            password_version=user.password_version,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=settings.session_days),
            login_ip=client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        try:
            with db.begin_nested():
                db.add(session)
                db.flush()
            return session, token
        except IntegrityError as error:
            if getattr(error.orig.diag, "constraint_name", "") != "uq_user_sessions_token":
                raise
    raise HTTPException(503, "无法创建登录会话，请重试")


def revoke_sessions(db: Session, user_id: int, reason: str) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoke_reason=reason)
    )


def set_password(db: Session, request: Request, user: User, password: str | None) -> None:
    previous_version = user.password_version
    if password is None:
        user.password_hash = user.password_ciphertext = user.password_key_id = None
    else:
        user.password_hash, user.password_ciphertext, user.password_key_id = encrypt_password(
            request.app.state.settings, user.id, password
        )
    user.password_version += 1
    user.password_changed_at = datetime.now(UTC)
    # DevOps authenticates by email. Keep currently valid sessions without restoring stale,
    # expired or explicitly revoked sessions when the legacy credential changes.
    db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.password_version == previous_version,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
        .values(password_version=user.password_version)
    )


def protect_last_admin(db: Session, user: User) -> None:
    if user.status != "active" or "admin" not in role_codes(db, user.id):
        return
    active_admins = db.scalar(
        select(func.count())
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(User.status == "active", Role.code == "admin")
    )
    if active_admins <= 1:
        raise HTTPException(409, "不能停用最后一个有效管理员")


def session_out(session: UserSession, current_id: int, user: User, *, now: datetime) -> SessionOut:
    return SessionOut(
        id=session.id,
        user_id=session.user_id,
        created_at=session.created_at,
        expires_at=session.expires_at,
        last_seen_at=session.last_seen_at,
        revoked_at=session.revoked_at,
        revoke_reason=session.revoke_reason,
        login_ip=str(session.login_ip) if session.login_ip else None,
        user_agent=session.user_agent,
        current=session.id == current_id,
        active=(
            session.revoked_at is None
            and session.expires_at > now
            and session.password_version == user.password_version
            and user.status == "active"
        ),
    )
