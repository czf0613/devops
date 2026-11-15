"""Build images locally, transfer them with docker save/load, and deploy kdevops.

The server cannot reach a registry, so every image (including the official
Distribution image) is transferred over SSH as an archive and verified by image
ID on both ends. No buildx push, no docker pull on the server.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

DEVOPS = Path(__file__).resolve().parents[1]
HOST = os.environ.get("DEVOPS_DEPLOY_HOST", "kdevops")
SITE = os.environ.get("DEVOPS_SITE_URL", "https://devops.kevinc.ltd")
PLATFORM = "linux/amd64"
REMOTE_DEPLOY = "/dockerdata/devops/deploy"
DATA_ROOT = "/dockerdata/devops"
GATEWAY_ROOT = "/dockerdata/gateway"
REPOSITORIES = f"{DATA_ROOT}/git-repos"
REGISTRY_DATA = f"{DATA_ROOT}/registry"
WEBSITE_ROOT = f"{GATEWAY_ROOT}/website"
CERT_ROOT = f"{GATEWAY_ROOT}/cer"
PROJECT = "devops"

SERVER_IMAGE = "devops.kevinc.ltd/core/devops"
GATEWAY_IMAGE = "devops.kevinc.ltd/core/devops-gateway"
# The Distribution image is pinned by digest, verified locally, then transferred
# under this tag because a loaded archive cannot keep a registry digest.
DISTRIBUTION_TAG = "registry:3.1.2"
DISTRIBUTION_DIGEST = (
    "sha256:ddf754342cfc8acc51a56d5d0ab6af06826461864460636d8bd5c546dab2a7b8"
)
IMAGES = {"server": SERVER_IMAGE, "gateway": GATEWAY_IMAGE}
COMPONENTS = ("server", "gateway", "registry", "cli", "runner", "website")

ADMIN_SEED = r"""
import os
import psycopg
from devops_server.config import get_connections

url = get_connections().database_url.set(drivername="postgresql")
email = os.environ["DEVOPS_ADMIN_EMAIL"].strip().lower()
username = os.environ.get("DEVOPS_ADMIN_USERNAME", "admin").strip()
with psycopg.connect(url.render_as_string(hide_password=False)) as connection:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (username, display_name, email) VALUES (%s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            (username, "管理员", email),
        )
        cursor.execute("SELECT id FROM users WHERE lower(email) = %s", (email,))
        row = cursor.fetchone()
        if row is None:
            raise SystemExit("admin user was not created; check the username and email")
        cursor.execute(
            "INSERT INTO user_roles (user_id, role_id)"
            " SELECT %s, id FROM roles WHERE code = 'admin'"
            " ON CONFLICT DO NOTHING",
            (row[0],),
        )
        cursor.execute("SELECT count(*) FROM user_roles WHERE user_id = %s", (row[0],))
        if not cursor.fetchone()[0]:
            raise SystemExit("admin role was not granted")
