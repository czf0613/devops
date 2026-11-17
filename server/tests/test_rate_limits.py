from concurrent.futures import ThreadPoolExecutor

from conftest import mutate
from redis.exceptions import ConnectionError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devops_server.config import get_connections
from devops_server.models import EmailChallenge
from devops_server.rate_limits import EmailCodeRateLimiter


def test_two_instances_share_atomic_cooldown_under_concurrency(limiter, auth_settings):
    second = EmailCodeRateLimiter(limiter.client, limiter.namespace)
    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(
            pool.map(
                lambda index: (limiter if index % 2 else second).reserve(
                    "alice@example.com", "192.0.2.1", auth_settings
                ),
                range(24),
            )
        )
    assert sum(item.allowed for item in outcomes) == 1
    assert all(1 <= item.retry_after <= 60 for item in outcomes)
    email, ip, cooldown = limiter.keys("alice@example.com", "192.0.2.1")
    assert limiter.client.zcard(email) == limiter.client.zcard(ip) == 1
    assert 0 < limiter.client.pttl(cooldown) <= 60_000
    assert 0 < limiter.client.pttl(email) <= 3_600_000


def test_rolling_email_quota_expires_and_denials_do_not_extend_ttl(limiter, auth_settings):
    auth_settings.email_code_email_hourly_limit = 2
    email, ip, cooldown = limiter.keys("alice@example.com", "192.0.2.1")
    for _ in range(2):
        assert limiter.reserve("alice@example.com", "192.0.2.1", auth_settings).allowed
        limiter.client.delete(cooldown)
    limiter.client.pexpire(email, 120_000)
    denied = limiter.reserve("alice@example.com", "192.0.2.2", auth_settings)
    assert not denied.allowed and 3590 <= denied.retry_after <= 3600
    assert limiter.client.zcard(email) == 2
    assert limiter.client.pttl(email) <= 120_000
    assert not limiter.client.exists(limiter.keys("alice@example.com", "192.0.2.2")[1])
    seconds, micros = limiter.client.time()
    old_score = seconds * 1000 + micros // 1000 - 3_600_001
    for key in (email, ip):
        entries = limiter.client.zrange(key, 0, -1)
        limiter.client.zadd(key, {entry: old_score for entry in entries})
    assert limiter.reserve("alice@example.com", "192.0.2.1", auth_settings).allowed
    assert limiter.client.zcard(email) == 1


def test_ip_quota_is_shared_across_different_email_addresses(limiter, auth_settings):
    auth_settings.email_code_ip_hourly_limit = 2
    for email in ("one@example.com", "two@example.com"):
        assert limiter.reserve(email, "192.0.2.1", auth_settings).allowed
    denied = limiter.reserve("three@example.com", "192.0.2.1", auth_settings)
    assert not denied.allowed
    assert not limiter.client.exists(limiter.keys("three@example.com", "192.0.2.1")[0])
    assert limiter.reserve("three@example.com", "192.0.2.2", auth_settings).allowed


def test_redis_failure_fails_closed_without_sending_mail(system, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionError("private redis credentials")

    client = system.client()
    with monkeypatch.context() as patch:
        patch.setattr(system.limiter, "reserve", unavailable)
        response = mutate(
            client,
            "POST",
            "/api/auth/email-codes",
            {"email": "member@example.com", "purpose": "login"},
        )
    assert response.status_code == 503
    assert "private" not in response.text
    assert system.mailbox.messages == []
    with Session(system.engine) as db:
        assert db.scalar(select(func.count()).select_from(EmailChallenge)) == 0


def test_http_limit_returns_retry_after_and_uses_shared_ip_header(system):
    system.settings.email_code_ip_hourly_limit = 1
    client = system.client()
    for index, expected in enumerate((202, 429)):
        response = mutate(
            client,
            "POST",
            "/api/auth/email-codes",
            {"email": f"member{index}@example.com", "purpose": "login"},
            headers={"x-forwarded-for": "192.0.2.1, 10.0.0.1"},
        )
        assert response.status_code == expected
    assert 3590 <= int(response.headers["Retry-After"]) <= 3600
    assert len(system.mailbox.messages) == 1


def test_readiness_includes_redis_failure_without_connection_details(system, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionError("private redis credentials")

    client = system.client()
    with monkeypatch.context() as patch:
        patch.setattr(system.limiter.client, "ping", unavailable)
        response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "database": "connected",
        "redis": "unavailable",
    }


def test_local_redis_uses_the_configured_database(limiter):
    # REDIS_DB is configurable; the client must honour it instead of hardcoding 0,
    # and the local server must expose the default 16 databases.
    settings = get_connections()
    assert limiter.client.connection_pool.connection_kwargs["db"] == settings.redis_db
    with limiter.client.client() as connection:
        connection.execute_command("SELECT", 15)
        assert connection.execute_command("PING") is True
