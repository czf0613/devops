#!/bin/sh
set -eu
# Keep the plaintext password out of Redis process arguments and the ACL file.
password_hash=$(printf '%s' "$REDIS_PASSWORD" | sha256sum | cut -d ' ' -f 1)
umask 077
printf 'user default on #%s ~* &* +@all\n' "$password_hash" > /tmp/devops-redis.acl
chown redis:redis /tmp/devops-redis.acl
exec /usr/local/bin/docker-entrypoint.sh redis-server \
  --aclfile /tmp/devops-redis.acl --databases 1 --appendonly yes --maxmemory-policy noeviction
