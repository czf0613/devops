"""Consume one email code per administrative mutation, in its transaction."""

from datetime import UTC, datetime

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from devops_server.auth import verify_code
from devops_server.dependencies import Principal, role_codes
from devops_server.services import audit


def verify_admin_operation(db: Session, request: Request, actor: Principal, code: str):
    # Call only before mutating business data: verify_code persists failed attempts.
    email = actor.user.email
    verify_code(db, request, email, "admin_operation", code, actor.user.id)
    # A lock wait must not allow a revoked session, changed email or removed admin role.
    db.refresh(actor.user)
    db.refresh(actor.session)
    if (
        actor.user.email != email
        or actor.user.status != "active"
        or actor.session.revoked_at is not None
        or actor.session.expires_at <= datetime.now(UTC)
        or actor.session.password_version != actor.user.password_version
    ):
        raise HTTPException(401, "登录状态已变更，请重新登录")
    if "admin" not in role_codes(db, actor.user.id):
        raise HTTPException(403, "此操作需要管理员权限")
    audit(
        db,
        request,
        "admin.operation_verified",
        actor_id=actor.user.id,
        details={"method": request.method, "route": request.scope["route"].path},
    )
