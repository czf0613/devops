# Brotli is not part of the upstream Nginx package set, so build the two dynamic
# modules once and copy them into the runtime image. The module is compiled
# against the exact source version of the base image with --with-compat, which is
# what makes it load into the unmodified official build.
# nginx 1.31.9 fixes the nginx2root CVEs (CVE-2026-123007 and related).
FROM nginx:1.31.9-alpine AS brotli
RUN apk add --no-cache alpine-sdk cmake git linux-headers pcre2-dev zlib-dev openssl-dev
WORKDIR /src
RUN git clone --depth 1 --recurse-submodules https://github.com/google/ngx_brotli.git
RUN NGINX_VERSION="$(nginx -v 2>&1 | sed -E 's#^nginx version: nginx/##')" \
    && echo "$NGINX_VERSION" > /src/nginx-version \
    && curl -fsSL "https://nginx.org/download/nginx-$NGINX_VERSION.tar.gz" -o nginx.tar.gz \
    && tar -xzf nginx.tar.gz
# The module links the bundled Brotli static libraries from deps/brotli/out.
RUN cd /src/ngx_brotli/deps/brotli \
    && cmake -S . -B out -DCMAKE_BUILD_TYPE=Release >/dev/null \
    && cmake --build out -j"$(nproc)" >/dev/null
RUN NGINX_VERSION="$(cat /src/nginx-version)" \
    && cd "nginx-$NGINX_VERSION" \
    && ./configure --with-compat --add-dynamic-module=/src/ngx_brotli >/dev/null \
    && make -j"$(nproc)" modules \
    && mkdir -p /out \
    && cp objs/ngx_http_brotli_filter_module.so objs/ngx_http_brotli_static_module.so /out/

# Official Docker Hub image with --with-http_v3_module and nginx UID 101.
FROM nginx:1.31.9-alpine
USER root
# git-daemon contains git-http-backend on Alpine; Nginx FastCGI alone cannot run CGI.
RUN apk add --no-cache git git-daemon fcgiwrap spawn-fcgi tini \
    && nginx -v 2>&1 | grep -qx 'nginx version: nginx/1.31.9' \
    && nginx -V 2>&1 | grep -q -- --with-http_v3_module \
    && test -x /usr/libexec/git-core/git-http-backend \
    && test "$(id -u nginx)" = 101 && test "$(id -g nginx)" = 101 \
    && mkdir -p /mnt/git-repos /usr/share/nginx/html /usr/local/share/devops-git-home \
    && chown nginx:nginx /mnt/git-repos
COPY --from=brotli /out/ /usr/lib/nginx/modules/
RUN printf 'load_module /usr/lib/nginx/modules/ngx_http_brotli_filter_module.so;\nload_module /usr/lib/nginx/modules/ngx_http_brotli_static_module.so;\nevents {}\nhttp { brotli on; brotli_static on; }\n' > /tmp/brotli-check.conf \
    && nginx -t -c /tmp/brotli-check.conf 2>&1 | grep -q successful
COPY gateway/nginx-main.conf /etc/nginx/nginx.conf
COPY gateway/nginx.conf /etc/nginx/conf.d/devops.conf
COPY gateway/start.sh /usr/local/bin/devops-gateway-start
COPY gateway/git-http-backend.sh /usr/local/bin/devops-git-http-backend
COPY gateway/hooks/ /usr/local/lib/devops-git-hooks/
RUN chmod +x /usr/local/bin/devops-gateway-start /usr/local/bin/devops-git-http-backend /usr/local/lib/devops-git-hooks/pre-receive /usr/local/lib/devops-git-hooks/post-receive
EXPOSE 80/tcp 443/tcp 443/udp
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD test -s /usr/share/nginx/html/current/index.html && wget -q -O /dev/null --no-check-certificate https://127.0.0.1/gateway-health && nc -z 127.0.0.1 9000
ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/devops-gateway-start"]
