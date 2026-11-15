import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from devops_server.dependencies import (
    Authenticated,
    Database,
    Principal,
    administrator,
    current_user,
)
from devops_server.mail import MailDeliveryError
from devops_server.models import EmailChallenge, User
from devops_server.schemas import EmailCodeRequest, LoginRequest, RegisterRequest
from devops_server.security import (
    clear_session_cookie,
    client_ip,
    code_digest,
    set_csrf_cookie,
    set_session_cookie,
)
from devops_server.services import (
    advisory_lock,
    audit,
    create_member,
    create_session,
    fail,
    user_out,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.get("/csrf")
def csrf(request: Request, response: Response):
    settings = request.app.state.settings
    return {
        "csrf_token": set_csrf_cookie(
            response, settings, request.cookies.get(settings.session_cookie_name, "")
        ),
        "registration_enabled": settings.registration_enabled,
    }


@router.post("/email-codes", status_code=202)
def send_email_code(body: EmailCodeRequest, request: Request, db: Database):
    actor = (
        current_user(request, db)
        if body.purpose
        in {"change_email", "legacy_password", "admin_operation", "repository_operation"}
        else None
    )
    if body.purpose == "admin_operation":
        administrator(actor)
        if body.email != actor.user.email:
            raise HTTPException(422, "管理操作验证码只能发送到当前管理员的邮箱")
    if body.purpose == "legacy_password" and body.email != actor.user.email:
        raise HTTPException(422, "兼容密码验证码只能发送到当前账号的邮箱")
    if body.purpose == "repository_operation" and body.email != actor.user.email:
        raise HTTPException(422, "仓库操作验证码只能发送到当前账号的邮箱")
    return issue_email_code(body, request, db, actor)


def issue_email_code(
    body: EmailCodeRequest, request: Request, db: Session, actor: Principal | None = None
):
    """Shared delivery after the entry point has validated identity and purpose."""
    settings = request.app.state.settings
    if not settings.smtp_configured:
        raise HTTPException(503, "邮箱服务尚未配置，请联系管理员设置 SMTP")
    if body.purpose == "register" and not settings.registration_enabled:
        raise HTTPException(403, "暂未开放注册，请联系管理员")
    ip = client_ip(request)
    try:
        reservation = request.app.state.email_code_limiter.reserve(body.email, ip, settings)
    except RedisError:
        raise HTTPException(503, "验证码服务暂时不可用，请稍后重试") from None
    if not reservation.allowed:
        audit(
            db,
            request,
            "auth.code_requested",
            result="failure",
            details={"reason": "rate_limited"},
            actor_id=actor.user.id if actor else None,
        )
        db.commit()
        raise HTTPException(
            429,
            "验证码申请过于频繁，请稍后再试",
            headers={"Retry-After": str(reservation.retry_after)},
        )
    # Serialize challenge replacement; rate limits themselves live in Redis.
    advisory_lock(db, f"email-send:{body.email}")
    now = datetime.now(UTC)
    db.execute(
        update(EmailChallenge)
        .where(
            EmailChallenge.email == body.email,
            EmailChallenge.purpose == body.purpose,
            EmailChallenge.subject_user_id == (actor.user.id if actor else None),
            EmailChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    nonce = secrets.token_urlsafe(24)
    challenge = EmailChallenge(
        email=body.email,
        purpose=body.purpose,
        nonce=nonce,
        subject_user_id=actor.user.id if actor else None,
        code_digest=code_digest(settings, body.email, body.purpose, nonce, code),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.email_code_seconds),
        request_ip=ip,
    )
    db.add(challenge)
    db.flush()
    try:
        request.app.state.mailer.send_code(body.email, code, body.purpose)
    except MailDeliveryError:
        challenge.consumed_at = now
        fail(
            db,
            request,
            "auth.code_requested",
            503,
            "验证码邮件暂时无法发送，请稍后重试",
            reason="mail_delivery_failed",
            actor_id=actor.user.id if actor else None,
        )
    audit(
        db,
        request,
        "auth.code_requested",
        actor_id=actor.user.id if actor else None,
        details={"purpose": body.purpose},
    )
    return {
        "message": "验证码已发送，请查看邮箱",
        "retry_after": settings.email_code_resend_seconds,
    }


def verify_code(
    db: Session,
    request: Request,
    email: str,
    purpose: str,
    code: str,
    subject_user_id: int | None = None,
) -> None:
    settings = request.app.state.settings
    challenge = db.scalar(
        select(EmailChallenge)
        .where(
            EmailChallenge.email == email,
            EmailChallenge.purpose == purpose,
            EmailChallenge.subject_user_id == subject_user_id,
        )
        .order_by(EmailChallenge.id.desc())
        .limit(1)
        .with_for_update()
    )
    now = datetime.now(UTC)
    if (
        challenge is None
        or challenge.consumed_at is not None
        or challenge.expires_at <= now
        or challenge.attempts >= settings.email_code_attempts
    ):
        fail(
            db,
            request,
            "auth.code_verified",
            400,
            "验证码无效或已过期，请重新申请",
            actor_id=subject_user_id,
            reason="invalid_or_expired",
        )
    challenge.attempts += 1
    expected = code_digest(settings, email, purpose, challenge.nonce, code)
    if not hmac.compare_digest(challenge.code_digest, expected):
        if challenge.attempts >= settings.email_code_attempts:
            challenge.consumed_at = now
        fail(
            db,
            request,
            "auth.code_verified",
            400,
            "验证码错误或已失效",
            actor_id=subject_user_id,
            reason="incorrect_code",
        )
    challenge.consumed_at = now


def login_result(db: Session, request: Request, response: Response, user: User):
    session, token = create_session(db, request, user)
    audit(db, request, "auth.login", actor_id=user.id, target_type="session", target_id=session.id)
    return {
        "user": user_out(db, user),
        "csrf_token": set_session_cookie(response, request.app.state.settings, token),
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Database):
    verify_code(db, request, body.email, "login", body.code)
    user = db.scalar(select(User).where(User.email == body.email).with_for_update())
    if user is None:
        fail(
            db,
            request,
            "auth.login",
            400,
            "该邮箱尚未注册，请先注册或联系管理员",
            reason="user_missing",
        )
    if user.status != "active":
        fail(
            db,
            request,
            "auth.login",
            403,
            "账号已停用，请联系管理员",
            actor_id=user.id,
            reason="user_disabled",
        )
    return login_result(db, request, response, user)


@router.post("/register", status_code=201)
def register(body: RegisterRequest, request: Request, response: Response, db: Database):
    if not request.app.state.settings.registration_enabled:
        raise HTTPException(403, "暂未开放注册，请联系管理员")
    verify_code(db, request, body.email, "register", body.code)
    user = create_member(db, body.model_dump(exclude={"code"}))
    audit(db, request, "user.registered", actor_id=user.id, target_type="user", target_id=user.id)
    return login_result(db, request, response, user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Database, actor: Authenticated):
    actor.session.revoked_at = datetime.now(UTC)
    actor.session.revoke_reason = "logout"
    audit(
        db,
        request,
        "auth.logout",
        actor_id=actor.user.id,
        target_type="session",
        target_id=actor.session.id,
    )
    clear_session_cookie(response, request.app.state.settings)
    return {"message": "已退出登录"}
