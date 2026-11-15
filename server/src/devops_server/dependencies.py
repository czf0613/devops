import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from devops_server.models import GitOAuthGrant, GitOAuthToken, Role, User, UserRole, UserSession


def database(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine, expire_on_commit=False) as db:
        try:
            yield db
            expires_at = renew_session(db, request)
            db.commit()
            # Middleware emits cookies only after the transaction has committed.
            request.state.renewed_session_expiry = expires_at
        except BaseException:
            db.rollback()
            raise


def renew_session(db: Session, request: Request) -> datetime | None:
    if not getattr(request.state, "session_renewal_due", False):
        return None
    now = datetime.now(UTC)
    session_id = db.scalar(
        select(UserSession.id)
        .where(
            UserSession.id == request.state.session_id,
            or_(
                UserSession.last_seen_at <= now - timedelta(minutes=1),
                UserSession.expires_at <= now + timedelta(minutes=1),
            ),
        )
        .with_for_update()
    )
    if session_id is None:
        return None
    # Recheck after waiting for the lock; never revive an expired or revoked session.
    now = datetime.now(UTC)
    active_user = (
        select(User.id)
        .where(
            User.id == UserSession.user_id,
            User.status == "active",
            User.password_version == UserSession.password_version,
        )
        .exists()
    )
    return db.scalar(
        update(UserSession)
        .where(
            UserSession.id == session_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            active_user,
        )
        .values(
            last_seen_at=now,
            expires_at=func.greatest(
                UserSession.expires_at,
                now + timedelta(days=request.app.state.settings.session_days),
            ),
        )
        .returning(UserSession.expires_at)
        .execution_options(synchronize_session=False)
    )


# Commit before headers/cookies reach the client, so a failed commit cannot report success.
Database = Annotated[Session, Depends(database, scope="function")]


def role_codes(db: Session, user_id: int) -> list[str]:
    return list(
        db.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.code)
        )
    )


@dataclass
class Principal:
    user: User
    session: UserSession | None
    roles: list[str]
    grant: GitOAuthGrant | None = None


def current_user(request: Request, db: Database) -> Principal:
    if request.headers.get("authorization"):
        raise HTTPException(401, "此接口只接受网页登录会话")
    token = request.cookies.get(request.app.state.settings.session_cookie_name, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{64}", token):
        raise HTTPException(401, "请先通过邮箱验证码登录")
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token == token)
    ).first()
    now = datetime.now(UTC)
    if row is None:
        raise HTTPException(401, "登录已失效，请重新登录")
    session, user = row
    if (
        session.revoked_at is not None
        or session.expires_at <= now
        or user.status != "active"
        or session.password_version != user.password_version
    ):
        raise HTTPException(401, "登录已失效，请重新登录")
    request.state.session_id = session.id
    request.state.user_id = user.id
    request.state.session_renewal_due = session.last_seen_at <= now - timedelta(
        minutes=1
    ) or session.expires_at <= now + timedelta(minutes=1)
    return Principal(user, session, role_codes(db, user.id))


Authenticated = Annotated[Principal, Depends(current_user)]


def api_machine_path(path: str) -> bool:
    return bool(
        re.fullmatch(
            r"/api/(?:repositories(?:/.*)?|attachments/[0-9]+|me/notifications(?:/.*)?|cli/me)",
            path,
        )
    )


def bearer_user(request: Request, db: Session) -> Principal:
    value = request.headers.get("authorization", "")
    match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{64})", value, re.IGNORECASE)
    if not match:
        raise HTTPException(401, "CLI 授权无效，请运行 devops auth login")
    row = db.execute(
        select(GitOAuthToken, GitOAuthGrant, User)
        .join(GitOAuthGrant, GitOAuthGrant.id == GitOAuthToken.grant_id)
        .join(User, User.id == GitOAuthGrant.user_id)
        .where(GitOAuthToken.access_token == match[1])
        .execution_options(populate_existing=True)
    ).first()
    now = datetime.now(UTC)
    if row is None:
        raise HTTPException(401, "CLI 授权已失效")
    token, grant, user = row
    if (
        token.rotated_at
        or token.expires_at <= now
        or grant.revoked_at
        or grant.expires_at <= now
        or user.status != "active"
    ):
        raise HTTPException(401, "CLI 授权已失效")
    scope = "api:read" if request.method in {"GET", "HEAD", "OPTIONS"} else "api:write"
    if (
        not request.app.state.settings.git_oauth_enabled
        or grant.client_id != "devops-cli"
        or not api_machine_path(request.url.path)
        or scope not in grant.scope.split()
    ):
        raise HTTPException(403, "此授权没有所需的 API 权限")
    request.state.user_id = user.id
    request.state.oauth_grant_id = grant.id
    return Principal(user, None, role_codes(db, user.id), grant)


def repository_user(request: Request, db: Database) -> Principal:
    if request.headers.get("authorization"):
        return bearer_user(request, db)
    return current_user(request, db)


RepositoryAuthenticated = Annotated[Principal, Depends(repository_user)]


def administrator(actor: Authenticated) -> Principal:
    if "admin" not in actor.roles:
        raise HTTPException(403, "此操作需要管理员权限")
    return actor


Administrator = Annotated[Principal, Depends(administrator)]
