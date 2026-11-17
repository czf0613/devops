import hashlib
import math
import secrets
from dataclasses import dataclass

from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from devops_server.config import ConnectionSettings, Settings

# One script reserves both rolling quotas and the resend delay atomically. Redis TIME
# avoids clock skew between application instances. Rejections never extend a window.
RESERVE_EMAIL_CODE = """
local time = redis.call('TIME')
local now = time[1] * 1000 + math.floor(time[2] / 1000)
local window = 3600000
local retry = math.max(0, redis.call('PTTL', KEYS[3]))
for i = 1, 2 do
    redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', now - window)
    if redis.call('ZCARD', KEYS[i]) >= tonumber(ARGV[i]) then
        local oldest = redis.call('ZRANGE', KEYS[i], 0, 0, 'WITHSCORES')
        retry = math.max(retry, tonumber(oldest[2]) + window - now)
    end
end
if retry > 0 then
    return {0, retry}
end
for i = 1, 2 do
    redis.call('ZADD', KEYS[i], now, ARGV[4])
    redis.call('PEXPIRE', KEYS[i], window)
end
redis.call('PSETEX', KEYS[3], ARGV[3], '1')
return {1, tonumber(ARGV[3])}
"""


def build_redis(settings: ConnectionSettings) -> Redis:
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        username=settings.redis_username or None,
        password=settings.redis_password.get_secret_value() or None,
        db=settings.redis_db,
        ssl=settings.redis_ssl,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry=Retry(NoBackoff(), 0),
        decode_responses=True,
    )


@dataclass(frozen=True)
class Reservation:
    allowed: bool
    retry_after: int


class EmailCodeRateLimiter:
    def __init__(self, client: Redis, namespace: str = "devops:rate-limit"):
        self.client = client
        self.namespace = namespace
        self.script = client.register_script(RESERVE_EMAIL_CODE)

    def keys(self, email: str, ip: str | None) -> list[str]:
        email_key = hashlib.sha256(email.encode()).hexdigest()
        ip_key = hashlib.sha256((ip or "unknown").encode()).hexdigest()
        # Shared hash tag also permits this atomic script on a future Redis Cluster.
        prefix = self.namespace + ":{email-code}:"
        return [
            prefix + "email:" + email_key,
            prefix + "ip:" + ip_key,
            prefix + "cooldown:" + email_key,
        ]

    def reserve(self, email: str, ip: str | None, settings: Settings) -> Reservation:
        allowed, retry_ms = self.script(
            keys=self.keys(email, ip),
            args=[
                settings.email_code_email_hourly_limit,
                settings.email_code_ip_hourly_limit,
                settings.email_code_resend_seconds * 1000,
                secrets.token_urlsafe(24),
            ],
        )
        return Reservation(bool(allowed), max(1, math.ceil(retry_ms / 1000)))
