"""Read-only HTTP/3 probe with certificate validation and no TCP fallback."""

import argparse
import asyncio
import json
import socket
from urllib.parse import urlsplit

from aioquic.asyncio import QuicConnectionProtocol, connect
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ConnectionTerminated, ProtocolNegotiated


class HTTP3Client(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http = H3Connection(self._quic)
        self.response = None
        self.headers = {}
        self.body = bytearray()
        self.alpn = None

    def quic_event_received(self, event):
        if isinstance(event, ProtocolNegotiated):
            self.alpn = event.alpn_protocol
        if (
            isinstance(event, ConnectionTerminated)
            and self.response is not None
            and not self.response.done()
        ):
            self.response.set_exception(ConnectionError("QUIC connection terminated"))
        for message in self.http.handle_event(event):
            if not isinstance(message, (HeadersReceived, DataReceived)):
                continue
            if self.response is None or self.response.done():
                continue
            if isinstance(message, HeadersReceived):
                self.headers.update(message.headers)
            else:
                self.body.extend(message.data)
                if len(self.body) > 8 * 1024 * 1024:
                    self.response.set_exception(
                        ValueError("HTTP/3 probe response exceeds 8 MiB")
                    )
                    continue
            if message.stream_ended:
                self.response.set_result(
                    (int(self.headers[b":status"]), self.headers, bytes(self.body))
                )

    async def get(self, authority, path):
        if self.alpn != "h3":
            raise RuntimeError("Server did not negotiate HTTP/3")
        self.response = asyncio.get_running_loop().create_future()
        self.http.send_headers(
            self._quic.get_next_available_stream_id(),
            [
                (b":method", b"GET"),
                (b":scheme", b"https"),
                (b":authority", authority.encode()),
                (b":path", path.encode()),
            ],
            end_stream=True,
        )
        self.transmit()
        return await self.response


async def get_http3(url, *, ca_file=None, family=socket.AF_UNSPEC):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Expected an HTTPS URL without credentials")
    path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
    addresses = await asyncio.get_running_loop().getaddrinfo(
        parsed.hostname, parsed.port or 443, type=socket.SOCK_DGRAM, family=family
    )
    errors = []
    # aioquic.connect uses only the first DNS answer. Try other addresses when
    # that route is unavailable; every attempt still requires QUIC + valid TLS.
    for address in dict.fromkeys(item[4][0] for item in addresses):
        configuration = QuicConfiguration(
            is_client=True,
            alpn_protocols=["h3"],
            server_name=parsed.hostname,
            idle_timeout=5,
        )
        if ca_file:
            configuration.load_verify_locations(cafile=str(ca_file))
        try:
            async with asyncio.timeout(20):
                async with connect(
                    address,
                    parsed.port or 443,
                    configuration=configuration,
                    create_protocol=HTTP3Client,
                ) as client:
                    return await client.get(parsed.netloc, path)
        except (TimeoutError, ConnectionError, OSError) as error:
            errors.append(f"{address}: {type(error).__name__}")
    raise ConnectionError(
        "HTTP/3 unavailable on resolved addresses: " + "; ".join(errors)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://devops.kevinc.ltd")
    parser.add_argument("--ca-file", help="Trust the local integration certificate")
    family = parser.add_mutually_exclusive_group()
    family.add_argument("--ipv4", action="store_true", help="Probe IPv4 only")
    family.add_argument("--ipv6", action="store_true", help="Probe IPv6 only")
    args = parser.parse_args()
    origin = args.url.rstrip("/")
    family = (
        socket.AF_INET
        if args.ipv4
        else socket.AF_INET6
        if args.ipv6
        else socket.AF_UNSPEC
    )
    for path in ("/", "/api/health/ready"):
        status, headers, body = asyncio.run(
            get_http3(origin + path, ca_file=args.ca_file, family=family)
        )
        if status != 200 or b'h3=":443"' not in headers.get(b"alt-svc", b""):
            raise RuntimeError(f"HTTP/3 check failed for {path}: status {status}")
        if path.endswith("ready") and json.loads(body).get("status") != "ready":
            raise RuntimeError("Public API is not ready")
        print(f"HTTP/3 {status} {path} ({len(body)} bytes); certificate verified")


if __name__ == "__main__":
    main()
