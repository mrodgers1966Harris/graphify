"""URL ingest must not let a public URL pull a private or link-local target.

`ingest` treats any URL string that contains ``youtube.com`` or ``youtu.be`` as a
video and downloads it with yt-dlp. ``download_audio`` checks that original URL
once. These tests point that URL at a local public address, then serve a
redirect or a media playlist whose next hop is loopback or link-local.

The tests fail while that later hop is requested or its body is written.
Checking the original URL again still leaves them failing: the URL that was
allowed is not the URL that gets fetched.
"""
from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

import yt_dlp  # the downloader under test; collection fails if it is not installed
from graphify.ingest import ingest
from graphify.security import validate_url
from graphify.transcribe import download_audio, transcribe, transcribe_all

_MARKER = b"INTERNAL_RESPONSE_MARKER"
_PUBLIC_IP = "1.2.3.4"
_LOOPBACK_IP = "127.0.0.1"
_LINK_LOCAL_IP = "169.254.169.254"
_ATTACKER_HOST = "attacker.example"
_HOSTS_TAG = "# graphify-url-ingest-ssrf-test"
_REPO_ROOT = Path(__file__).resolve().parents[1]


class _RecordingServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], name: str):
        self.name = name
        self.hits: list[str] = []
        self._lock = threading.Lock()
        super().__init__(address, _Handler)

    def record(self, path: str) -> None:
        with self._lock:
            self.hits.append(path)

    def clear(self) -> None:
        with self._lock:
            self.hits.clear()

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.hits)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_HEAD(self) -> None:  # noqa: N802
        self._respond(include_body=False)

    def do_GET(self) -> None:  # noqa: N802
        self._respond(include_body=True)

    def log_message(self, fmt: str, *args) -> None:
        return

    def _respond(self, include_body: bool) -> None:
        self.server.record(urllib.parse.urlparse(self.path).path)
        kind = self.server.name
        if kind == "public":
            path = urllib.parse.urlparse(self.path).path
            if path == "/playlist.m3u8":
                body = _media_playlist(self.server.segment_url)
                self._send(200, "application/vnd.apple.mpegurl", body, include_body)
                return
            if path == "/to-link-local" or path.startswith("/to-link-local/"):
                self._redirect(self.server.link_local_url)
                return
            self._redirect(self.server.loopback_url)
            return
        body = _MARKER
        content_type = "text/plain" if kind == "link-local" else "audio/mp4"
        self._send(200, content_type, body, include_body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def _send(self, status: int, content_type: str, body: bytes, include_body: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if include_body:
            self.wfile.write(body)


def _media_playlist(segment_url: str) -> bytes:
    # GenericIE treats a body starting with #EXTM3U as an HLS playlist and the
    # native HLS downloader requests each absolute media URL in it.
    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:2\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXTINF:2.0,\n"
        f"{segment_url}\n"
        "#EXT-X-ENDLIST\n"
    ).encode()


def _can_bind(ip: str) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((ip, 0))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _ensure_loopback_alias(ip: str) -> bool:
    """Return True when this call added ``ip`` to the loopback interface."""
    if _can_bind(ip):
        return False
    commands = (
        ["/usr/sbin/ip", "addr", "add", f"{ip}/32", "dev", "lo"],
        ["sudo", "-n", "/usr/sbin/ip", "addr", "add", f"{ip}/32", "dev", "lo"],
    )
    errors: list[str] = []
    for command in commands:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
        if proc.returncode == 0 or _can_bind(ip):
            return True
        errors.append(proc.stderr.strip() or f"{command[0]} exited {proc.returncode}")
    pytest.fail(f"cannot bind {ip} on the local host: {errors}")
    return False


def _drop_loopback_alias(ip: str) -> None:
    commands = (
        ["/usr/sbin/ip", "addr", "del", f"{ip}/32", "dev", "lo"],
        ["sudo", "-n", "/usr/sbin/ip", "addr", "del", f"{ip}/32", "dev", "lo"],
    )
    for command in commands:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return


def _route_is_local(ip: str) -> None:
    proc = subprocess.run(
        ["/usr/sbin/ip", "route", "get", ip],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or " dev lo " not in f" {proc.stdout} ":
        pytest.fail(
            f"{ip} is not routed to the local host ({proc.stdout!r} {proc.stderr!r}); "
            "refusing to run the download"
        )


def _write_hosts(text: str) -> None:
    proc = subprocess.run(
        ["sudo", "-n", "tee", "/etc/hosts"],
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        Path("/etc/hosts").write_text(text)


def _ensure_attacker_host() -> None:
    path = Path("/etc/hosts")
    original = path.read_text()
    line = f"{_PUBLIC_IP} {_ATTACKER_HOST} {_HOSTS_TAG}"
    kept = [row for row in original.splitlines() if _HOSTS_TAG not in row]
    if line not in kept:
        kept.append(line)
    _write_hosts("\n".join(kept) + "\n")
    infos = socket.getaddrinfo(_ATTACKER_HOST, 80, socket.AF_UNSPEC, socket.SOCK_STREAM)
    addresses = {item[4][0] for item in infos}
    if addresses != {_PUBLIC_IP}:
        pytest.fail(
            f"{_ATTACKER_HOST} resolved to {addresses}, not {_PUBLIC_IP}; "
            "refusing to run the download"
        )


def _remove_attacker_host() -> None:
    path = Path("/etc/hosts")
    try:
        original = path.read_text()
    except OSError:
        return
    kept = [row for row in original.splitlines() if _HOSTS_TAG not in row]
    updated = "\n".join(kept) + "\n"
    if updated != original:
        try:
            _write_hosts(updated)
        except OSError:
            return


def _http_get(host: str, port: int, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=2)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


class _Net:
    def __init__(
        self,
        public: _RecordingServer,
        loopback: _RecordingServer,
        link_local: _RecordingServer,
    ):
        self.public = public
        self.loopback = loopback
        self.link_local = link_local
        public_port = public.server_address[1]
        loop_port = loopback.server_address[1]
        link_port = link_local.server_address[1]
        self.loopback_url = f"http://{_LOOPBACK_IP}:{loop_port}/latest/meta-data/"
        self.link_local_url = f"http://{_LINK_LOCAL_IP}:{link_port}/latest/meta-data/"
        public.segment_url = f"http://{_LOOPBACK_IP}:{loop_port}/segment.ts"
        public.loopback_url = self.loopback_url
        public.link_local_url = self.link_local_url
        self.youtube_url = f"http://{_ATTACKER_HOST}:{public_port}/youtube.com"
        self.short_url = f"http://{_ATTACKER_HOST}:{public_port}/youtu.be"
        # The path contains the youtube.com text, so ingest classifies this as a
        # video. The host is still the attacker hostname.
        self.link_local_redirect_url = (
            f"http://{_ATTACKER_HOST}:{public_port}/to-link-local/youtube.com"
        )
        self.playlist_url = f"http://{_ATTACKER_HOST}:{public_port}/playlist.m3u8?q=youtube.com"

    def clear(self) -> None:
        self.public.clear()
        self.loopback.clear()
        self.link_local.clear()


@pytest.fixture(scope="module")
def net():
    # uv.lock pins yt-dlp 2026.6.9, whose runtime version string is 2026.06.09.
    assert yt_dlp.version.__version__ == "2026.06.09"
    added_aliases = [
        ip for ip in (_PUBLIC_IP, _LINK_LOCAL_IP) if _ensure_loopback_alias(ip)
    ]
    _route_is_local(_PUBLIC_IP)
    _route_is_local(_LINK_LOCAL_IP)
    _ensure_attacker_host()

    public = _RecordingServer((_PUBLIC_IP, 0), "public")
    loopback = _RecordingServer((_LOOPBACK_IP, 0), "loopback")
    link_local = _RecordingServer((_LINK_LOCAL_IP, 0), "link-local")
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (public, loopback, link_local)
    ]
    for thread in threads:
        thread.start()
    fixture = _Net(public, loopback, link_local)

    status, _body = _http_get(_PUBLIC_IP, public.server_address[1], "/youtube.com")
    assert status == 302
    status, body = _http_get(_LOOPBACK_IP, loopback.server_address[1], "/latest/meta-data/")
    assert status == 200 and body == _MARKER
    status, body = _http_get(_LINK_LOCAL_IP, link_local.server_address[1], "/latest/meta-data/")
    assert status == 200 and body == _MARKER
    with pytest.raises(ValueError):
        validate_url(fixture.loopback_url)
    with pytest.raises(ValueError):
        validate_url(fixture.link_local_url)
    validate_url(fixture.youtube_url)
    fixture.clear()

    yield fixture

    for server in (public, loopback, link_local):
        server.shutdown()
    _remove_attacker_host()
    for ip in added_aliases:
        _drop_loopback_alias(ip)


def _written_files(output_dir: Path) -> list[str]:
    if not output_dir.exists():
        return []
    return sorted(
        str(path.relative_to(output_dir)) for path in output_dir.rglob("*") if path.is_file()
    )


def _files_with_marker(output_dir: Path) -> list[str]:
    if not output_dir.exists():
        return []
    found = []
    for path in output_dir.rglob("*"):
        if path.is_file() and _MARKER in path.read_bytes():
            found.append(str(path.relative_to(output_dir)))
    return sorted(found)


def _exercise(invoke, url: str, output_dir: Path, private: list[_RecordingServer]) -> None:
    # The URL passed to the product resolves to a public address. A second
    # lookup of this same URL, immediately before the download, still allows it.
    validate_url(url)
    for server in private:
        server.clear()
    refused = False
    try:
        invoke(url, output_dir)
    except ImportError:
        raise
    except Exception:
        refused = True
    leaked = [(server.name, server.snapshot()) for server in private if server.snapshot()]
    assert leaked == []
    assert _files_with_marker(output_dir) == []
    if refused:
        # A refusal that already wrote or deleted a file is still a failure.
        assert _written_files(output_dir) == []


def _invoke_download(url: str, output_dir: Path) -> None:
    download_audio(url, output_dir)


def _invoke_ingest(url: str, output_dir: Path) -> None:
    ingest(url, output_dir)


def _invoke_transcribe(url: str, output_dir: Path) -> None:
    with patch("graphify.transcribe._get_whisper", side_effect=RuntimeError("whisper not used")):
        transcribe(url, output_dir=output_dir)


def _invoke_transcribe_all(url: str, output_dir: Path) -> None:
    with patch("graphify.transcribe._get_whisper", side_effect=RuntimeError("whisper not used")):
        transcribe_all([url], output_dir=output_dir)


def _invoke_cli_add(url: str, output_dir: Path) -> None:
    _run_module(["-m", "graphify", "add", url, "--dir", str(output_dir)], output_dir)


def _invoke_module_cli(url: str, output_dir: Path) -> None:
    _run_module(["-m", "graphify.ingest", url, str(output_dir)], output_dir)


def _run_module(args: list[str], output_dir: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT) + (f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=output_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    text = f"{proc.stdout}\n{proc.stderr}"
    if "No module named" in text:
        pytest.fail(text)
    if proc.returncode != 0:
        raise RuntimeError(text)


@pytest.mark.parametrize(
    "invoke",
    [
        _invoke_download,
        _invoke_ingest,
        _invoke_transcribe,
        _invoke_transcribe_all,
        _invoke_cli_add,
        _invoke_module_cli,
    ],
    ids=["download_audio", "ingest", "transcribe", "transcribe_all", "cli_add", "ingest_module"],
)
def test_youtube_substring_redirect_to_loopback_is_not_fetched(invoke, net, tmp_path):
    _exercise(invoke, net.youtube_url, tmp_path, [net.loopback, net.link_local])


@pytest.mark.parametrize(
    "invoke",
    [_invoke_download, _invoke_ingest, _invoke_cli_add],
    ids=["download_audio", "ingest", "cli_add"],
)
def test_youtu_be_substring_redirect_to_loopback_is_not_fetched(invoke, net, tmp_path):
    _exercise(invoke, net.short_url, tmp_path, [net.loopback, net.link_local])


@pytest.mark.parametrize(
    "invoke",
    [_invoke_download, _invoke_ingest, _invoke_transcribe, _invoke_transcribe_all],
    ids=["download_audio", "ingest", "transcribe", "transcribe_all"],
)
def test_redirect_to_link_local_is_not_fetched(invoke, net, tmp_path):
    _exercise(invoke, net.link_local_redirect_url, tmp_path, [net.loopback, net.link_local])


@pytest.mark.parametrize(
    "invoke",
    [_invoke_download, _invoke_ingest, _invoke_transcribe, _invoke_transcribe_all],
    ids=["download_audio", "ingest", "transcribe", "transcribe_all"],
)
def test_playlist_media_url_on_loopback_is_not_fetched(invoke, net, tmp_path):
    _exercise(invoke, net.playlist_url, tmp_path, [net.loopback, net.link_local])
