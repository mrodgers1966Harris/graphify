"""Spec for the container's default HTTP bind.

The image entrypoint is ``python -m graphify.serve`` and its default command
is HTTP on a wildcard address with no API key. These tests run that process
and speak MCP over the socket it binds.

They fail while a wildcard bind with no API key still accepts clients, and
while a request whose Host (or Origin) is not on the loopback allowlist can
read the graph. A fix passes them by refusing to start in that configuration
before any client is accepted, and by rejecting unlisted Host and Origin
headers on the request that reads the graph.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("starlette")

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
PLACEHOLDER_API_KEY = "placeholder-api-key"
EVIL_HOST = "rebinding.invalid"
MCP_ACCEPT = "application/json, text/event-stream"

# Every graph-reading tool and resource registered in graphify.serve._build_server.
# PR tools share the same unauthenticated HTTP dispatch; they are called too.
GRAPH_TOOLS = {
    "query_graph",
    "get_node",
    "get_neighbors",
    "get_community",
    "god_nodes",
    "graph_stats",
    "shortest_path",
    "list_prs",
    "get_pr_impact",
    "triage_prs",
}
GRAPH_RESOURCES = {
    "graphify://report",
    "graphify://stats",
    "graphify://god-nodes",
    "graphify://surprises",
    "graphify://audit",
    "graphify://questions",
}

PRIVATE_LABEL = "VaultTokenRotation"
PRIVATE_SOURCE = "src/private/vault_client.py"
PRIVATE_COMMUNITY = "SecretsHandling"
OTHER_LABEL = "OtherRepoSymbol"
OTHER_SOURCE = "src/other/hidden.py"
REPORT_SENTINEL = "PRIVATE_REPORT_SENTINEL"
MARKERS = (
    PRIVATE_LABEL,
    PRIVATE_SOURCE,
    PRIVATE_COMMUNITY,
    "NightlyJob",
    "src/private/nightly.py",
    "BatchWork",
    OTHER_LABEL,
    OTHER_SOURCE,
    REPORT_SENTINEL,
)


def _graph_dir(tmp_path: Path) -> tuple[Path, str]:
    """Mounted graph plus a second project graph reached via project_path."""
    root = tmp_path / "mounted"
    root.mkdir()
    graph = {
        "directed": True,
        "nodes": [
            {
                "id": "vault",
                "label": PRIVATE_LABEL,
                "community": 0,
                "community_name": PRIVATE_COMMUNITY,
                "source_file": PRIVATE_SOURCE,
                "source_location": "L42",
                "file_type": "code",
            },
            {
                "id": "caller",
                "label": "NightlyJob",
                "community": 1,
                "community_name": "BatchWork",
                "source_file": "src/private/nightly.py",
                "source_location": "L7",
                "file_type": "code",
            },
        ],
        "edges": [
            {
                "source": "caller",
                "target": "vault",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "context": "call",
            }
        ],
    }
    graph_path = root / "graph.json"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    (root / "GRAPH_REPORT.md").write_text(
        f"{REPORT_SENTINEL} {PRIVATE_COMMUNITY} {PRIVATE_SOURCE}\n",
        encoding="utf-8",
    )
    project = tmp_path / "other-project"
    (project / "graphify-out").mkdir(parents=True)
    other = {
        "directed": True,
        "nodes": [
            {
                "id": "other",
                "label": OTHER_LABEL,
                "community": 0,
                "community_name": "OtherCommunity",
                "source_file": OTHER_SOURCE,
                "source_location": "L3",
                "file_type": "code",
            }
        ],
        "edges": [],
    }
    (project / "graphify-out" / "graph.json").write_text(json.dumps(other), encoding="utf-8")
    return graph_path, str(project)


def _shadow_gh(tmp_path: Path) -> Path:
    """PATH entry whose gh exits immediately so PR tools cannot open a network client."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
    gh.chmod(0o755)
    return bindir


