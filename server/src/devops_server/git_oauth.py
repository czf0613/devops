"""PKCE login for GCM with live, per-repository Git authorization."""

import base64
import binascii
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, StrictBool
from redis.exceptions import RedisError
from sqlalchemy import or_, select

from devops_server.dependencies import Authenticated, Database, RepositoryAuthenticated
from devops_server.models import (
    GitOAuthGrant,
    GitOAuthRequest,
    GitOAuthToken,
    OAuthDeviceRequest,
    Repository,
    User,
)
from devops_server.repositories import repository_access
from devops_server.security import client_ip, digest
from devops_server.services import audit

router = APIRouter(tags=["git-oauth"])
CLIENT_ID = "devops-gcm"
SCOPES = {"git:read", "git:write"}
CLIENTS = {
    CLIENT_ID: ("Git Credential Manager", SCOPES),
    "devops-cli": ("DevOps CLI", {"api:read", "api:write"}),
}
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{64}")
FLOW_COOKIE = "devops_git_oauth_flow"
GRANT_LIFETIME = timedelta(days=30)


class OAuthError(Exception):
    def __init__(self, error: str, status: int = 400):
        self.error, self.status = error, status


def enabled(request: Request) -> None:
    if not request.app.state.settings.git_oauth_enabled:
        raise HTTPException(404, "Git OAuth 登录尚未启用")


def user_active(db: Database, user_id: int | None) -> bool:
    user = db.get(User, user_id) if user_id is not None else None
    return bool(user and user.status == "active")


def parameters(pairs: list[tuple[str, str]]) -> dict[str, str]:
    values = dict(pairs)
    if len(values) != len(pairs):
        raise OAuthError("invalid_request")
    return values


async def form_values(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != (
        "application/x-www-form-urlencoded"
    ):
        raise OAuthError("invalid_request")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            raise OAuthError("invalid_request")
    try:
        return parameters(
            parse_qsl(
                body.decode("utf-8"), keep_blank_values=True, errors="strict", max_num_fields=16
            )
        )
    except ValueError, UnicodeError:
        raise OAuthError("invalid_request") from None


FormValues = Annotated[dict[str, str], Depends(form_values)]


def validate_client(values: dict[str, str]) -> None:
    # GCM's generic provider can send an empty client_secret for a public client.
    if values.get("client_id") not in CLIENTS or values.get("client_secret", ""):
        raise OAuthError("invalid_client", 401)


def validate_scope(values: dict[str, str]) -> str:
    scope = set(values.get("scope", "").split())
    if not scope or not scope <= CLIENTS[values["client_id"]][1]:
        raise OAuthError("invalid_scope")
    return " ".join(sorted(scope))


def limit_start(request: Request, purpose: str = "start") -> None:
    key = (
        request.app.state.email_code_limiter.namespace
        + ":oauth-"
        + purpose
        + ":"
        + digest(request.app.state.settings, client_ip(request) or "unknown")
    )
    try:
        count = request.app.state.redis.eval(
            "local n=redis.call('INCR',KEYS[1]); "
            "if n==1 then redis.call('EXPIRE',KEYS[1],3600) end; return n",
            1,
            key,
        )
    except RedisError:
        raise OAuthError("temporarily_unavailable", 503) from None
    if count > 60:
        raise OAuthError("temporarily_unavailable", 429)


def validate_callback(uri: str) -> None:
    # GCM binds an ephemeral IPv4 loopback port. Exact root path, no query/fragment/userinfo.
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})/?", uri)
    if match is None or not 1 <= int(match[1]) <= 65535:
        raise OAuthError("invalid_request")


