"""Consistent HTTP proxy discovery for WSS, artifacts and Git on native platforms."""

import ssl
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx


def network_proxy(server):
    parsed = urlsplit(server)
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"} or proxy_bypass(parsed.netloc):
        return None
    proxies = getproxies()
    proxy = proxies.get(parsed.scheme) or proxies.get("all")
    if proxy and urlsplit(proxy).scheme not in {"http", "https"}:
        raise RuntimeError("请通过 HTTP_PROXY / HTTPS_PROXY 配置 HTTP CONNECT 代理")
    return proxy


def artifact_client(server, **options):
    # None explicitly means a direct connection here. Do not let httpx rediscover
    # environment proxies after network_proxy has decided to bypass them.
    return httpx.AsyncClient(
        proxy=network_proxy(server),
        trust_env=False,
        verify=ssl.create_default_context(),
        **options,
    )