def _server_env(tmp_path: Path, log_path: Path, api_key: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("GRAPHIFY_API_KEY", None)
    if api_key is not None:
        env["GRAPHIFY_API_KEY"] = api_key
    env["GRAPHIFY_QUERY_LOG"] = str(log_path)
    env["PATH"] = f"{_shadow_gh(tmp_path)}{os.pathsep}/usr/bin{os.pathsep}/bin"
    return env


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _exec_json(dockerfile: str, keyword: str) -> list[str]:
    for raw in dockerfile.splitlines():
        line = raw.strip()
        if line.startswith(keyword + " "):
            payload = line[len(keyword):].strip()
            if payload.startswith("["):
                parsed = json.loads(payload)
                if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                    raise AssertionError(f"{keyword} is not a string array: {payload}")
                return parsed
            raise AssertionError(f"{keyword} is not exec-form JSON: {payload}")
    raise AssertionError(f"{keyword} missing from Dockerfile")


def _image_argv(graph_path: Path, port: int) -> list[str]:
    """Default container command, with only the mount path and listen port substituted."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    argv = _exec_json(text, "ENTRYPOINT") + _exec_json(text, "CMD")
    if argv[0] in {"python", "python3"}:
        argv[0] = sys.executable
    argv = [str(graph_path) if arg == "/data/graph.json" else arg for arg in argv]
    if "--port" in argv:
        argv[argv.index("--port") + 1] = str(port)
    return argv


def _decode_body(raw: str, content_type: str) -> str:
    if "text/event-stream" in content_type:
        messages = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    messages.append(payload)
        return "\n".join(messages)
    return raw


def _mcp_post(
    port: int,
    body: dict,
    host_header: str,
    *,
    connect_host: str = "127.0.0.1",
    api_key: str | None = None,
    session: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict[str, str], str]:
    conn = socket.create_connection((connect_host, port), timeout=10)
    # HTTPConnection does not accept an already-connected IPv6 peer cleanly
    # when the host string is a scoped address, so talk HTTP on the socket.
    request_head = [
        "POST /mcp HTTP/1.1",
        f"Host: {host_header}",
        "Content-Type: application/json",
        f"Accept: {MCP_ACCEPT}",
        "Connection: close",
    ]
    if api_key is not None:
        request_head.append(f"X-API-Key: {api_key}")
    if session is not None:
        request_head.append(f"mcp-session-id: {session}")
    if origin is not None:
        request_head.append(f"Origin: {origin}")
    payload = json.dumps(body).encode("utf-8")
    request_head.append(f"Content-Length: {len(payload)}")
    raw_request = ("\r\n".join(request_head) + "\r\n\r\n").encode("ascii") + payload
    conn.sendall(raw_request)
    chunks = []
    while True:
        piece = conn.recv(65536)
        if not piece:
            break
        chunks.append(piece)
    conn.close()
    raw = b"".join(chunks)
    head, _, rest = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    body_bytes = rest
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body_bytes = _decode_chunked(rest)
    text = _decode_body(body_bytes.decode("utf-8", errors="replace"), headers.get("content-type", ""))
    return status, headers, text


def _decode_chunked(data: bytes) -> bytes:
    out = bytearray()
    while data:
        line, sep, data = data.partition(b"\r\n")
        if not sep:
            break
        size_token = line.split(b";", 1)[0].strip()
        if not size_token:
            continue
        size = int(size_token, 16)
        if size == 0:
            break
        out.extend(data[:size])
        data = data[size:]
        if data.startswith(b"\r\n"):
            data = data[2:]
    return bytes(out)


def _initialize_body() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "wildcard-bind-spec", "version": "0"},
        },
    }


def _rpc(method: str, params: dict, rid: int) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


def _read_operations(project_path: str) -> list[tuple[str, dict, str]]:
    """(method, params, snippet that only this graph read would return)."""
    return [
        ("tools/call", {"name": "query_graph", "arguments": {"question": PRIVATE_LABEL}}, PRIVATE_SOURCE),
        ("tools/call", {"name": "get_node", "arguments": {"label": PRIVATE_LABEL}}, PRIVATE_SOURCE),
        ("tools/call", {"name": "get_neighbors", "arguments": {"label": "NightlyJob"}}, PRIVATE_LABEL),
        (
            "tools/call",
            {"name": "get_community", "arguments": {"community_id": 0}},
            PRIVATE_COMMUNITY,
        ),
        ("tools/call", {"name": "god_nodes", "arguments": {"top_n": 5}}, PRIVATE_LABEL),
        ("tools/call", {"name": "graph_stats", "arguments": {}}, "Communities: 2"),
        (
            "tools/call",
            {"name": "shortest_path", "arguments": {"source": "NightlyJob", "target": PRIVATE_LABEL}},
            "NightlyJob",
        ),
        ("tools/call", {"name": "list_prs", "arguments": {}}, ""),
        ("tools/call", {"name": "get_pr_impact", "arguments": {"pr_number": 1}}, ""),
        ("tools/call", {"name": "triage_prs", "arguments": {}}, ""),
        (
            "tools/call",
            {"name": "get_node", "arguments": {"label": OTHER_LABEL, "project_path": project_path}},
            OTHER_SOURCE,
        ),
        ("resources/read", {"uri": "graphify://report"}, REPORT_SENTINEL),
        ("resources/read", {"uri": "graphify://stats"}, "Communities: 2"),
        ("resources/read", {"uri": "graphify://god-nodes"}, PRIVATE_LABEL),
        ("resources/read", {"uri": "graphify://surprises"}, PRIVATE_LABEL),
        ("resources/read", {"uri": "graphify://audit"}, "Total edges: 1"),
        ("resources/read", {"uri": "graphify://questions"}, PRIVATE_LABEL),
    ]


class _Server:
    def __init__(self, proc: subprocess.Popen[str], port: int, log_path: Path, connect_host: str):
        self.proc = proc
        self.port = port
        self.log_path = log_path
        self.connect_host = connect_host
        self.stderr = ""
        self._err_parts: list[str] = []
        self._reader = threading.Thread(target=self._drain_stderr, daemon=True)
        self._reader.start()

    def _drain_stderr(self) -> None:
        if self.proc.stderr is None:
            return
        self._err_parts.append(self.proc.stderr.read())

    def accepted(self) -> bool:
        try:
            with socket.create_connection((self.connect_host, self.port), timeout=0.2):
                return True
        except OSError:
            return False

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
        self._reader.join(timeout=3)
        if self.proc.poll() is None:
            self.proc.kill()
            self._reader.join(timeout=3)
        self.stderr = "".join(self._err_parts)


def _start(argv: list[str], env: dict[str, str], port: int, connect_host: str, log_path: Path) -> _Server:
    proc = subprocess.Popen(
        argv,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    server = _Server(proc, port, log_path, connect_host)
    deadline = time.time() + 8
    while time.time() < deadline:
        if proc.poll() is not None:
            server.stop()
            return server
        if server.accepted():
            return server
        time.sleep(0.05)
    server.stop()
    return server


def _assert_refused(server: _Server, evidence: str) -> None:
    """The process must exit non-zero before a client can complete a read.

    Reaching the listen socket, or dying inside the bind after the unauthenticated
    startup warning, is still the vulnerable path.
    """
    exited = server.proc.returncode is not None and not server.accepted()
    bind_error = "error while attempting to bind" in server.stderr or "could not bind" in server.stderr
    assert exited and not bind_error and server.proc.returncode != 0, (
        "wildcard HTTP server with no API key kept serving the mounted graph\n"
        f"returncode={server.proc.returncode}\n"
        f"stderr:\n{server.stderr}\n"
        f"client read:\n{evidence}"
    )
    log_text = server.log_path.read_text(encoding="utf-8") if server.log_path.exists() else ""
    assert log_text == "", f"query log was written before the server refused the client:\n{log_text}"


def _open_session(server: _Server, host_header: str, api_key: str) -> str:
    status, headers, body = _mcp_post(
        server.port,
        _initialize_body(),
        host_header,
        connect_host=server.connect_host,
        api_key=api_key,
    )
    assert status == 200, f"allowlisted initialize failed: {status} {body[:400]}"
    session = headers.get("mcp-session-id")
    assert session, f"stateful server did not return a session id: {headers}"
    note_status, _, note_body = _mcp_post(
        server.port,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        host_header,
        connect_host=server.connect_host,
        api_key=api_key,
        session=session,
    )
    assert note_status in {200, 202}, note_body[:400]
    return session


def _listed_names(server: _Server, session: str, host_header: str, api_key: str) -> tuple[set[str], set[str]]:
    status, _, body = _mcp_post(
        server.port,
        _rpc("tools/list", {}, 2),
        host_header,
        connect_host=server.connect_host,
        api_key=api_key,
        session=session,
    )
    assert status == 200, body[:400]
    tools = {tool["name"] for tool in json.loads(body)["result"]["tools"]}
    status, _, body = _mcp_post(
        server.port,
        _rpc("resources/list", {}, 3),
        host_header,
        connect_host=server.connect_host,
        api_key=api_key,
        session=session,
    )
    assert status == 200, body[:400]
    resources = {item["uri"] for item in json.loads(body)["result"]["resources"]}
    return tools, resources


def _sweep_unauthenticated(server: _Server, project_path: str) -> str:
    """Issue every graph read with no credentials. Return leaked text."""
    status, headers, body = _mcp_post(
        server.port,
        _initialize_body(),
        f"127.0.0.1:{server.port}",
        connect_host=server.connect_host,
    )
    leaked = [f"initialize {status} {body[:500]}"]
    if status != 200:
        return "\n".join(leaked)
    session = headers.get("mcp-session-id")
    _mcp_post(
        server.port,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        f"127.0.0.1:{server.port}",
        connect_host=server.connect_host,
        session=session,
    )
    for rid, (method, params, _snippet) in enumerate(_read_operations(project_path), start=4):
        status, _, body = _mcp_post(
            server.port,
            _rpc(method, params, rid),
            f"127.0.0.1:{server.port}",
            connect_host=server.connect_host,
            session=session,
        )
        leaked.append(f"{method} {params.get('name') or params.get('uri')} {status} {body[:500]}")
    return "\n".join(leaked)


@pytest.fixture()
def mounted(tmp_path: Path):
    graph_path, project = _graph_dir(tmp_path)
    log_path = tmp_path / "queries.log"
    yield graph_path, project, log_path


def test_image_default_command_does_not_serve_without_api_key(mounted, tmp_path):
    """Dockerfile ENTRYPOINT+CMD, with the key unset, empty, or whitespace."""
    graph_path, project, log_path = mounted
    for api_key in (None, "", "   ", "\n"):
        port = _free_port()
        argv = _image_argv(graph_path, port)
        env = _server_env(tmp_path, log_path, api_key)
        server = _start(argv, env, port, "127.0.0.1", log_path)
        try:
            evidence = ""
            if server.accepted():
                evidence = _sweep_unauthenticated(server, project)
            _assert_refused(server, evidence)
        finally:
            server.stop()
        if log_path.exists():
            log_path.unlink()


@pytest.mark.parametrize("host", ["0.0.0.0", "::", ""])
@pytest.mark.parametrize("key_args", [[], ["--api-key", ""]])
@pytest.mark.parametrize("extra", [[], ["--json-response"], ["--stateless"]])
def test_wildcard_bind_without_api_key_refuses_before_accept(mounted, tmp_path, host, key_args, extra):
    graph_path, project, log_path = mounted
    port = _free_port()
    connect_host = "::1" if host == "::" else "127.0.0.1"
    argv = [
        sys.executable,
        "-m",
        "graphify.serve",
        str(graph_path),
        "--transport",
        "http",
        "--host",
        host,
        "--port",
        str(port),
        *extra,
        *key_args,
    ]
    env = _server_env(tmp_path, log_path, None)
    env.pop("GRAPHIFY_API_KEY", None)
    server = _start(argv, env, port, connect_host, log_path)
    try:
        evidence = ""
        if server.accepted():
            evidence = _sweep_unauthenticated(server, project)
        _assert_refused(server, evidence)
    finally:
        server.stop()


def test_whitespace_env_key_is_not_a_credential(mounted, tmp_path):
    graph_path, _project, log_path = mounted
    port = _free_port()
    argv = [
        sys.executable,
        "-m",
        "graphify.serve",
        str(graph_path),
        "--transport",
        "http",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
    ]
    env = _server_env(tmp_path, log_path, "   ")
    server = _start(argv, env, port, "127.0.0.1", log_path)
    try:
        evidence = ""
        if server.accepted():
            status, _, body = _mcp_post(
                port,
                _initialize_body(),
                f"127.0.0.1:{port}",
            )
            evidence = f"{status} {body[:400]}"
        _assert_refused(server, evidence)
    finally:
        server.stop()


def _reject_unlisted(server: _Server, session: str, api_key: str, project_path: str) -> None:
    """A session opened on an allowlisted Host must still be checked per read."""
    cases = [
        (EVIL_HOST, None),
        (f"{EVIL_HOST}:{server.port}", None),
        (f"127.0.0.1:{server.port}", f"http://{EVIL_HOST}"),
        ("localhost", f"http://{EVIL_HOST}:{server.port}"),
    ]
    for host_header, origin in cases:
        for rid, (method, params, _snippet) in enumerate(_read_operations(project_path), start=20):
            status, _, body = _mcp_post(
                server.port,
                _rpc(method, params, rid),
                host_header,
                connect_host=server.connect_host,
                api_key=api_key,
                session=session,
                origin=origin,
            )
            assert status >= 400, (
                f"unlisted host/origin was served {method} "
                f"{params.get('name') or params.get('uri')} host={host_header!r} "
                f"origin={origin!r} status={status} body={body[:400]}"
            )
            for marker in MARKERS:
                assert marker not in body, (
                    f"{marker} leaked on host={host_header!r} origin={origin!r}: {body[:400]}"
                )
            log_text = server.log_path.read_text(encoding="utf-8") if server.log_path.exists() else ""
            assert PRIVATE_LABEL not in log_text, (
                "query log was written for a request whose Host or Origin is not allowlisted:\n"
                f"{log_text}"
            )


def _assert_allowlisted_reads(server: _Server, session: str, api_key: str, project_path: str) -> None:
    host_header = f"127.0.0.1:{server.port}"
    for rid, (method, params, snippet) in enumerate(_read_operations(project_path), start=80):
        if not snippet:
            continue
        status, _, body = _mcp_post(
            server.port,
            _rpc(method, params, rid),
            host_header,
            connect_host=server.connect_host,
            api_key=api_key,
            session=session,
        )
        assert status == 200, f"{method} {params} -> {status} {body[:400]}"
        assert snippet in body, f"{method} {params} did not return the mounted graph: {body[:400]}"
        if params.get("name") == "get_node" and params.get("arguments", {}).get("project_path"):
            assert PRIVATE_LABEL not in body, (
                "project_path read returned the default graph instead of the requested project: "
                f"{body[:400]}"
            )


@pytest.mark.parametrize("host", ["0.0.0.0", "::", ""])
@pytest.mark.parametrize("extra", [[], ["--json-response"], ["--stateless"]])
def test_unlisted_host_is_rejected_on_the_read_that_uses_the_session(mounted, tmp_path, host, extra):
    """DNS-rebinding protection stays on for wildcard binds once a key is configured.

    The session is opened with an allowlisted Host. The following read sends a
    different Host (or an unlisted Origin) and must not return the graph, write
    the query log, or swap in a different project_path result.
    """
    graph_path, project, log_path = mounted
    port = _free_port()
    connect_host = "::1" if host == "::" else "127.0.0.1"
    argv = [
        sys.executable,
        "-m",
        "graphify.serve",
        str(graph_path),
        "--transport",
        "http",
        "--host",
        host,
        "--port",
        str(port),
        "--api-key",
        PLACEHOLDER_API_KEY,
        *extra,
    ]
    env = _server_env(tmp_path, log_path, None)
    server = _start(argv, env, port, connect_host, log_path)
    try:
        assert server.accepted(), f"keyed wildcard server did not start:\n{server.stderr}"
        evil_status, _, evil_body = _mcp_post(
            port,
            _initialize_body(),
            EVIL_HOST,
            connect_host=connect_host,
            api_key=PLACEHOLDER_API_KEY,
        )
        assert evil_status >= 400, f"initialize accepted unlisted Host: {evil_status} {evil_body[:300]}"
        for marker in MARKERS:
            assert marker not in evil_body

        allow_host = "localhost" if host == "::" else f"127.0.0.1:{port}"
        if "--stateless" in extra:
            status, _, body = _mcp_post(
                port,
                _initialize_body(),
                allow_host,
                connect_host=connect_host,
                api_key=PLACEHOLDER_API_KEY,
            )
            assert status == 200, body[:400]
            session = None
        else:
            session = _open_session(server, allow_host, PLACEHOLDER_API_KEY)
            tools, resources = _listed_names(server, session, allow_host, PLACEHOLDER_API_KEY)
            assert tools == GRAPH_TOOLS
            assert resources == GRAPH_RESOURCES

        _reject_unlisted(server, session, PLACEHOLDER_API_KEY, project)
        if "--stateless" in extra:
            status, _, body = _mcp_post(
                port,
                _rpc(
                    "tools/call",
                    {"name": "get_node", "arguments": {"label": PRIVATE_LABEL}},
                    4,
                ),
                allow_host,
                connect_host=connect_host,
                api_key=PLACEHOLDER_API_KEY,
            )
            assert status == 200 and PRIVATE_SOURCE in body, body[:400]
        else:
            _assert_allowlisted_reads(server, session, PLACEHOLDER_API_KEY, project)
    finally:
        server.stop()


def test_image_command_with_api_key_rejects_unlisted_host_on_read(mounted, tmp_path):
    """Same default command as the image, once GRAPHIFY_API_KEY is set to a placeholder."""
    graph_path, project, log_path = mounted
    port = _free_port()
    argv = _image_argv(graph_path, port)
    env = _server_env(tmp_path, log_path, PLACEHOLDER_API_KEY)
    server = _start(argv, env, port, "127.0.0.1", log_path)
    try:
        assert server.accepted(), server.stderr
        missing, _, missing_body = _mcp_post(port, _initialize_body(), f"127.0.0.1:{port}")
        assert missing == 401, missing_body[:300]
        assert not any(marker in missing_body for marker in MARKERS)
        session = _open_session(server, f"127.0.0.1:{port}", PLACEHOLDER_API_KEY)
        _reject_unlisted(server, session, PLACEHOLDER_API_KEY, project)
        _assert_allowlisted_reads(server, session, PLACEHOLDER_API_KEY, project)
    finally:
        server.stop()
