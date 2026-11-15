"""Local Docker/Git integration check; isolated schema and files, no real email or deployment."""

import asyncio
import base64
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from deploy import REGISTRY_IMAGE, native_platform
from deploy_website import assets_in_html, package
from deploy_website import digest as file_digest
from devops_server.config import get_connections
from devops_server.config_store import load_settings, update_configuration
from devops_server.database import build_engine
from devops_server.models import (
    AuditEvent,
    GitOAuthGrant,
    GitOAuthToken,
    Role,
    User,
    UserRole,
    UserSession,
)
from devops_server.rate_limits import build_redis
from devops_server.security import digest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from verify_ci import verify_ci
from verify_http3 import get_http3
from verify_registry import verify_registry
from verify_registry_gc import synthetic_email_code, verify_registry_gc

SERVER = "devops.kevinc.ltd/core/devops:latest"
GATEWAY = "devops.kevinc.ltd/core/devops-gateway:latest"


def run(*args, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, check=False, **kwargs)
    if result.returncode:
        # Commands contain only paths/config names, never token values or passwords.
        raise RuntimeError(f"Command failed: {args[0:3]}\n{result.stderr}")
    return result.stdout.strip()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    run("git", "lfs", "version")
    platform = native_platform()
    for image in (SERVER, GATEWAY):
        actual = run(
            "docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image
        )
        if actual != platform:
            raise RuntimeError(f"Rebuild {image} for native {platform}; found {actual}")
    dist = Path(__file__).resolve().parents[1] / "website" / "dist"
    devops = Path(__file__).resolve().parents[1]
    cli_version = json.loads((devops / "cli/package.json").read_text())["version"]
    cli_bundle = devops / ".data/cli-releases" / f"{cli_version}.tgz"
    if not cli_bundle.is_file():
        raise RuntimeError(
            "Build the CLI tarball with scripts/deploy_cli.py build first"
        )
    if not (dist / "index.html").is_file():
        raise RuntimeError(
            "Build the local website with pnpm before running integration checks"
        )
    connections = get_connections()
    if connections.postgres_host not in {"127.0.0.1", "localhost"} or (
        connections.redis_host not in {"127.0.0.1", "localhost"}
    ):
        raise RuntimeError(
            "Integration check requires the local development PostgreSQL and Redis"
        )
    suffix = secrets.token_hex(6)
    schema = "git_integration_" + suffix
    network, backend, gateway = (
        "devops-integration-" + kind + suffix for kind in ("net", "api", "gw")
    )
    worker = "devops-integration-worker-" + suffix
    registry = "devops-integration-registry-" + suffix
    registry_gc = "devops-integration-gc-" + suffix
    registry_data = "devops-integration-registry-data-" + suffix
    website = "devops-integration-site-" + suffix
    root = build_engine(connections)
    engine = create_engine(
        connections.database_url,
        hide_parameters=True,
        connect_args={"options": "-csearch_path=" + schema},
    )
    runtime = None
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    try:
        with root.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        run("docker", "network", "create", network)
        run("docker", "volume", "create", website)
        run("docker", "volume", "create", registry_data)
        with tempfile.TemporaryDirectory(prefix="devops-git-integration-") as temporary:
            temp = Path(temporary)
            certs, repos = temp / "cert", temp / "repos"
            certs.mkdir()
            repos.mkdir()
            website_bundle = temp / "website.tar.gz"

            def publish_website(release):
                package(dist, website_bundle, release)
                # Match the Linux server filesystem. Docker Desktop's macOS shared
                # filesystem can return EINVAL after host-side symlink replacement.
                run(
                    "docker",
                    "run",
                    "--rm",
                    "--platform",
                    platform,
                    "--user",
                    "0:0",
                    "-v",
                    f"{website}:/website",
                    "-v",
                    f"{website_bundle}:/tmp/website.tar.gz:ro",
                    "-v",
                    f"{Path(__file__).with_name('deploy_website.py')}:/tmp/deploy_website.py:ro",
                    SERVER,
                    "python",
                    "/tmp/deploy_website.py",
                    "install",
                    "--root",
                    "/website",
                    "--archive",
                    "/tmp/website.tar.gz",
                    "--checksum",
                    file_digest(website_bundle),
                    "--release",
                    release,
                )

            publish_website("integration-first")
            run(
                "docker",
                "run",
                "--rm",
                "--platform",
                platform,
                "--user",
                "0:0",
                "-v",
                f"{website}:/website",
                "-v",
                f"{cli_bundle}:/tmp/cli.tgz:ro",
                "-v",
                f"{Path(__file__).with_name('deploy_cli.py')}:/tmp/deploy_cli.py:ro",
                SERVER,
                "python",
                "/tmp/deploy_cli.py",
                "--remote",
                "/website",
                "/tmp/cli.tgz",
                file_digest(cli_bundle),
                cli_version,
            )
            run(
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=127.0.0.1",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
                "-keyout",
                str(certs / "sc.key"),
                "-out",
                str(certs / "sc.pem"),
            )
            environment = temp / "connections.env"
            values = {
                "POSTGRES_HOST": "host.docker.internal",
                "POSTGRES_PORT": connections.postgres_port,
                "POSTGRES_DB": connections.postgres_db,
                "POSTGRES_USER": connections.postgres_user,
                "POSTGRES_PASSWORD": connections.postgres_password.get_secret_value(),
                "REDIS_HOST": "host.docker.internal",
                "REDIS_PORT": connections.redis_port,
                "REDIS_USERNAME": connections.redis_username,
                "REDIS_PASSWORD": connections.redis_password.get_secret_value(),
                "REDIS_DB": 0,
                "PGOPTIONS": "-csearch_path=" + schema,
            }
            if any(
                "\n" in str(value) or "\r" in str(value) for value in values.values()
            ):
                raise RuntimeError(
                    "Docker env-file cannot represent newline-containing values"
                )
            environment.write_text(
                "".join(f"{key}={value}\n" for key, value in values.items())
            )
            environment.chmod(0o600)
            docker_server = [
                "docker",
                "run",
                "--rm",
                "--platform",
                platform,
                "--network",
                network,
                "--env-file",
                str(environment),
                SERVER,
            ]
            run(*docker_server, "alembic", "upgrade", "head")
            run(*docker_server, "python", "-m", "devops_server.config_cli", "init")
            update_configuration(
                engine,
                {
                    "ENVIRONMENT": "production",
                    "SESSION_COOKIE_SECURE": True,
                    "ALLOWED_ORIGINS": [origin],
                    "GIT_OAUTH_ENABLED": True,
                    "REGISTRY_ENABLED": True,
                    "GIT_REPOSITORIES_ROOT": "/mnt/git-repos",
                },
            )
            runtime = load_settings(engine)
            run(
                "docker",
                "run",
                "-d",
                "--name",
                registry,
                "--network",
                network,
                "--network-alias",
                "registry",
                "--entrypoint",
                "/bin/sh",
                "-v",
                f"{devops / 'registry/config.yml'}:/etc/distribution/config.yml:ro",
                "-v",
                f"{registry_data}:/var/lib/registry",
                "-v",
                f"{devops / 'registry/start.sh'}:/etc/distribution/start.sh:ro",
                REGISTRY_IMAGE,
                "/etc/distribution/start.sh",
            )
            run(
                "docker",
                "run",
                "-d",
                "--name",
                registry_gc,
                "--network",
                network,
                "--user",
                "0:0",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/tmp",
                "--env-file",
                str(environment),
                "-v",
                f"{registry_data}:/var/lib/registry",
                "-v",
                f"{devops / 'registry/config.yml'}:/etc/distribution/config.yml:ro",
                SERVER,
                "python",
                "-m",
                "devops_server.registry_gc_worker",
            )
            web_session = secrets.token_urlsafe(48)
            with Session(engine) as db:
                user = User(
                    username="integration_owner",
                    display_name="Integration owner",
                    email="integration@example.com",
                )
                db.add(user)
                db.flush()
                next_owner = User(
                    username="integration_next",
                    display_name="Next owner",
                    email="integration-next@example.com",
                )
                db.add(next_owner)
                db.flush()
                next_owner_id = next_owner.id
                owner_id = user.id
                db.add(
                    UserRole(
                        user_id=user.id,
                        role_id=db.scalar(select(Role.id).where(Role.code == "member")),
                    )
                )
                db.add(
                    UserSession(
                        user_id=user.id,
                        token=web_session,
                        password_version=0,
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
                db.commit()
            run(
                "docker",
                "run",
                "--rm",
                "--platform",
                platform,
                "--entrypoint",
                "sh",
                "-v",
                f"{repos}:/mnt/git-repos",
                GATEWAY,
                "-c",
                "chown 101:101 /mnt/git-repos && chmod 750 /mnt/git-repos",
            )
            run(
                "docker",
                "run",
                "-d",
                "--name",
                backend,
                "--platform",
                platform,
                "--network",
                network,
                "--network-alias",
                "server",
                "--env-file",
                str(environment),
                "-v",
                f"{repos}:/mnt/git-repos",
                SERVER,
            )
            run(
                "docker",
                "run",
                "-d",
                "--name",
                gateway,
                "--platform",
                platform,
                "--network",
                network,
                "-p",
                f"127.0.0.1:{port}:443/tcp",
                "-p",
                f"127.0.0.1:{port}:443/udp",
                "-v",
                f"{certs}:/cert:ro",
                "-v",
                f"{repos}:/mnt/git-repos",
                "-v",
                f"{website}:/usr/share/nginx/html:ro",
                GATEWAY,
            )
            run(
                "docker",
                "run",
                "-d",
                "--name",
                worker,
                "--platform",
                platform,
                "--network",
                network,
                "--env-file",
                str(environment),
                "-v",
                f"{repos}:/mnt/git-repos",
                "--health-cmd",
                "python -m devops_server.collaboration_health",
                SERVER,
                "python",
                "-m",
                "devops_server.collaboration_worker",
            )
            context = ssl.create_default_context(cafile=str(certs / "sc.pem"))
            cookies = http.cookiejar.CookieJar()
            cookies.set_cookie(
                http.cookiejar.Cookie(
                    0,
                    runtime.session_cookie_name,
                    web_session,
                    None,
                    False,
                    "127.0.0.1",
                    False,
                    False,
                    "/",
                    True,
                    True,
                    None,
                    True,
                    None,
                    None,
                    {},
                    False,
                )
            )
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=context),
                urllib.request.HTTPCookieProcessor(cookies),
                NoRedirect(),
            )

            def request(path, data=None, headers=None, method=None):
                req = urllib.request.Request(
                    origin + path, data=data, headers=headers or {}, method=method
                )
                try:
                    response = opener.open(req, timeout=10)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return response.status, response.headers, response.read()

            for _ in range(60):
                try:
                    if request("/api/health/ready")[0] == 200:
                        break
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("Gateway/backend did not become ready")
            # Compose --wait depends on the image health commands, not only HTTP readiness.
            for container in (backend, gateway):
                health = json.loads(
                    run(
                        "docker",
                        "inspect",
                        "--format",
                        "{{json .Config.Healthcheck.Test}}",
                        container,
                    )
                )
                assert health[0] == "CMD-SHELL"
                run("docker", "exec", container, "sh", "-c", health[1])
            status, headers, body = request("/")
            assert 'h3=":443"' in headers["Alt-Svc"]
            for path, expected_status in (
                ("/", 200),
                ("/api/health/ready", 200),
                ("/_internal/git/authorize", 404),
                (
                    "/git/integration_owner/example.git/info/refs?service=git-upload-pack",
                    401,
                ),
            ):
                h3_status, h3_headers, h3_body = asyncio.run(
                    get_http3(origin + path, ca_file=certs / "sc.pem")
                )
                # These probes are unauthenticated and their responses contain no credentials.
                assert h3_status == expected_status, (path, h3_status, h3_body[:500])
                assert b'h3=":443"' in h3_headers[b"alt-svc"]
                if path == "/":
                    assert h3_body == body
                if path.endswith("ready"):
                    assert json.loads(h3_body)["status"] == "ready"
            print(
                "Native Docker + HTTP/3: TLS, static files, API and access denials passed"
            )
            assert status == 200 and body == (dist / "index.html").read_bytes()
            for asset in assets_in_html(body.decode()):
                assert request(asset)[2] == (dist / asset.lstrip("/")).read_bytes()
            assert request("/assets/nonexistent.js")[0] == 404
            status, _, marker = request("/release.json")
            assert status == 200 and marker.startswith(b"{"), (status, marker[:300])
            assert json.loads(marker) == {"release": "integration-first"}
            publish_website("integration-second")
            download = f"/res/devops-cli/{cli_version}.tgz"
            package_status, package_headers, package_body = request(download)
            assert package_status == 200 and package_body == cli_bundle.read_bytes()
            assert package_headers["Content-Type"] == "application/gzip"
            assert request(download, method="HEAD")[0] == 200
            assert request(download, data=b"overwrite", method="PUT")[0] == 403
            assert request("/res/devops-cli/nonexistent.tgz")[0] == 404
            assert request("/res/devops-cli/")[0] == 404
            assert request("/guide")[0] == 200
            assert request("/oauth/device")[0] == 200
            device_status, _, device_body = request(
                "/oauth/device/code",
                urllib.parse.urlencode(
                    {"client_id": "devops-cli", "scope": "api:read"}
                ).encode(),
                {"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert device_status == 200
            assert (
                json.loads(device_body)["verification_uri"] == origin + "/oauth/device"
            )
            print(
                "CLI: immutable download survives website switch; device endpoint passed"
            )
            status, _, marker = request("/release.json")
            assert status == 200 and marker.startswith(b"{"), (status, marker[:300])
            assert json.loads(marker) == {"release": "integration-second"}
            assert request("/oauth/consent")[0] == 200
            assert request("/_internal/git/authorize")[0] == 404
            csrf = json.loads(request("/api/auth/csrf")[2])["csrf_token"]
            write_headers = {
                "Origin": origin,
                "X-CSRF-Token": csrf,
                "Content-Type": "application/json",
            }
            verify_registry(request, write_headers, engine)

            def storage_blob_exists(digest):
                assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        registry_gc,
                        "python",
                        "-c",
                        (
                            "from pathlib import Path; import sys; h=sys.argv[1].split(':')[1]; "
                            "root=Path('/var/lib/registry/data/docker/registry/v2/blobs/sha256'); "
                            "print((root / h[:2] / h / 'data').is_file())"
                        ),
                        digest,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return result.stdout.strip() == "True"

            verify_registry_gc(request, write_headers, engine, storage_blob_exists)
            status, _, body = request(
                "/api/repositories", b'{"name":"example"}', write_headers
            )
            assert status == 201, body
            repository = json.loads(body)
            assert repository["clone_path"] == "/git/integration_owner/example.git"
            assert (
                request("/git/oauth-smoke.git/info/refs?service=git-upload-pack")[0]
                == 404
            )
            status, headers, _ = request(
                "/git/integration_owner/example.git/info/refs?service=git-upload-pack"
            )
            assert status == 401 and "Basic" in headers["WWW-Authenticate"]
            verifier = secrets.token_urlsafe(48)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            callback = "http://127.0.0.1:49152/"
            status, headers, _ = request(
                "/oauth/authorize?"
                + urllib.parse.urlencode(
                    {
                        "client_id": "devops-gcm",
                        "response_type": "code",
                        "redirect_uri": callback,
                        "scope": "git:read git:write",
                        "state": "integration-state",
                        "code_challenge_method": "S256",
                        "code_challenge": challenge,
                    }
                )
            )
            assert status == 302
            public_id = urllib.parse.parse_qs(
                urllib.parse.urlsplit(headers["Location"]).query
            )["request"][0]
            pending = f"/api/oauth/authorization-requests/{public_id}"
            assert request(pending)[0] == 200
            csrf = json.loads(request("/api/auth/csrf")[2])["csrf_token"]
            write_headers = {
                "Origin": origin,
                "X-CSRF-Token": csrf,
                "Content-Type": "application/json",
            }
            status, _, body = request(
                pending + "/decision", b'{"approve":true}', write_headers
            )
            assert status == 200
            result = urllib.parse.parse_qs(
                urllib.parse.urlsplit(json.loads(body)["redirect_uri"]).query
            )
            assert result["state"] == ["integration-state"]
            status, _, body = request(
                "/oauth/token",
                urllib.parse.urlencode(
                    {
                        "client_id": "devops-gcm",
                        "grant_type": "authorization_code",
                        "code": result["code"][0],
                        "code_verifier": verifier,
                        "redirect_uri": callback,
                    }
                ).encode(),
                {"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert status == 200
            tokens = json.loads(body)
            # Exercise a nearly expired grant through the real HTTPS refresh endpoint.
            # All writes here are restricted to this run's isolated schema.
            with Session(engine) as db:
                grant = db.scalar(select(GitOAuthGrant))
                grant_id = grant.id
                grant.expires_at = datetime.now(UTC) + timedelta(minutes=2)
                db.scalar(select(GitOAuthToken)).expires_at = datetime.now(
                    UTC
                ) - timedelta(seconds=1)
                db.commit()

            def backend_now():
                # Use the backend clock on both sides; Docker Desktop may drift from macOS.
                return datetime.fromisoformat(
                    run(
                        "docker",
                        "exec",
                        backend,
                        "python",
                        "-c",
                        "from datetime import datetime,UTC; print(datetime.now(UTC).isoformat())",
                    )
                )

            refreshed_at = backend_now()
            status, _, body = request(
                "/oauth/token",
                urllib.parse.urlencode(
                    {
                        "client_id": "devops-gcm",
                        "grant_type": "refresh_token",
                        "refresh_token": tokens["refresh_token"],
                        "redirect_uri": "http://127.0.0.1",
                    }
                ).encode(),
                {"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert status == 200
            renewed = json.loads(body)
            assert renewed["expires_in"] == 3600
            grants = json.loads(request("/api/me/git-authorizations")[2])
            assert len(grants) == 1 and grants[0]["id"] == grant_id
            assert (
                refreshed_at + timedelta(days=30)
                <= datetime.fromisoformat(grants[0]["expires_at"])
                <= backend_now() + timedelta(days=30)
            )
            old_credentials = base64.b64encode(
                f"oauth2:{tokens['access_token']}".encode()
            ).decode()
            assert (
                request(
                    "/git/integration_owner/example.git/info/refs?service=git-upload-pack",
                    headers={"Authorization": "Basic " + old_credentials},
                )[0]
                == 401
            )
            token = renewed["access_token"]
            token_file, askpass = temp / "token", temp / "askpass.sh"
            token_file.write_text(token)
            token_file.chmod(0o600)
            askpass.write_text(
                '#!/bin/sh\ncase "$1" in *Username*) echo oauth2;; '
                '*) cat "$DEVOPS_TEST_TOKEN_FILE";; esac\n'
            )
            askpass.chmod(0o700)
            git_env = os.environ | {
                "GIT_ASKPASS": str(askpass),
                "DEVOPS_TEST_TOKEN_FILE": str(token_file),
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_SSL_CAINFO": str(certs / "sc.pem"),
            }

            def git(*args, cwd=None):
                return run(
                    "git", "-c", "credential.helper=", *args, cwd=cwd, env=git_env
                )

            verify_ci(
                request,
                write_headers,
                engine,
                origin,
                certs / "sc.pem",
                temp,
                git,
                owner_id,
            )

            remote = origin + "/git/integration_owner/example.git"
            first, second = temp / "first", temp / "second"
            git("clone", remote, str(first))
            initial_readme = (first / "README.md").read_text()
            assert initial_readme.startswith("# integration_owner/example\n")
            assert f"git clone {remote}" in initial_readme
            assert git("branch", "--show-current", cwd=first).strip() == "main"
            assert git("rev-list", "--count", "HEAD", cwd=first).strip() == "1"
            tree = json.loads(
                request(f"/api/repositories/{repository['id']}/contents")[2]
            )
            assert tree["readme"]["content"] == initial_readme
            activity = json.loads(
                request(f"/api/repositories/{repository['id']}/activity")[2]
            )
            assert activity["total"] == 1 and len(activity["weeks"]) == 12
            (first / "README.md").write_text(
                "Synthetic repository integration fixture\n"
            )
            git("add", ".", cwd=first)
            git(
                "-c",
                "user.name=Integration",
                "-c",
                "user.email=integration@example.com",
                "commit",
                "-m",
                "Synthetic fixture",
                cwd=first,
            )
            git("push", "-u", "origin", "main", cwd=first)
            git("clone", remote, str(second))
            assert (second / "README.md").read_text() == (
                first / "README.md"
            ).read_text()
            # Incompressible payload exceeds postBuffer, exercising a chunked Git push.
            payload = os.urandom(2 * 1024 * 1024)
            (first / "payload.bin").write_bytes(payload)
            git("add", ".", cwd=first)
            git(
                "-c",
                "user.name=Integration",
                "-c",
                "user.email=integration@example.com",
                "commit",
                "-m",
                "Chunked upload fixture",
                cwd=first,
            )
            git("-c", "http.postBuffer=1024", "push", cwd=first)
            git("pull", "--ff-only", cwd=second)
            assert (second / "payload.bin").read_bytes() == payload
            assert request("/git/integration_owner/example.git/HEAD")[0] == 404
            repository_path = f"/api/repositories/{repository['id']}"
            assert request("/repositories/integration_owner/example")[0] == 200
            assert (
                json.loads(
                    request("/api/repositories/by-name/integration_owner/example")[2]
                )["id"]
                == repository["id"]
            )

            def lifecycle_code():
                return synthetic_email_code(engine, owner_id, "repository_operation")

            def mutate_repository(suffix, body, method):
                if suffix in {"/archive", "/transfer"}:
                    body = {**body, "code": lifecycle_code()}
                return request(
                    repository_path + suffix,
                    json.dumps(body).encode(),
                    write_headers,
                    method,
                )

            def refused_git(*args, cwd=None):
                result = subprocess.run(
                    ["git", "-c", "credential.helper=", *args],
                    cwd=cwd,
                    env=git_env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                assert result.returncode != 0
                return result.stderr

            # Branch/file APIs read actual objects pushed through this gateway.
            branches = json.loads(request(repository_path + "/branches")[2])
            assert [branch["name"] for branch in branches["items"]] == ["main"]
            code = json.loads(
                request(repository_path + "/contents?ref=main&path=README.md")[2]
            )
            assert code["content"] == (first / "README.md").read_text()

            def protection(main=False, push=False):
                assert (
                    mutate_repository(
                        "/protection",
                        {"protect_main": main, "protect_push": push},
                        "PUT",
                    )[0]
                    == 200
                )

            original_head = git("rev-parse", "HEAD", cwd=first)
            older_head = git("rev-parse", "HEAD~1", cwd=first)
            git("tag", "v1", older_head, cwd=first)
            git("push", "origin", "v1", cwd=first)
            protection(push=True)
            assert "push protection" in refused_git(
                "push", "--force", "origin", "HEAD~1:main", cwd=first
            )
            assert "push protection" in refused_git(
                "push", "origin", ":main", cwd=first
            )
            git("tag", "-f", "v1", original_head, cwd=first)
            assert "push protection" in refused_git(
                "push", "--force", "origin", "v1", cwd=first
            )
            assert "push protection" in refused_git("push", "origin", ":v1", cwd=first)
            # Client-supplied policy headers cannot disable the trusted CGI policy.
            assert "push protection" in refused_git(
                "-c",
                "http.extraHeader=X-Git-Protect-Push: 0",
                "push",
                "--force",
                "origin",
                "HEAD~1:main",
                cwd=first,
            )
            protection(main=True, push=True)
            git(
                "-c",
                "user.name=Integration",
                "-c",
                "user.email=integration@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "Protected push fixture",
                cwd=first,
            )
            assert "main is protected" in refused_git("push", cwd=first)
            # A mixed push must reject all refs, even without --atomic.
            assert "main is protected" in refused_git(
                "push", "origin", "main", "HEAD:refs/heads/rejected-topic", cwd=first
            )
            assert "rejected-topic" not in git("ls-remote", remote)
            git("push", "origin", "HEAD:refs/heads/feature/code", cwd=first)
            protection(push=True)
            git("push", cwd=first)  # Fast-forward is permitted.
            protection()
            git("push", "--force", "origin", "HEAD~1:main", cwd=first)
            git("push", cwd=first)

            # End-to-end collaboration uses only this disposable repository/account.
            assert "reserved" in refused_git(
                "push", "origin", "HEAD:refs/devops/merges/999", cwd=first
            )
            status, _, data = mutate_repository(
                "/issues", {"title": "Integration Issue"}, "POST"
            )
            assert status == 201
            issue = json.loads(data)
            status, _, data = mutate_repository(
                "/issues", {"title": "Child", "parent_id": issue["id"]}, "POST"
            )
            assert status == 201
            child = json.loads(data)
            git("checkout", "-b", "collaboration-topic", cwd=first)
            (first / "collaboration.txt").write_text("Review this change\n")
            git("add", ".", cwd=first)
            git(
                "-c",
                "user.name=Integration",
                "-c",
                "user.email=integration@example.com",
                "commit",
                "-m",
                f"Fixes #{issue['number']}",
                cwd=first,
            )
            git("push", "origin", "collaboration-topic", cwd=first)
            status, _, data = mutate_repository(
                "/pulls",
                {
                    "title": "Integration PR",
                    "head_ref": "collaboration-topic",
                    "body": f"See #{child['number']}",
                },
                "POST",
            )
            assert status == 201
            pr = json.loads(data)
            assert pr["number"] == child["number"] + 1
            assert pr["url"].endswith(f"/pr/{pr['number']}")
            pr_path = repository_path + f"/pulls/{pr['number']}"
            for _ in range(120):
                detail = json.loads(request(pr_path)[2])
                backlinks = json.loads(
                    request(repository_path + f"/items/{issue['number']}")[2]
                )["backlinks"]
                if detail["merge_checks"]["merge"]["status"] == "clean" and backlinks:
                    break
                time.sleep(0.5)
            else:
                raise AssertionError(
                    "Worker failed to process push receipt or merge checks"
                )
            assert backlinks[0]["source"]["kind"] == "commit"
            diff = json.loads(request(pr_path + "/diff?path=collaboration.txt")[2])
            assert "+Review this change" in diff["patch"]
            protection(main=True, push=True)
            status, _, _ = mutate_repository(
                f"/pulls/{pr['number']}/merge",
                {
                    "base_sha": detail["base_sha"],
                    "head_sha": detail["head_sha"],
                    "method": "merge",
                },
                "POST",
            )
            assert status == 202
            for _ in range(120):
                merged = json.loads(
                    request(repository_path + f"/items/{pr['number']}")[2]
                )
                closed = json.loads(
                    request(repository_path + f"/items/{issue['number']}")[2]
                )
                if merged["state"] == "merged" and closed["state"] == "closed":
                    break
                time.sleep(0.5)
            else:
                raise AssertionError(
                    "Worker failed to merge and close the linked Issue"
                )
            assert (
                json.loads(request(repository_path + f"/items/{child['number']}")[2])[
                    "state"
                ]
                == "open"
            )
            git("checkout", "main", cwd=first)
            git("pull", "--ff-only", cwd=first)
            assert (first / "collaboration.txt").read_text() == "Review this change\n"
            protected = json.loads(request(repository_path)[2])
            assert protected["protect_main"] and protected["protect_push"]
            git(
                "-c",
                "user.name=Integration",
                "-c",
                "user.email=integration@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "Direct push after PR merge",
                cwd=first,
            )
            assert "main is protected" in refused_git(
                "push", "origin", "main", cwd=first
            )
            git("reset", "--hard", "origin/main", cwd=first)
            print(
                "Protected main: direct push rejected; PR merged; protection remains enforced"
            )
            protection()
            run(
                "docker",
                "exec",
                worker,
                "python",
                "-m",
                "devops_server.collaboration_health",
            )

            # Real LFS pre-push, batch, verify, clone and pull. No global Git changes.
            git("lfs", "install", "--local", cwd=first)
            git("lfs", "track", "*.lfsbin", cwd=first)
            large_payload = os.urandom(3 * 1024 * 1024)
            (first / "large.lfsbin").write_bytes(large_payload)
            git("add", ".", cwd=first)
            git(
                "-c",
                "user.name=Integration",
                "-c",
                "user.email=integration@example.com",
                "commit",
                "-m",
                "LFS fixture",
                cwd=first,
            )
            git("push", cwd=first)
            oid = hashlib.sha256(large_payload).hexdigest()
            lfs_clone = temp / "lfs-clone"
            git("clone", remote, str(lfs_clone))
            git("lfs", "install", "--local", cwd=lfs_clone)
            git("lfs", "pull", cwd=lfs_clone)
            assert (lfs_clone / "large.lfsbin").read_bytes() == large_payload
            pointer = json.loads(
                request(repository_path + "/contents?path=large.lfsbin")[2]
            )
            assert pointer["lfs"] == {"oid": oid, "size": len(large_payload)}
            assert request(repository_path + "/lfs/" + oid)[2] == large_payload
            lfs_url = "/git/integration_owner/example.git/info/lfs"
            basic = "Basic " + base64.b64encode(f"oauth2:{token}".encode()).decode()
            lfs_headers = {
                "Authorization": basic,
                "Content-Type": "application/vnd.git-lfs+json",
            }
            batch_body = json.dumps(
                {
                    "operation": "download",
                    "objects": [{"oid": oid, "size": len(large_payload)}],
                }
            ).encode()
            assert (
                request(lfs_url + "/objects/batch", batch_body, lfs_headers)[0] == 200
            )
            # Browser cookies are not a Git LFS credential.
            assert (
                request(
                    lfs_url + "/objects/batch",
                    batch_body,
                    {"Content-Type": "application/vnd.git-lfs+json"},
                )[0]
                == 401
            )
            assert (
                request("/git/integration_owner/example.git/lfs/objects/" + oid)[0]
                == 404
            )

            assert mutate_repository("/archive", {"archived": True}, "PUT")[0] == 200
            assert (
                request(lfs_url + "/objects/" + oid, headers=lfs_headers)[2]
                == large_payload
            )
            assert (
                request(
                    lfs_url + "/objects/batch",
                    batch_body.replace(b"download", b"upload"),
                    lfs_headers,
                )[0]
                == 403
            )
            git("ls-remote", remote)
            git(
                "-c",
                "user.name=Integration",
                "-c",
                "user.email=integration@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "Archive gate fixture",
                cwd=first,
            )
            refused_git("push", cwd=first)
            assert mutate_repository("/archive", {"archived": False}, "PUT")[0] == 200
            git("push", cwd=first)
            # Delete a disposable sibling without erasing its storage; its URL must deny Git.
            status, _, body = request(
                "/api/repositories", b'{"name":"disposable"}', write_headers
            )
            assert status == 201
            disposable = json.loads(body)
            assert (
                request(
                    f"/api/repositories/{disposable['id']}",
                    json.dumps(
                        {
                            "confirm_name": "integration_owner/disposable",
                            "code": lifecycle_code(),
                        }
                    ).encode(),
                    write_headers,
                    "DELETE",
                )[0]
                == 200
            )
            refused_git("ls-remote", origin + disposable["clone_path"])
            assert request(f"/api/repositories/{disposable['id']}")[0] == 404

            status, _, body = mutate_repository(
                "/transfer",
                {
                    "new_owner_id": next_owner_id,
                    "confirm_name": "integration_owner/example",
                },
                "POST",
            )
            assert status == 200
            transferred = json.loads(body)
            assert transferred["id"] == repository["id"]
            refused_git("ls-remote", remote)
            remote = origin + transferred["clone_path"]
            assert request(lfs_url + "/objects/" + oid, headers=lfs_headers)[0] == 404
            lfs_url = lfs_url.replace("/integration_owner/", "/integration_next/")
            assert (
                request(lfs_url + "/objects/" + oid, headers=lfs_headers)[2]
                == large_payload
            )
            git("remote", "set-url", "origin", remote, cwd=first)
            git("push", cwd=first)
            git("clone", remote, str(temp / "transferred"))
            assert (temp / "transferred" / "payload.bin").read_bytes() == payload
            grant_id = json.loads(request("/api/me/git-authorizations")[2])[0]["id"]
            assert (
                request(
                    f"/api/me/git-authorizations/{grant_id}",
                    headers=write_headers,
                    method="DELETE",
                )[0]
                == 200
            )
            assert all(
                grant["id"] != grant_id
                for grant in json.loads(request("/api/me/git-authorizations")[2])
            )
            refused = subprocess.run(
                ["git", "-c", "credential.helper=", "ls-remote", remote],
                env=git_env,
                text=True,
                capture_output=True,
                check=False,
            )
            assert refused.returncode != 0
            assert request(lfs_url + "/objects/" + oid, headers=lfs_headers)[0] == 401
            print(
                "PASS: API repository creation by ordinary member, TLS, mounted SPA/API, atomic website switch, "
                "PKCE consent, Git clone/push/pull, Code browsing and activity, main/push protection, "
                "mixed-ref atomic rejection, forged-policy header rejection, "
                "Issue/sub-issue routes, push receipt/commit backlinks, native PR checks/merge/auto-close and worker health, "
                "3 MiB LFS upload/verify/clone/pull and LFS live ACL, "
                "2 MiB chunked push, archive write gate, unarchive, deletion, ownership transfer, "
                "internal-path protection and hidden revoked authorizations",
                flush=True,
            )
    finally:
        for name in (gateway, backend, worker, registry_gc, registry):
            subprocess.run(
                ["docker", "rm", "-f", "-v", name], capture_output=True, check=False
            )
        subprocess.run(
            ["docker", "network", "rm", network], capture_output=True, check=False
        )
        subprocess.run(
            ["docker", "volume", "rm", website, registry_data],
            capture_output=True,
            check=False,
        )
        if runtime is not None:
            with Session(engine) as db:
                ips = {
                    event.details.get("ip") for event in db.scalars(select(AuditEvent))
                }
            with build_redis(connections) as redis:
                for ip in ips:
                    redis.delete(
                        "devops:rate-limit:oauth-start:"
                        + digest(runtime, ip or "unknown")
                    )
        engine.dispose()
        with root.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        root.dispose()


if __name__ == "__main__":
    main()