print("Admin account ready.")
"""


class DeploymentError(Exception):
    pass


def run(*args, capture=False, input=None, cwd=None):
    printable = [str(arg) for arg in args]
    if len(printable) > 1 and printable[1:3] == ["save", "-"]:
        printable = printable[:2] + ["<images>"]
    print("+ " + shlex.join(printable), flush=True)
    return subprocess.run(
        printable,
        check=True,
        text=True,
        capture_output=capture,
        input=input,
        cwd=cwd,
    ).stdout


def ssh(command, *, capture=False, input=None):
    return run(
        "ssh",
        "-o",
        "BatchMode=yes",
        HOST,
        command,
        capture=capture,
        input=input,
    )


def read_env(path):
    """Parse KEY=VALUE lines, accepting single/double quotes and ignoring comments."""
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            raw = raw[1:-1]
        values[key] = raw
    return values


def release_name():
    revision = run(
        "git", "-C", str(DEVOPS), "rev-parse", "--short", "HEAD", capture=True
    ).strip()
    dirty = run("git", "-C", str(DEVOPS), "status", "--porcelain", capture=True).strip()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{revision}{'-dirty' if dirty else ''}-{stamp}"


def image_id(image):
    return run(
        "docker", "image", "inspect", "--format", "{{.Id}}", image, capture=True
    ).strip()


def build_images(release, names):
    """Build the selected amd64 project images locally; the server is linux/amd64."""
    local = {}
    for name in names:
        repository = IMAGES[name]
        image = f"{repository}:{release}"
        context = DEVOPS / "server" if name == "server" else DEVOPS
        run(
            "docker",
            "buildx",
            "build",
            "--platform",
            PLATFORM,
            "--load",
            "-t",
            image,
            "-f",
            str(DEVOPS / name / "Dockerfile"),
            str(context),
        )
        local[name] = {"tag": image, "id": image_id(image)}
    return local


def fetch_distribution():
    """Use the digest-pinned Distribution image, reusing the local build base offline."""
    expected = DISTRIBUTION_DIGEST
    reference = f"{DISTRIBUTION_TAG}@{expected}"
    try:
        if image_id(reference) == expected:
            run("docker", "tag", reference, DISTRIBUTION_TAG)
    except subprocess.CalledProcessError:
        try:
            if image_id(DISTRIBUTION_TAG) == expected:
                return {"tag": DISTRIBUTION_TAG, "id": expected}
        except subprocess.CalledProcessError:
            pass
        run("docker", "pull", "--platform", PLATFORM, reference)
        run("docker", "tag", reference, DISTRIBUTION_TAG)
    actual = image_id(DISTRIBUTION_TAG)
    if actual != expected:
        raise DeploymentError(
            "Distribution image digest does not match the pinned value"
        )
    return {"tag": DISTRIBUTION_TAG, "id": actual}


def transfer(images):
    """Stream one save archive over SSH and verify identical image IDs remotely."""
    tags = [entry["tag"] for entry in images.values()]
    print(f"Transferring {len(tags)} images to {HOST}", flush=True)
    save = subprocess.Popen(["docker", "save", *tags], stdout=subprocess.PIPE)
    gzip = subprocess.Popen(["gzip", "-1"], stdin=save.stdout, stdout=subprocess.PIPE)
    loader = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", HOST, "docker load"],
        stdin=gzip.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if save.stdout is not None:
        save.stdout.close()
    if gzip.stdout is not None:
        gzip.stdout.close()
    output = loader.communicate()[0]
    if save.wait() != 0:
        raise DeploymentError("docker save failed")
    if gzip.wait() != 0:
        raise DeploymentError("gzip failed")
    if loader.returncode != 0:
        raise DeploymentError(f"docker load failed: {output.strip()}")
    for name, entry in images.items():
        actual = ssh(
            "docker image inspect --format "
            + shlex.quote("{{.Id}}")
            + " "
            + shlex.quote(entry["tag"]),
            capture=True,
        ).strip()
        if actual != entry["id"]:
            raise DeploymentError(f"{name} image differs after transfer")


def provision(settings, images):
    """Create directories, write the deployment files, and keep data owned by UID 101."""
    ssh(
        "install -d -m 755 "
        + " ".join(
            shlex.quote(path)
            for path in (
                REMOTE_DEPLOY,
                REPOSITORIES,
                REGISTRY_DATA,
                WEBSITE_ROOT,
                CERT_ROOT,
            )
        )
    )
    ssh(f"chown 101:101 {shlex.quote(REPOSITORIES)}")
    if not settings.get("ADMIN_EMAIL"):
        raise DeploymentError("ADMIN_EMAIL is required in deploy.env")
    env_lines = [f"{key}={value}" for key, value in settings.items()]
    env_lines += [
        f"DEVOPS_SERVER_IMAGE={images['server']['tag']}",
        f"DEVOPS_GATEWAY_IMAGE={images['gateway']['tag']}",
    ]
    ssh(
        f"umask 077 && cat > {shlex.quote(REMOTE_DEPLOY)}/.env.deploy",
        input="\n".join(env_lines) + "\n",
    )
    ssh(
        f"cat > {shlex.quote(REMOTE_DEPLOY)}/compose.deploy.yaml",
        input=(DEVOPS / "compose.deploy.yaml").read_text(),
    )
    ssh(f"install -d -m 700 {shlex.quote(REMOTE_DEPLOY + '/registry')}")
    for name in ("config.yml", "start.sh"):
        ssh(
            f"cat > {shlex.quote(REMOTE_DEPLOY + '/registry/' + name)}",
            input=(DEVOPS / "registry" / name).read_text(),
        )
    ssh(f"chmod 700 {shlex.quote(REMOTE_DEPLOY + '/registry')}")


def compose(*args, capture=False, input=None):
    return ssh(
        shlex.join(
            [
                "docker",
                "compose",
                "--project-name",
                PROJECT,
                "--env-file",
                f"{REMOTE_DEPLOY}/.env.deploy",
                "-f",
                f"{REMOTE_DEPLOY}/compose.deploy.yaml",
                *args,
            ]
        ),
        capture=capture,
        input=input,
    )


def runtime_settings(settings, registry=None):
    """Runtime configuration written to PostgreSQL, not to the environment.

    ``registry`` is tri-state: True/False set REGISTRY_ENABLED, while None keeps
    the stored value so a server-only or website-only deploy cannot disable it.
    """
    origins = [SITE]
    smtp_security = settings.get("SMTP_SSL", "").strip().lower()
    if smtp_security not in {"none", "starttls", "ssl"}:
        raise DeploymentError("SMTP_SSL must be one of none, starttls, ssl")
    if not settings.get("SMTP_HOST") or not settings.get("SMTP_PORT"):
        raise DeploymentError("SMTP_HOST and SMTP_PORT are required")
    values = {
        "ENVIRONMENT": "production",
        "ALLOWED_ORIGINS": origins,
        "SESSION_COOKIE_SECURE": True,
        "SESSION_COOKIE_NAME": "devops_session",
        "CSRF_COOKIE_NAME": "devops_csrf",
        "REGISTRATION_ENABLED": False,
        "GIT_OAUTH_ENABLED": True,
        "GIT_REPOSITORIES_ROOT": "/mnt/git-repos",
        "SMTP_HOST": settings["SMTP_HOST"],
        "SMTP_PORT": int(settings["SMTP_PORT"]),
        "SMTP_SECURITY": smtp_security,
        "SMTP_USERNAME": settings.get("SMTP_USER", ""),
        "SMTP_PASSWORD": settings.get("SMTP_PASSWORD", ""),
        "SMTP_FROM": settings.get("SMTP_FROM") or settings.get("SMTP_USER", ""),
    }
    if registry is not None:
        values["REGISTRY_ENABLED"] = bool(registry)
    return values


def migrate():
    compose("run", "--rm", "--no-deps", "-T", "server", "alembic", "upgrade", "head")


def configure(settings, registry):
    compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "server",
        "python",
        "-m",
        "devops_server.config_cli",
        "init",
    )
    values = runtime_settings(settings, registry)
    compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "server",
        "python",
        "-m",
        "devops_server.config_cli",
        "set",
        input=json.dumps(values),
    )


def seed_admin(settings):
    compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "-e",
        f"DEVOPS_ADMIN_EMAIL={settings['ADMIN_EMAIL']}",
        "-e",
        f"DEVOPS_ADMIN_USERNAME={settings.get('ADMIN_USERNAME') or 'admin'}",
        "server",
        "python",
        "-",
        input=ADMIN_SEED,
    )


def start(components):
    services = []
    if "server" in components:
        services += ["server", "worker"]
        # The GC worker runs the same image as the server; keep it in lockstep.
        services += ["registry-gc"]
    if "registry" in components:
        services += ["registry", "registry-gc"]
    if "gateway" in components:
        services += ["gateway"]
    if services:
        compose("up", "-d", "--wait", "--wait-timeout", "180", *services)


def fetch(path, *, ipv6=False):
    """Read a URL from the server itself; the public port may still be closed."""
    family = "-6 " if ipv6 else ""
    address = "[::1]" if ipv6 else "127.0.0.1"
    return ssh(
        f"curl -g {family}-fsS --max-time 20 --resolve "
        f"devops.kevinc.ltd:443:{address} {SITE}{path}",
        capture=True,
    )


def verify():
    health = json.loads(fetch("/api/health/ready"))
    if health.get("status") != "ready":
        raise DeploymentError("Public readiness endpoint is not ready")
    marker = json.loads(fetch("/release.json"))
    if not marker.get("release"):
        raise DeploymentError("Gateway is not serving a website release")
    # Host networking must also answer on IPv6; the host has a global /128 and DNS AAAA.
    if json.loads(fetch("/api/health/ready", ipv6=True)).get("status") != "ready":
        raise DeploymentError("IPv6 readiness endpoint is not ready")
    if json.loads(fetch("/release.json", ipv6=True)) != marker:
        raise DeploymentError("IPv6 gateway serves a different website release")
    print(
        f"Deployed {SITE}: database and Redis connected, website {marker['release']}, IPv4 + IPv6 ready.",
        flush=True,
    )


def deploy(components, *, dry_run=False):
    selected = [
        name for name in COMPONENTS if name in components or "all" in components
    ]
    if not selected:
        raise DeploymentError("Select at least one component")
    if "registry" in selected and "server" not in selected:
        raise DeploymentError("Registry activation requires the server component")
    settings = read_env(DEVOPS / "deploy.env")
    missing = [
        key
        for key in (
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "REDIS_HOST",
            "REDIS_PORT",
            "REDIS_PASSWORD",
        )
        if not settings.get(key)
    ]
    if missing:
        raise DeploymentError("deploy.env is missing: " + ", ".join(missing))
    images_wanted = [
        name for name in ("server", "gateway", "registry") if name in selected
    ]
    print(
        f"Deployment: {HOST}:{REMOTE_DEPLOY}; components: {', '.join(selected)}",
        flush=True,
    )
    if dry_run:
        for name in selected:
            if name in IMAGES:
                print(f"  {name}: local buildx {PLATFORM} {IMAGES[name]}:<release>")
            elif name == "registry":
                print(f"  registry: local {DISTRIBUTION_TAG}@{DISTRIBUTION_DIGEST}")
            elif name == "website":
                print(f"  website: local pnpm build → {WEBSITE_ROOT}/releases")
        if images_wanted:
            print(
                "  transfer: docker save | gzip | ssh docker load, then verify image IDs"
            )
        if "server" in selected:
            print(
                "  server: alembic upgrade head → config init/set → admin seed → up --wait"
            )
        print("  verify: server-side HTTPS readiness and website release marker")
        return

    images = {}
    if images_wanted:
        release = release_name()
        built = build_images(
            release, [name for name in ("server", "gateway") if name in selected]
        )
        images.update(built)
        if "registry" in selected:
            images["registry"] = fetch_distribution()
        transfer(images)

    previous = {}
    state = REMOTE_DEPLOY + "/images.json"
    try:
        previous = json.loads(ssh(f"cat {shlex.quote(state)}", capture=True))
    except subprocess.CalledProcessError:
        previous = {}
    merged = {
        name: entry
        for name, entry in {**previous, **images}.items()
        if name in {"server", "gateway"}
    }
    if set(merged) != {"server", "gateway"}:
        raise DeploymentError(
            "Both server and gateway images must be known before provisioning"
        )
    provision(settings, merged)
    ssh(f"cat > {shlex.quote(state)}", input=json.dumps(merged) + "\n")

    if "server" in selected:
        migrate()
        configure(settings, registry=True if "registry" in selected else None)
        seed_admin(settings)
    if "server" in selected or "registry" in selected:
        start([name for name in ("server", "registry") if name in selected])
    if "website" in selected:
        run(
            sys.executable,
            str(DEVOPS / "scripts" / "deploy_website.py"),
            "publish",
            "--host",
            HOST,
            "--root",
            WEBSITE_ROOT,
            "--prepare-only",
        )
    if "gateway" in selected:
        start(["gateway"])
    if "cli" in selected:
        run(sys.executable, str(DEVOPS / "scripts" / "deploy_cli.py"), "publish")
    if "runner" in selected:
        run(sys.executable, str(DEVOPS / "scripts" / "deploy_runner.py"), "publish")
    verify()
    print(f"Deployment complete: {', '.join(selected)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("components", nargs="+", choices=(*COMPONENTS, "all"))
    parser.add_argument("--dry-run", action="store_true", help="Show the plan only")
    args = parser.parse_args()
    try:
        deploy(args.components, dry_run=args.dry_run)
    except (
        DeploymentError,
        subprocess.CalledProcessError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"Deployment failed: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
