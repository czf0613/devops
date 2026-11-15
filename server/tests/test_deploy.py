import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("server_deploy", ROOT / "scripts" / "deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

COMPOSE = (ROOT / "compose.deploy.yaml").read_text()
SETTINGS = {
    "POSTGRES_HOST": "pg.internal",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "devops",
    "POSTGRES_USER": "devops",
    "POSTGRES_PASSWORD": "secret",
    "REDIS_HOST": "redis.internal",
    "REDIS_PORT": "6379",
    "REDIS_PASSWORD": "secret",
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "465",
    "SMTP_SSL": "ssl",
    "SMTP_USER": "service@kevinc.ltd",
    "SMTP_PASSWORD": "secret",
    "SMTP_FROM": "DevOps <service@kevinc.ltd>",
    "ADMIN_EMAIL": "admin@kevinc.ltd",
}


def test_deployment_images_live_under_the_new_domain_and_registry_is_local():
    assert deploy.SERVER_IMAGE == "devops.kevinc.ltd/core/devops"
    assert deploy.GATEWAY_IMAGE == "devops.kevinc.ltd/core/devops-gateway"
    assert deploy.DISTRIBUTION_TAG == "registry:3.1.2"
    assert deploy.DISTRIBUTION_DIGEST.startswith("sha256:")
    source = (ROOT / "scripts" / "deploy.py").read_text()
    assert "nas.kevinc" not in source
    assert '"--push"' not in source
    assert "docker save" in source and "docker load" in source


def test_compose_uses_transferred_images_and_managed_services():
    assert "nas.kevinc.ltd" not in COMPOSE
    assert "image: registry:3.1.2" in COMPOSE
    assert "${DEVOPS_SERVER_IMAGE:?" in COMPOSE
    assert "${DEVOPS_GATEWAY_IMAGE:?" in COMPOSE
    assert "postgres:" not in COMPOSE and "redis:" not in COMPOSE
    assert "POSTGRES_HOST: ${POSTGRES_HOST:?" in COMPOSE
    assert "/dockerdata/gateway/cer:/cert:ro" in COMPOSE
    assert "/dockerdata/gateway/website:/usr/share/nginx/html:ro" in COMPOSE
    assert "network_mode: host" in COMPOSE
    assert COMPOSE.count('"127.0.0.1:8000:8000"') == 1
    assert '"127.0.0.1:5000:5000"' in COMPOSE
    assert "443:443" not in COMPOSE


def test_gateway_config_matches_the_new_domain_and_certificates():
    nginx = (ROOT / "gateway" / "nginx.conf").read_text()
    assert "server_name devops.kevinc.ltd;" in nginx
    assert "ssl_certificate /cert/kevinc.pem;" in nginx
    assert "ssl_certificate_key /cert/kevinc.key;" in nginx
    assert "listen [::]:443 ssl;" in nginx
    assert "server 127.0.0.1:8000;" in nginx
    assert "server 127.0.0.1:5000;" in nginx
    assert "listen 80 default_server;" in nginx
    assert "listen [::]:80 default_server;" in nginx
    assert "return 301 https://devops.kevinc.ltd$request_uri;" in nginx
    immutable = 'add_header Cache-Control "public, max-age=31536000, immutable" always;'
    assert nginx.count(immutable) == 3
    assert "add_header_inherit merge;" in nginx


def test_runtime_settings_disable_registration_and_wire_smtp():
    values = deploy.runtime_settings(SETTINGS, registry=True)
    assert values["ALLOWED_ORIGINS"] == ["https://devops.kevinc.ltd"]
    assert values["REGISTRATION_ENABLED"] is False
    assert values["GIT_OAUTH_ENABLED"] is True
    assert values["REGISTRY_ENABLED"] is True
    assert "REGISTRY_ENABLED" not in deploy.runtime_settings(SETTINGS)
    assert values["ENVIRONMENT"] == "production"
    assert values["SESSION_COOKIE_NAME"] == "devops_session"
    assert values["SMTP_SECURITY"] == "ssl"
    assert values["SMTP_PORT"] == 465
    assert values["SMTP_USERNAME"] == "service@kevinc.ltd"
    assert values["SMTP_FROM"] == "DevOps <service@kevinc.ltd>"


@pytest.mark.parametrize("value", ["none", "starttls", "ssl"])
def test_smtp_ssl_accepts_the_documented_values(value):
    assert (
        deploy.runtime_settings({**SETTINGS, "SMTP_SSL": value}, registry=False)["SMTP_SECURITY"]
        == value
    )


def test_smtp_ssl_rejects_unknown_values():
    with pytest.raises(deploy.DeploymentError, match="SMTP_SSL"):
        deploy.runtime_settings({**SETTINGS, "SMTP_SSL": "tls"}, registry=False)


def test_read_env_accepts_quotes_and_comments(tmp_path):
    env = tmp_path / "deploy.env"
    env.write_text("# comment\nPOSTGRES_HOST='pg'\nSMTP_PASSWORD=\"p=a#s\"\n\nREDIS_PORT=6379\n")
    values = deploy.read_env(env)
    assert values == {"POSTGRES_HOST": "pg", "SMTP_PASSWORD": "p=a#s", "REDIS_PORT": "6379"}


def test_deploy_requires_database_settings_before_touching_any_host(monkeypatch, tmp_path):
    env = tmp_path / "deploy.env"
    env.write_text("POSTGRES_HOST=pg\n")
    monkeypatch.setattr(deploy, "DEVOPS", tmp_path)
    calls = []
    monkeypatch.setattr(deploy, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(deploy.DeploymentError, match="missing"):
        deploy.deploy(["all"])
    assert calls == []


def test_dry_run_has_no_side_effects(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "read_env", lambda path: dict(SETTINGS))
    calls = []
    monkeypatch.setattr(deploy, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(deploy, "ssh", lambda *args, **kwargs: calls.append(args))
    deploy.deploy(["all"], dry_run=True)
    assert calls == []
    output = capsys.readouterr().out
    assert "docker save" in output and "alembic upgrade head" in output


def test_transfer_verification_rejects_a_mismatched_remote_image(monkeypatch):
    entry = {"server": {"tag": "devops.kevinc.ltd/core/devops:test", "id": "sha256:" + "a" * 64}}

    class Process:
        def __init__(self, *args, **kwargs):
            self.returncode = 0
            self.stdout = None

        def communicate(self, *args, **kwargs):
            return ("Loaded image\n", "")

        def wait(self):
            return 0

    monkeypatch.setattr(
        deploy.subprocess, "Popen", lambda *args, **kwargs: Process(*args, **kwargs)
    )
    monkeypatch.setattr(deploy, "ssh", lambda *args, **kwargs: "sha256:" + "b" * 64)
    with pytest.raises(deploy.DeploymentError, match="differs after transfer"):
        deploy.transfer(entry)


def test_registry_activation_requires_server(monkeypatch):
    monkeypatch.setattr(deploy, "read_env", lambda path: dict(SETTINGS))
    with pytest.raises(deploy.DeploymentError, match="requires the server"):
        deploy.deploy(["registry"], dry_run=True)
