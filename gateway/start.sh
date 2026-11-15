#!/bin/sh
set -eu
test -r /cert/sc.pem && test -r /cert/sc.key
test -s /usr/share/nginx/html/current/index.html || {
    echo 'Website release missing: publish the local website build before starting the gateway.' >&2
    exit 1
}
nginx -t

fcgi_pid=
nginx_pid=
stop() {
    trap - EXIT INT TERM
    [ -z "$nginx_pid" ] || kill -QUIT "$nginx_pid" 2>/dev/null || true
    [ -z "$fcgi_pid" ] || kill -TERM "$fcgi_pid" 2>/dev/null || true
    wait || true
}
trap 'stop; exit 0' INT TERM
trap stop EXIT
# Only loopback FastCGI; both the CGI workers and Git run as nginx (UID/GID 101).
spawn-fcgi -n -a 127.0.0.1 -p 9000 -u nginx -g nginx -- /usr/bin/fcgiwrap -f -c 4 &
fcgi_pid=$!
nginx -g 'daemon off;' &
nginx_pid=$!
# If either service dies, exit so Docker can restart the whole gateway.
wait -n "$fcgi_pid" "$nginx_pid" || true
echo 'Gateway child exited; stopping container.' >&2
exit 1
