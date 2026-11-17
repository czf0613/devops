import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from devops_server import (
    auth,
    ci_api,
    ci_socket,
    collaboration_content,
    collaboration_issues,
    collaboration_pulls,
    dashboard,
    git_lfs,
    git_oauth,
    registry,
    registry_admin,
    repositories,
    repository_admin,
    repository_code,
    system_status,
    users,
)
from devops_server.config import ConnectionSettings, Settings, get_connections
from devops_server.config_store import load_settings
from devops_server.database import build_engine
from devops_server.dependencies import api_machine_path, bearer_user
from devops_server.mail import SMTPMailer
from devops_server.rate_limits import EmailCodeRateLimiter, build_redis
from devops_server.security import renew_session_cookies, valid_csrf
from devops_server.services import audit


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    mailer: SMTPMailer | None = None,
    connections: ConnectionSettings | None = None,
    redis_client: Redis | None = None,
    rate_limiter: EmailCodeRateLimiter | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bootstrap = connections or get_connections()
        app.state.engine = engine or build_engine(bootstrap)
        app.state.redis = redis_client or build_redis(bootstrap)
        try:
            app.state.settings = settings or await run_in_threadpool(
                load_settings, app.state.engine
            )
            app.state.settings.require_auth_secrets()
            app.state.mailer = mailer or SMTPMailer(app.state.settings)
            app.state.email_code_limiter = rate_limiter or EmailCodeRateLimiter(app.state.redis)
            yield
        finally:
            if engine is None:
                app.state.engine.dispose()
            if redis_client is None:
                app.state.redis.close()

    app = FastAPI(
        title="DevOps API",
        version=version("devops-server"),
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    def record_rejected_request(request: Request, status: int):
        try:
            with Session(request.app.state.engine) as db:
                route = request.scope.get("route")
                audit(
                    db,
                    request,
                    "request.rejected",
                    actor_id=request.state.user_id,
                    result="failure",
                    details={
                        "status": status,
                        "method": request.method,
                        "route": getattr(route, "path", "unmatched"),
                    },
                )
                db.commit()
        except SQLAlchemyError:
            pass  # A secondary audit failure must not hide the original database error.

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.request_id = secrets.token_hex(16)
        # Exact machine endpoints use OAuth credentials, never browser cookies.
        oauth_machine = request.method == "POST" and request.url.path in {
            "/oauth/token",
            "/oauth/revoke",
            "/oauth/device/code",
        }
        api_machine = False
        if request.headers.get("authorization") and api_machine_path(request.url.path):

            def validate_bearer():
                with Session(request.app.state.engine) as db:
                    bearer_user(request, db)

            try:
                await run_in_threadpool(validate_bearer)
                api_machine = True
            except HTTPException as error:
                response = JSONResponse(
                    status_code=error.status_code, content={"detail": error.detail}
                )
                response.headers["Cache-Control"] = "no-store"
                response.headers["X-Request-ID"] = request.state.request_id
                return response
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and not oauth_machine
            and not api_machine
            and not git_lfs.machine_request(request)
            and not ci_api.machine_request(request)
            and not valid_csrf(request)
        ):
            response = JSONResponse(
                status_code=403, content={"detail": "请求验证失败，请刷新页面后重试"}
            )
        else:
            response = await call_next(request)
        if response.status_code < 400 and (
            expires_at := getattr(request.state, "renewed_session_expiry", None)
        ):
            renew_session_cookies(response, request, expires_at)
        if (
            response.status_code >= 400
            and getattr(request.state, "user_id", None)
            and not request.url.path.startswith("/api/auth/")
        ):
            await run_in_threadpool(record_rejected_request, request, response.status_code)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Pydantic's default errors echo submitted values, including passwords and codes.
        errors = [
            {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError):
        return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请稍后重试"})

    @app.exception_handler(git_oauth.OAuthError)
    async def oauth_error(request: Request, error: git_oauth.OAuthError):
        return JSONResponse(
            status_code=error.status, content={"error": error.error}, headers={"Pragma": "no-cache"}
        )

    @app.exception_handler(git_lfs.LFSError)
    async def lfs_error(request: Request, error: git_lfs.LFSError):
        return JSONResponse(
            {"message": error.detail},
            status_code=error.status_code,
            headers=error.headers,
            media_type=git_lfs.MEDIA_TYPE,
        )

    app.include_router(auth.router)
    app.include_router(ci_api.router)
    app.include_router(ci_socket.router)
    app.include_router(users.router)
    app.include_router(repositories.router)
    app.include_router(repository_admin.router)
    app.include_router(registry.router)
    app.include_router(registry_admin.router)
    app.include_router(system_status.router)
    app.include_router(repository_code.router)
    app.include_router(collaboration_issues.router)
    app.include_router(collaboration_content.router)
    app.include_router(collaboration_pulls.router)
    app.include_router(dashboard.router)
    app.include_router(git_oauth.router)
    app.include_router(git_lfs.router)

    @app.get("/api/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "devops-server"}

    @app.get("/api/health/ready", tags=["health"])
    def readiness(request: Request) -> JSONResponse:
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "unavailable"},
            )
        try:
            request.app.state.redis.ping()
        except RedisError:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "connected", "redis": "unavailable"},
            )
        return JSONResponse(
            content={"status": "ready", "database": "connected", "redis": "connected"}
        )

    return app


app = create_app()
