#!/bin/sh
set -eu
umask 077
# Upload state signing key, not an account credential. Persist across restarts.
# This service is single-instance; do not share its filesystem between replicas.
key=/var/lib/registry/.http-secret
if [ ! -s "$key" ]; then
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$key.tmp"
    mv "$key.tmp" "$key"
fi
REGISTRY_HTTP_SECRET=$(cat "$key")
export REGISTRY_HTTP_SECRET
export OTEL_TRACES_EXPORTER=none
exec /bin/registry serve /etc/distribution/config.yml
