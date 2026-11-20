"""Environment forward proxies must not re-resolve guarded fetch URLs.

``safe_fetch`` checks the URL on this machine, then builds an opener that still
installs urllib's default ``ProxyHandler``. With ``http_proxy`` / ``HTTP_PROXY``
(or ``https_proxy`` / ``HTTPS_PROXY``) set, the handler connects to the proxy
and sends the original hostname in the absolute-form request line or in
``CONNECT``. These tests stand up that proxy. Its resolver maps the public
name to a loopback HTTP server the client-side guard would reject. They fail
while the opener still asks the proxy to dial that name.

Transcription (``graphify.transcribe.download_audio``) does not use this opener.
It is not covered here. Tweet and arXiv-id ingest fetch a fixed host, not the
caller-supplied host; the shared fetch they do use is ``safe_fetch``.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from graphify.ingest import ingest
from graphify.security import _ip_is_blocked, safe_fetch

# Documentation ranges are private in this Python's ipaddress, so they never
# reach the guarded connect. These globals are outside every range
# ``_ip_is_blocked`` rejects, and the fixture pins them on the loopback
# interface so the sockets stay on this machine.
_PROXY_IP = "1.2.3.4"
_PUBLIC_IP = "1.2.3.5"
_REDIRECT_IP = "1.2.3.6"
_PUBLIC_HOST = "graphify-ssrf-public.test"
_REDIRECT_HOST = "graphify-ssrf-redirect.test"
_HOSTS_TAG = "graphify-proxy-ssrf-fixture"
_MARKER = b"GRAPHIFY-PROXY-BODY-MARKER"
_HTML = (
    b"<html><head><title>t</title></head><body>"
    + _MARKER
    + b"</body></html>"
)
_PDF = b"%PDF-1.4\n" + _MARKER + b"\n"
_PNG = b"\x89PNG\r\n" + _MARKER
_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROXY_ENV_NAMES = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
)


def _run_privileged(argv: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        argv = ["sudo", "-n", *argv]
    return subprocess.run(argv, check=check, capture_output=True, text=True)


def _rewrite_hosts(install: bool) -> None:
    """Point the two attacker names at the public fixture addresses.

    glibc resolves ``hosts: files`` before DNS (``/etc/nsswitch.conf``), which
    is the same lookup ``socket.getaddrinfo`` uses inside ``validate_url``.
    """
    path = Path("/etc/hosts")
    kept: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if _HOSTS_TAG in line or _PUBLIC_HOST in line or _REDIRECT_HOST in line:
            continue
        kept.append(line)
    if install:
        kept.append(f"# {_HOSTS_TAG}")
        kept.append(f"{_PUBLIC_IP} {_PUBLIC_HOST}")
        kept.append(f"{_REDIRECT_IP} {_REDIRECT_HOST}")
    payload = "\n".join(kept).rstrip() + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="graphify-hosts-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        _run_privileged(["cp", tmp_name, str(path)], check=True)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _pin_loopback_address(ip: str, *, present: bool) -> None:
    action = "replace" if present else "del"
    _run_privileged(["ip", "addr", action, f"{ip}/32", "dev", "lo"], check=present)


def _read_headers(conn: socket.socket) -> tuple[str, str, str]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    head = data.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1", errors="replace")
    lines = head.split("\r\n")
    parts = (lines[0] if lines else "").split(" ")
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def _internal_response(path: str) -> bytes:
    if path.startswith("/start"):
        location = f"http://{_REDIRECT_HOST}/landed"
        body = b""
        header = (
            "HTTP/1.1 302 Found\r\n"
            f"Location: {location}\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        return header.encode("ascii") + body
    if path.endswith(".pdf"):
        body = _PDF
        content_type = "application/pdf"
    elif path.endswith(".png"):
        body = _PNG
        content_type = "image/png"
    else:
        body = _HTML
        content_type = "text/html"
    header = (
        "HTTP/1.1 200 OK\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    return header.encode("ascii") + body


def _serve_internal(ready: threading.Event, port: list[int], stop: threading.Event) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(64)
    server.settimeout(0.2)
    port.append(server.getsockname()[1])
    ready.set()
    try:
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            try:
                conn.settimeout(2)
                _method, path, _version = _read_headers(conn)
                target = path.split("?", 1)[0] or "/"
                conn.sendall(_internal_response(target))
            except OSError:
                pass
            finally:
                conn.close()
    finally:
        server.close()


class _SplitDnsProxy:
    """Forward proxy whose name map is independent of the client's resolver.

    urllib sends ``GET http://host/path HTTP/1.1`` after ``Request.set_proxy``
    rewrites the selector to the full URL (``urllib/request.py`` ``set_proxy``),
    or ``CONNECT host:443`` when the URL is https (``http/client.py``
    ``HTTPConnection._tunnel``). This proxy dials 127.0.0.1 only for the two
    fixture names, which is the split-DNS step the product never sees.
    """

    def __init__(self) -> None:
        self.request_lines: list[str] = []
        self.loopback_dials: list[tuple[str, int]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.port = 0
        self._thread: threading.Thread | None = None
        self._server: socket.socket | None = None
        self._name_map: dict[str, tuple[str, int]] = {}

    @property
    def url(self) -> str:
        return f"http://{_PROXY_IP}:{self.port}"

    def set_name_map(self, name_map: dict[str, tuple[str, int]]) -> None:
        self._name_map = dict(name_map)

    def reset(self) -> None:
        with self._lock:
            self.request_lines.clear()
            self.loopback_dials.clear()

    def unpinned_lines(self) -> list[str]:
        with self._lock:
            return [
                line
                for line in self.request_lines
                if _PUBLIC_HOST in line or _REDIRECT_HOST in line
            ]

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((_PROXY_IP, 0))
        server.listen(64)
        server.settimeout(0.2)
        self.port = server.getsockname()[1]
        self._server = server
        self._thread = threading.Thread(
            target=self._accept_loop, name="split-dns-proxy", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                conn, _addr = self._server.accept()
            except OSError:
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(5)
            method, target, _version = _read_headers(conn)
            line = f"{method} {target}"
            with self._lock:
                self.request_lines.append(line)
            if method.upper() == "CONNECT":
                host, _, port_text = target.partition(":")
                upstream = self._dial_split(host, int(port_text or "443"))
                if upstream is not None:
                    upstream.close()
                conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                return
            split = urlsplit(target)
            host = split.hostname or ""
            path = split.path or "/"
            if split.query:
                path = f"{path}?{split.query}"
            upstream = self._dial_split(host, split.port or 80)
            if upstream is None:
                message = b"gateway error"
                conn.sendall(
                    b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: "
                    + str(len(message)).encode("ascii")
                    + b"\r\nConnection: close\r\n\r\n"
                    + message
                )
                return
            upstream.sendall(
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii")
            )
            while True:
                chunk = upstream.recv(65536)
                if not chunk:
                    break
                conn.sendall(chunk)
            upstream.close()
        except OSError:
            pass
        finally:
            conn.close()

    def _dial_split(self, host: str, port: int) -> socket.socket | None:
        mapped = self._name_map.get(host.lower())
        if mapped is not None:
            with self._lock:
                self.loopback_dials.append(mapped)
            try:
                return socket.create_connection(mapped, timeout=2)
            except OSError:
                return None
        try:
            return socket.create_connection((host, port), timeout=2)
        except OSError:
            return None


class _NetworkFixture:
    def __init__(self) -> None:
        self.proxy = _SplitDnsProxy()
        self._internal_stop = threading.Event()
        self._internal_thread: threading.Thread | None = None
        self._installed_ips: list[str] = []
        self._hosts_installed = False

    def start(self) -> None:
        for ip in (_PROXY_IP, _PUBLIC_IP, _REDIRECT_IP):
            if _ip_is_blocked(ipaddress.ip_address(ip)):
                raise RuntimeError(
                    f"{ip} is blocked by _ip_is_blocked; fixture cannot represent a public proxy"
                )
        for ip in (_PROXY_IP, _PUBLIC_IP, _REDIRECT_IP):
            _pin_loopback_address(ip, present=True)
            self._installed_ips.append(ip)
        _rewrite_hosts(True)
        self._hosts_installed = True
        for host, expected in ((_PUBLIC_HOST, _PUBLIC_IP), (_REDIRECT_HOST, _REDIRECT_IP)):
            infos = socket.getaddrinfo(host, 80, socket.AF_UNSPEC, socket.SOCK_STREAM)
            addrs = {item[4][0] for item in infos}
            if addrs != {expected}:
                raise RuntimeError(f"{host} resolved to {addrs}, expected {{{expected}}}")
        ready = threading.Event()
        port: list[int] = []
        self._internal_thread = threading.Thread(
            target=_serve_internal,
            args=(ready, port, self._internal_stop),
            name="split-dns-origin",
            daemon=True,
        )
        self._internal_thread.start()
        if not ready.wait(timeout=2) or not port:
            raise RuntimeError("internal origin failed to bind 127.0.0.1")
        origin = ("127.0.0.1", port[0])
        self.proxy.set_name_map({_PUBLIC_HOST: origin, _REDIRECT_HOST: origin})
        self.proxy.start()

    def stop(self) -> None:
        self.proxy.stop()
        self._internal_stop.set()
        if self._internal_thread is not None:
            self._internal_thread.join(timeout=2)
        if self._hosts_installed:
            _rewrite_hosts(False)
        for ip in self._installed_ips:
            _pin_loopback_address(ip, present=False)


@pytest.fixture(scope="module")
def split_dns_network():
    fixture = _NetworkFixture()
    fixture.start()
    try:
        yield fixture
    finally:
        fixture.stop()


@pytest.fixture
def proxy_env(monkeypatch: pytest.MonkeyPatch, split_dns_network: _NetworkFixture):
    for name in _PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("REQUEST_METHOD", raising=False)
    split_dns_network.proxy.reset()
    return split_dns_network


def _assert_proxy_did_not_receive_hostname(network: _NetworkFixture) -> None:
    lines = network.proxy.unpinned_lines()
    assert lines == [], (
        "guarded fetch handed the original hostname to the forward proxy: "
        + repr(lines)
    )
    assert network.proxy.loopback_dials == [], (
        "forward proxy dialed the split-DNS loopback origin: "
        + repr(network.proxy.loopback_dials)
    )


def _assert_marker_absent(body: bytes | None, directory: Path | None) -> None:
    if body is not None:
        assert _MARKER not in body
    if directory is None:
        return
    for path in directory.rglob("*"):
        if path.is_file():
            assert _MARKER not in path.read_bytes()


def _set_proxy(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)


def test_safe_fetch_ignores_http_proxy_split_dns(
    proxy_env: _NetworkFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_proxy(monkeypatch, "http_proxy", proxy_env.proxy.url)
    body, _error = _call_safe_fetch(f"http://{_PUBLIC_HOST}/page")
    _assert_proxy_did_not_receive_hostname(proxy_env)
    _assert_marker_absent(body, None)


def test_safe_fetch_ignores_uppercase_http_proxy_split_dns(
    proxy_env: _NetworkFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_proxy(monkeypatch, "HTTP_PROXY", proxy_env.proxy.url)
    body, _error = _call_safe_fetch(f"http://{_PUBLIC_HOST}/page")
    _assert_proxy_did_not_receive_hostname(proxy_env)
    _assert_marker_absent(body, None)


def test_safe_fetch_redirect_ignores_proxy_split_dns(
    proxy_env: _NetworkFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_proxy(monkeypatch, "http_proxy", proxy_env.proxy.url)
    body, _error = _call_safe_fetch(f"http://{_PUBLIC_HOST}/start")
    _assert_proxy_did_not_receive_hostname(proxy_env)
    _assert_marker_absent(body, None)


@pytest.mark.parametrize("suffix", [".pdf", ".png"])
def test_ingest_binary_ignores_proxy_split_dns(
    proxy_env: _NetworkFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    suffix: str,
) -> None:
    _set_proxy(monkeypatch, "http_proxy", proxy_env.proxy.url)
    url = f"http://{_PUBLIC_HOST}/payload{suffix}"
    try:
        ingest(url, tmp_path)
    except (OSError, ValueError, RuntimeError):
        pass
    _assert_proxy_did_not_receive_hostname(proxy_env)
    _assert_marker_absent(None, tmp_path)


def test_ingest_webpage_ignores_proxy_split_dns(
    proxy_env: _NetworkFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_proxy(monkeypatch, "http_proxy", proxy_env.proxy.url)
    try:
        ingest(f"http://{_PUBLIC_HOST}/page", tmp_path)
    except (OSError, ValueError, RuntimeError):
        pass
    _assert_proxy_did_not_receive_hostname(proxy_env)
    _assert_marker_absent(None, tmp_path)


def test_https_proxy_does_not_receive_original_hostname(
    proxy_env: _NetworkFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_proxy(monkeypatch, "https_proxy", proxy_env.proxy.url)
    body, _error = _call_safe_fetch(f"https://{_PUBLIC_HOST}/page")
    _assert_proxy_did_not_receive_hostname(proxy_env)
    _assert_marker_absent(body, None)


def test_uppercase_https_proxy_does_not_receive_original_hostname(
    proxy_env: _NetworkFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_proxy(monkeypatch, "HTTPS_PROXY", proxy_env.proxy.url)
    body, _error = _call_safe_fetch(f"https://{_PUBLIC_HOST}/page")
    _assert_proxy_did_not_receive_hostname(proxy_env)
    _assert_marker_absent(body, None)


def test_cli_add_ignores_http_proxy_split_dns(
    proxy_env: _NetworkFixture, tmp_path: Path
) -> None:
    env = os.environ.copy()
    for name in _PROXY_ENV_NAMES:
        env.pop(name, None)
    env.pop("REQUEST_METHOD", None)
    env["http_proxy"] = proxy_env.proxy.url
    subprocess.run(
        [
            sys.executable,
            "-m",
            "graphify",
            "add",
            f"http://{_PUBLIC_HOST}/page",
            "--dir",
            str(tmp_path),
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    _assert_proxy_did_not_receive_hostname(proxy_env)
    _assert_marker_absent(None, tmp_path)


def _call_safe_fetch(url: str) -> tuple[bytes | None, BaseException | None]:
    try:
        return safe_fetch(url, timeout=5), None
    except (OSError, ValueError) as exc:
        return None, exc
