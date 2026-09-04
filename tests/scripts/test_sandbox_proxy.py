"""Tests for scripts/sandbox/proxy.py's response Connection-header rewrite.

The dev-sandbox MITM proxy (``handle_connect``) serves exactly one HTTP
request per CONNECT tunnel, then tears the TLS session down when the
handler returns. ``close_request`` forces ``Connection: close`` on the
*outbound* request to upstream, but the response was being relayed back to
the client byte-for-byte with no header rewrite. If a real upstream (e.g.
registry.npmjs.org behind a CDN) ignores that hint and replies
``Connection: keep-alive`` anyway, a keep-alive client -- Node's
``https.Agent``, which npm's registry client uses -- believes the tunnel is
reusable and pipelines a second request onto a socket this proxy is about
to close. Under a real ``npm install``'s concurrent registry fetches that
races into a client-side ``SSLEOFError`` (confirmed via an instrumented
local reproduction of the "Install & Update E2E" installer-route failure).
Forcing ``Connection: close`` on what's relayed to the client, regardless
of what upstream actually said, is what makes the one-shot-per-tunnel
contract hold from the client's point of view too.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROXY_PATH = REPO_ROOT / "scripts" / "sandbox" / "proxy.py"


def _load_proxy_module():
    """Import scripts/sandbox/proxy.py as a module.

    It's a script, not a package, and reads its three positional CLI args
    (fixture root, certs dir, real CA bundle) at import time via
    ``sys.argv[1:]``. None of that is needed to exercise the response
    rewrite under test, so dummy paths are enough.
    """
    old_argv = sys.argv
    sys.argv = ["proxy.py", "/nonexistent/root", "/nonexistent/certs", "/nonexistent/ca.pem"]
    try:
        spec = importlib.util.spec_from_file_location("dev_sandbox_proxy", PROXY_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["dev_sandbox_proxy"] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old_argv
    return mod


@pytest.fixture(scope="module")
def proxy():
    return _load_proxy_module()


def _drain(sock, timeout=2.0):
    sock.settimeout(timeout)
    chunks = []
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except socket.timeout:
        pass
    return b"".join(chunks)


class TestForceConnectionClose:
    def test_rewrites_keep_alive_to_close(self, proxy):
        headers = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Connection: keep-alive\r\n"
            b"Content-Length: 5\r\n"
            b"\r\n"
        )
        rewritten = proxy._force_connection_close(headers)
        assert b"Connection: close" in rewritten
        assert b"keep-alive" not in rewritten
        assert rewritten.startswith(b"HTTP/1.1 200 OK\r\n")
        assert rewritten.endswith(b"\r\n\r\n")

    def test_inserts_close_when_absent(self, proxy):
        headers = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
        rewritten = proxy._force_connection_close(headers)
        assert b"Connection: close" in rewritten

    def test_case_insensitive_header_name(self, proxy):
        headers = b"HTTP/1.1 200 OK\r\nconnection: Keep-Alive\r\n\r\n"
        rewritten = proxy._force_connection_close(headers)
        assert rewritten.lower().count(b"connection:") == 1
        assert b"close" in rewritten.lower()


class TestRelayResponseForcingClose:
    def test_client_never_sees_keep_alive(self, proxy):
        """The scenario that broke npm: upstream says keep-alive, we must not repeat it."""
        upstream_a, upstream_b = socket.socketpair()
        client_a, client_b = socket.socketpair()
        # Bigger than one recv() chunk in _read_headers (4096), so the header
        # block and the start of the body land in the same read and the
        # leftover-body handoff into relay() actually gets exercised.
        body = b"x" * 5000
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Connection: keep-alive\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )

        def feed_upstream():
            upstream_b.sendall(response)
            upstream_b.close()

        t = threading.Thread(target=feed_upstream)
        t.start()
        try:
            proxy.relay_response_forcing_close(upstream_a, client_b)
        finally:
            t.join(timeout=5)
            upstream_a.close()

        received = _drain(client_a)
        client_a.close()
        client_b.close()

        assert b"Connection: keep-alive" not in received
        assert b"Connection: close" in received
        assert received.endswith(body)  # body must survive byte-for-byte

    def test_small_response_round_trips(self, proxy):
        upstream_a, upstream_b = socket.socketpair()
        client_a, client_b = socket.socketpair()
        response = b"HTTP/1.1 204 No Content\r\nConnection: keep-alive\r\n\r\n"

        def feed_upstream():
            upstream_b.sendall(response)
            upstream_b.close()

        t = threading.Thread(target=feed_upstream)
        t.start()
        try:
            proxy.relay_response_forcing_close(upstream_a, client_b)
        finally:
            t.join(timeout=5)
            upstream_a.close()

        received = _drain(client_a)
        client_a.close()
        client_b.close()

        assert received == b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n"
