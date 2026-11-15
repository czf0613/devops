"""Bounded metadata requests to the configured private Registry, never client URLs."""

import hashlib
import http.client
import json
from collections.abc import Callable
from urllib.parse import quote, urlencode, urlsplit

from fastapi import HTTPException

ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


class RegistryClient:
    def __init__(self, origin: str, *, heartbeat: Callable[[], None] | None = None):
        self.origin = urlsplit(origin)
        self.heartbeat = heartbeat

    def request(self, path: str, method="GET"):
        if self.heartbeat:
            self.heartbeat()
        cls = (
            http.client.HTTPSConnection
            if self.origin.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = cls(self.origin.hostname, self.origin.port, timeout=6)
        try:
            connection.request(method, path, headers={"Accept": ACCEPT})
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise HTTPException(502, "镜像仓库返回的数据过大")
            if response.status == 404:
                raise HTTPException(404, "镜像或版本已不存在，请刷新列表")
            if response.status not in {200, 202}:
                raise HTTPException(502, "镜像仓库操作失败，请稍后重试")
            return raw, dict(response.getheaders())
        except OSError, http.client.HTTPException:
            raise HTTPException(503, "镜像仓库暂时不可用，请稍后重试") from None
        finally:
            connection.close()

    def data(self, path: str):
        raw, _ = self.request(path)
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except ValueError, UnicodeError:
            raise HTTPException(502, "镜像仓库返回了无效数据") from None

    def pages(self, path: str, field: str) -> list[str]:
        values, last = [], ""
        seen = set()
        for _ in range(50):
            page = self.data(path + "?" + urlencode({"n": 100, "last": last})).get(field) or []
            if not isinstance(page, list) or any(not isinstance(item, str) for item in page):
                raise HTTPException(502, "镜像目录数据无效")
            if not page:
                return values
            # Distribution's catalog follows storage-directory traversal, not
            # lexical repository-name order (e.g. team/app before team-extra/app).
            # Preserve its cursor, while rejecting duplicates and pagination loops.
            if (
                len(page) != len(set(page))
                or seen.intersection(page)
                or (field != "repositories" and (page != sorted(page) or page[0] <= last))
            ):
                raise HTTPException(502, "镜像目录分页无效")
            values.extend(page)
            seen.update(page)
            last = page[-1]
            if len(page) < 100:
                return values
        raise HTTPException(503, "镜像目录超过当前查询上限（5000 项）")

    def images(self, namespace: str) -> list[str]:
        prefix = namespace + "/"
        return [
            name for name in self.pages("/v2/_catalog", "repositories") if name.startswith(prefix)
        ]

    def tags(self, name: str) -> list[str]:
        return self.pages(f"/v2/{quote(name, safe='/')}/tags/list", "tags")

    def manifest(self, name: str, reference: str) -> dict:
        raw, _ = self.request(f"/v2/{quote(name, safe='/')}/manifests/{quote(reference, safe=':')}")
        try:
            data = json.loads(raw)
            media = data["mediaType"]
            if media not in ACCEPT.split(", "):
                raise ValueError
            manifests = data.get("manifests", [])
            platforms = list(
                dict.fromkeys(
                    "/".join(
                        str(item["platform"][key])
                        for key in ("os", "architecture", "variant")
                        if item["platform"].get(key)
                    )
                    for item in manifests
                    if isinstance(item.get("platform"), dict)
                    and item["platform"].get("os") not in {None, "unknown"}
                    and item["platform"].get("architecture") not in {None, "unknown"}
                    and item.get("annotations", {}).get("vnd.docker.reference.type")
                    != "attestation-manifest"
                )
            )
            size = sum(int(layer["size"]) for layer in data.get("layers", []))
            return {
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "media_type": media,
                "size_bytes": size,
                "platforms": platforms,
                "attestation_count": sum(
                    item.get("annotations", {}).get("vnd.docker.reference.type")
                    == "attestation-manifest"
                    for item in manifests
                ),
            }
        except ValueError, TypeError, KeyError, AttributeError:
            raise HTTPException(502, "镜像清单格式不受支持") from None

    def delete(self, name: str, digest: str):
        self.request(f"/v2/{quote(name, safe='/')}/manifests/{quote(digest, safe=':')}", "DELETE")

    def delete_tag(self, name: str, tag: str):
        # Supported by the pinned Distribution 3.1.2; internal maintenance only.
        self.request(f"/v2/{quote(name, safe='/')}/manifests/{quote(tag, safe='')}", "DELETE")
