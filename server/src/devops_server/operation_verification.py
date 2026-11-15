"""Single-use email verification shared by browser and CLI repository operations."""

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from devops_server.auth import verify_code
from devops_server.dependencies import Principal, repository_user
from devops_server.services import audit


def verify_repository_operation(db: Session, request: Request, actor: Principal, code: str):
    # Must precede business mutations: incorrect attempts are committed by verify_code.
    email = actor.user.email
    verify_code(db, request, email, "repository_operation", code, actor.user.id)
    db.refresh(actor.user)
    if actor.session is not None:
        db.refresh(actor.session)
    # Revalidate the exact Cookie session or CLI Bearer after the challenge lock wait.
    current = repository_user(request, db)
    if current.user.id != actor.user.id or current.user.email != email:
        raise HTTPException(401, "账号或邮箱已变更，请重新验证")
    audit(
        db,
        request,
        "repository.operation_verified",
        actor_id=actor.user.id,
        details={"method": request.method, "route": request.scope["route"].path},
    )