@router.get("/oauth/authorize", dependencies=[Depends(enabled)])
def authorize(request: Request, db: Database):
    values = parameters(list(request.query_params.multi_items()))
    validate_client(values)
    if values.get("response_type") != "code":
        raise OAuthError("unsupported_response_type")
    redirect_uri = values.get("redirect_uri", "")
    validate_callback(redirect_uri)
    scope = validate_scope(values)
    challenge = values.get("code_challenge", "")
    if values.get("code_challenge_method") != "S256" or not re.fullmatch(
        r"[A-Za-z0-9_-]{43}", challenge
    ):
        raise OAuthError("invalid_request")
    state = values.get("state", "")
    if not 1 <= len(state) <= 1024 or len(request.url.query) > 4096:
        raise OAuthError("invalid_request")
    # Bound anonymous request creation across all server instances, using Redis DB 0.
    key = (
        request.app.state.email_code_limiter.namespace
        + ":oauth-start:"
        + digest(request.app.state.settings, client_ip(request) or "unknown")
    )
    try:
        count = request.app.state.redis.eval(
            "local n=redis.call('INCR',KEYS[1]); "
            "if n==1 then redis.call('EXPIRE',KEYS[1],3600) end; return n",
            1,
            key,
        )
    except RedisError:
        raise OAuthError("temporarily_unavailable", 503) from None
    if count > 60:
        raise OAuthError("temporarily_unavailable", 429)
    binding = secrets.token_urlsafe(48)
    pending = GitOAuthRequest(
        client_id=values["client_id"],
        public_id=secrets.token_urlsafe(32),
        browser_digest=digest(request.app.state.settings, binding),
        redirect_uri=redirect_uri,
        state=state,
        challenge=challenge,
        scope=scope,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add(pending)
    response = RedirectResponse("/oauth/consent?request=" + pending.public_id, status_code=302)
    response.set_cookie(
        FLOW_COOKIE,
        binding,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.session_cookie_secure,
        path=f"/api/oauth/authorization-requests/{pending.public_id}",
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def pending_request(request: Request, db: Database, public_id: str) -> GitOAuthRequest:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", public_id):
        raise HTTPException(404, "授权请求不存在或已过期")
    pending = db.scalar(
        select(GitOAuthRequest).where(GitOAuthRequest.public_id == public_id).with_for_update()
    )
    binding = request.cookies.get(FLOW_COOKIE, "")
    if (
        pending is None
        or not TOKEN_PATTERN.fullmatch(binding)
        or not secrets.compare_digest(
            pending.browser_digest, digest(request.app.state.settings, binding)
        )
        or pending.expires_at <= datetime.now(UTC)
        or pending.decided_at is not None
    ):
        raise HTTPException(404, "授权请求不存在或已过期，请从 Git 重新发起")
    return pending


@router.get("/api/oauth/authorization-requests/{public_id}", dependencies=[Depends(enabled)])
def authorization_request(public_id: str, request: Request, actor: Authenticated, db: Database):
    pending = pending_request(request, db, public_id)
    return {
        "client_name": CLIENTS[pending.client_id][0],
        "scope": pending.scope.split(),
        "redirect_uri": pending.redirect_uri,
        "expires_at": pending.expires_at,
    }


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: StrictBool


@router.post(
    "/api/oauth/authorization-requests/{public_id}/decision", dependencies=[Depends(enabled)]
)
def decide(public_id: str, body: Decision, request: Request, actor: Authenticated, db: Database):
    pending = pending_request(request, db, public_id)
    now = datetime.now(UTC)
    pending.decided_at = now
    pending.user_id = actor.user.id
    callback = {"state": pending.state}
    if body.approve:
        pending.code = secrets.token_urlsafe(48)
        pending.code_expires_at = now + timedelta(seconds=60)
        callback["code"] = pending.code
    else:
        callback["error"] = "access_denied"
    audit(
        db,
        request,
        "git_oauth.approve" if body.approve else "git_oauth.deny",
        actor_id=actor.user.id,
    )
    return {"redirect_uri": pending.redirect_uri + "?" + urlencode(callback)}


def mint_token(db: Database, grant: GitOAuthGrant) -> dict:
    now = datetime.now(UTC)
    token = GitOAuthToken(
        grant_id=grant.id,
        access_token=secrets.token_urlsafe(48),
        refresh_token=secrets.token_urlsafe(48),
        expires_at=min(now + timedelta(hours=1), grant.expires_at),
    )
    db.add(token)
    return {
        "access_token": token.access_token,
        "token_type": "Bearer",
        "expires_in": max(1, int((token.expires_at - now).total_seconds())),
        "refresh_token": token.refresh_token,
        "scope": grant.scope,
    }


@router.post("/oauth/token", dependencies=[Depends(enabled)])
def token_exchange(request: Request, values: FormValues, db: Database):
    validate_client(values)
    now = datetime.now(UTC)
    if values.get("grant_type") == "authorization_code":
        code = values.get("code", "")
        if not TOKEN_PATTERN.fullmatch(code):
            raise OAuthError("invalid_grant")
        pending = db.scalar(
            select(GitOAuthRequest).where(GitOAuthRequest.code == code).with_for_update()
        )
        verifier = values.get("code_verifier", "")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if (
            pending is None
            or pending.client_id != values["client_id"]
            or pending.consumed_at is not None
            or pending.code_expires_at is None
            or pending.code_expires_at <= now
            or values.get("redirect_uri") != pending.redirect_uri
            or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
            or not secrets.compare_digest(challenge, pending.challenge)
            or not user_active(db, pending.user_id)
        ):
            raise OAuthError("invalid_grant")
        pending.consumed_at = now
        grant = GitOAuthGrant(
            user_id=pending.user_id,
            client_id=pending.client_id,
            scope=pending.scope,
            expires_at=now + GRANT_LIFETIME,
        )
        db.add(grant)
        db.flush()
        audit(
            db,
            request,
            "git_oauth.created",
            actor_id=grant.user_id,
            target_type="git_oauth_grant",
            target_id=grant.id,
        )
        return mint_token(db, grant)
    if values.get("grant_type") == "refresh_token":
        raw = values.get("refresh_token", "")
        if not TOKEN_PATTERN.fullmatch(raw):
            raise OAuthError("invalid_grant")
        grant_id = db.scalar(
            select(GitOAuthToken.grant_id).where(GitOAuthToken.refresh_token == raw)
        )
        if grant_id is None:
            raise OAuthError("invalid_grant")
        # A single grant lock serializes refresh and revocation, including replay detection.
        grant = db.scalar(
            select(GitOAuthGrant).where(GitOAuthGrant.id == grant_id).with_for_update()
        )
        token = db.scalar(select(GitOAuthToken).where(GitOAuthToken.refresh_token == raw))
        # Lock waits must not let a grant that has since expired renew itself.
        now = datetime.now(UTC)
        if (
            grant.client_id != values["client_id"]
            or grant.revoked_at is not None
            or grant.expires_at <= now
        ):
            raise OAuthError("invalid_grant")
        if token.rotated_at is not None or not user_active(db, grant.user_id):
            grant.revoked_at = now
            audit(
                db,
                request,
                "git_oauth.revoked",
                actor_id=grant.user_id,
                target_type="git_oauth_grant",
                target_id=grant.id,
                details={"reason": "refresh_reuse_or_ineligible"},
            )
            db.commit()  # Revocation must survive the protocol error response.
            raise OAuthError("invalid_grant")
        if "scope" in values and set(values["scope"].split()) != set(grant.scope.split()):
            raise OAuthError("invalid_scope")
        token.rotated_at = now
        grant.expires_at = now + GRANT_LIFETIME
        return mint_token(db, grant)
    if values.get("grant_type") == "urn:ietf:params:oauth:grant-type:device_code":
        return exchange_device(request, values, db)
    raise OAuthError("unsupported_grant_type")


def revoke_grant(db: Database, request: Request, grant: GitOAuthGrant) -> None:
    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(UTC)
        audit(
            db,
            request,
            "git_oauth.revoked",
            actor_id=grant.user_id,
            target_type="git_oauth_grant",
            target_id=grant.id,
        )


@router.post("/oauth/revoke")
def revoke(request: Request, values: FormValues, db: Database):
    validate_client(values)
    raw = values.get("token", "")
    if TOKEN_PATTERN.fullmatch(raw):
        grant_id = db.scalar(
            select(GitOAuthToken.grant_id).where(
                or_(GitOAuthToken.access_token == raw, GitOAuthToken.refresh_token == raw)
            )
        )
        if grant_id is not None:
            grant = db.scalar(
                select(GitOAuthGrant).where(GitOAuthGrant.id == grant_id).with_for_update()
            )
            if grant.client_id == values["client_id"]:
                revoke_grant(db, request, grant)
    return Response(status_code=200)  # RFC 7009: unknown/already revoked is also success.


@router.get("/api/me/git-authorizations")
def authorizations(actor: Authenticated, db: Database):
    grants = db.scalars(
        select(GitOAuthGrant)
        .where(GitOAuthGrant.user_id == actor.user.id, GitOAuthGrant.revoked_at.is_(None))
        .order_by(GitOAuthGrant.id.desc())
        .limit(100)
    )
    return [
        {
            "id": g.id,
            "client_name": CLIENTS.get(g.client_id, (g.client_id, set()))[0],
            "scope": g.scope.split(),
            "created_at": g.created_at,
            "expires_at": g.expires_at,
            "revoked_at": g.revoked_at,
        }
        for g in grants
    ]


@router.delete("/api/me/git-authorizations/{grant_id}")
def delete_authorization(grant_id: int, request: Request, actor: Authenticated, db: Database):
    grant = db.scalar(
        select(GitOAuthGrant)
        .where(GitOAuthGrant.id == grant_id, GitOAuthGrant.user_id == actor.user.id)
        .with_for_update()
    )
    if grant is None:
        raise HTTPException(404, "授权记录不存在")
    revoke_grant(db, request, grant)
    return {"message": "客户端授权已撤销"}


@router.get("/api/cli/me")
def cli_identity(actor: RepositoryAuthenticated):
    return {
        "id": actor.user.id,
        "login": actor.user.username,
        "name": actor.user.display_name,
        "scopes": actor.grant.scope.split() if actor.grant else [],
        "grant_id": actor.grant.id if actor.grant else None,
    }


@router.post("/oauth/device/code", dependencies=[Depends(enabled)])
def device_code(request: Request, values: FormValues, db: Database):
    validate_client(values)
    if values["client_id"] != "devops-cli":
        raise OAuthError("unauthorized_client")
    scope = validate_scope(values)
    limit_start(request)
    device = secrets.token_urlsafe(48)
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    code = code[:4] + "-" + code[4:]
    db.add(
        OAuthDeviceRequest(
            device_digest=digest(request.app.state.settings, "device:" + device),
            user_code_digest=digest(request.app.state.settings, "user-code:" + code),
            client_id=values["client_id"],
            scope=scope,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )
    # Use a configured trusted origin, never a client-supplied redirect.
    origins = request.app.state.settings.allowed_origins
    origin = next(
        (value for value in origins if value.split("://", 1)[-1] == request.headers.get("host")),
        origins[0],
    )
    verification = origin.rstrip("/") + "/oauth/device"
    return {
        "device_code": device,
        "user_code": code,
        "verification_uri": verification,
        "verification_uri_complete": verification + "?user_code=" + code,
        "expires_in": 600,
        "interval": 5,
    }


def device_request(request: Request, db: Database, code: str):
    limit_start(request, "device-confirm")
    code = code.strip().upper()
    if not re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", code):
        raise HTTPException(404, "设备码不存在或已过期")
    pending = db.scalar(
        select(OAuthDeviceRequest)
        .where(
            OAuthDeviceRequest.user_code_digest
            == digest(request.app.state.settings, "user-code:" + code)
        )
        .with_for_update()
    )
    if not pending or pending.expires_at <= datetime.now(UTC) or pending.decided_at:
        raise HTTPException(404, "设备码不存在或已过期")
    return pending


@router.get("/api/oauth/device-requests/{code}", dependencies=[Depends(enabled)])
def device_details(code: str, request: Request, actor: Authenticated, db: Database):
    pending = device_request(request, db, code)
    return {
        "client_name": CLIENTS[pending.client_id][0],
        "scope": pending.scope.split(),
        "expires_at": pending.expires_at,
    }


@router.post("/api/oauth/device-requests/{code}/decision", dependencies=[Depends(enabled)])
def device_decide(code: str, body: Decision, request: Request, actor: Authenticated, db: Database):
    pending = device_request(request, db, code)
    pending.decided_at = datetime.now(UTC)
    pending.user_id = actor.user.id
    pending.approved = body.approve
    audit(
        db,
        request,
        "cli_oauth.approve" if body.approve else "cli_oauth.deny",
        actor_id=actor.user.id,
    )
    return {"ok": True}


def exchange_device(request: Request, values: dict[str, str], db: Database):
    raw = values.get("device_code", "")
    if not TOKEN_PATTERN.fullmatch(raw):
        raise OAuthError("invalid_grant")
    pending = db.scalar(
        select(OAuthDeviceRequest)
        .where(
            OAuthDeviceRequest.device_digest == digest(request.app.state.settings, "device:" + raw)
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if not pending or pending.client_id != values["client_id"] or pending.consumed_at:
        raise OAuthError("invalid_grant")
    if pending.expires_at <= now:
        raise OAuthError("expired_token")
    too_fast = (
        pending.last_poll_at and (now - pending.last_poll_at).total_seconds() < pending.interval
    )
    pending.last_poll_at = now
    if too_fast:
        pending.interval += 5
        db.commit()
        raise OAuthError("slow_down")
    if not pending.decided_at:
        db.commit()
        raise OAuthError("authorization_pending")
    if not pending.approved or not user_active(db, pending.user_id):
        pending.consumed_at = now
        db.commit()
        raise OAuthError("access_denied")
    pending.consumed_at = now
    grant = GitOAuthGrant(
        user_id=pending.user_id,
        client_id=pending.client_id,
        scope=pending.scope,
        expires_at=now + GRANT_LIFETIME,
    )
    db.add(grant)
    db.flush()
    audit(
        db,
        request,
        "cli_oauth.created",
        actor_id=grant.user_id,
        target_type="git_oauth_grant",
        target_id=grant.id,
    )
    return mint_token(db, grant)


def basic_token(request: Request) -> str:
    try:
        scheme, encoded = request.headers.get("authorization", "").split(" ", 1)
        if scheme.lower() != "basic" or len(encoded) > 512:
            return ""
        username, token = base64.b64decode(encoded, validate=True).decode("ascii").split(":", 1)
        return token if username == "oauth2" and TOKEN_PATTERN.fullmatch(token) else ""
    except ValueError, UnicodeError, binascii.Error:
        return ""


def authenticate_git(request: Request, db: Database) -> GitOAuthGrant:
    """The same revocable credential for Git and LFS; never fall back to cookies."""
    challenge = {"WWW-Authenticate": 'Basic realm="DevOps Git", charset="UTF-8"'}
    raw = basic_token(request)
    if not raw:
        raise HTTPException(401, "Git credentials required", headers=challenge)
    row = db.execute(
        select(GitOAuthToken, GitOAuthGrant)
        .join(GitOAuthGrant, GitOAuthGrant.id == GitOAuthToken.grant_id)
        .where(GitOAuthToken.access_token == raw)
        .execution_options(populate_existing=True)
    ).first()
    now = datetime.now(UTC)
    if row is None:
        raise HTTPException(401, "Git credentials required", headers=challenge)
    token, grant = row
    if (
        token.rotated_at is not None
        or token.expires_at <= now
        or grant.revoked_at is not None
        or grant.expires_at <= now
    ):
        raise HTTPException(401, "Git credentials expired or revoked", headers=challenge)
    if not request.app.state.settings.git_oauth_enabled or not user_active(db, grant.user_id):
        raise HTTPException(403, "Git access disabled")
    return grant


@router.get("/_internal/git/authorize", include_in_schema=False)
def git_authorize(request: Request, db: Database):
    # Reachable only through nginx's internal subrequest/private network.
    grant = authenticate_git(request, db)
    method = request.headers.get("x-original-method")
    uri = request.headers.get("x-original-uri")
    operation = request.headers.get("x-git-operation")
    service = {"read": "git-upload-pack", "write": "git-receive-pack"}.get(operation)
    owner = request.headers.get("x-git-owner", "")
    name = request.headers.get("x-git-repository", "")
    prefix = f"/git/{owner}/{name}.git"
    if (
        not re.fullmatch(r"[a-z][a-z0-9_.-]{2,31}", owner)
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", name)
        or service is None
        or f"git:{operation}" not in grant.scope.split()
        or (method, uri)
        not in {
            ("GET", f"{prefix}/info/refs?service={service}"),
            ("POST", f"{prefix}/{service}"),
        }
    ):
        return Response(status_code=403)
    repository = db.scalar(
        select(Repository)
        .join(User, User.id == Repository.owner_id)
        .where(User.username == owner, Repository.name == name, Repository.deleted_at.is_(None))
    )
    access = repository_access(db, repository, grant.user_id) if repository else None
    if repository and grant.client_id == "devops-ci":
        from devops_server.ci_service import authorize_task

        authorize_task(db, grant, repository.id, write=operation == "write")
    if access is None or (operation == "write" and access != "write"):
        return Response(status_code=403)
    return Response(
        status_code=204,
        headers={
            "X-OA-User-ID": str(grant.user_id),
            "X-Git-Repository-ID": str(repository.id),
            "X-Git-Protect-Main": str(int(repository.protect_main)),
            "X-Git-Protect-Push": str(int(repository.protect_push)),
        },
    )
