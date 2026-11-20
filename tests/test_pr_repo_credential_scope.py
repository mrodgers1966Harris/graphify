"""Spec for MCP pull-request tools and the gh repository they query.

The tools list_prs, get_pr_impact, and triage_prs may spawn gh for a
repository only when the normalized owner/name is one of:

1. The origin remote of the git checkout that contains the served
   graph.json. Walk upward from the graph file's directory and stop at
   the first .git. Parse origin the way gh parses a remote URL
   (https://github.com/owner/name.git, git@github.com:owner/name.git,
   or owner/name) and compare owner and name case-insensitively.
2. An entry in the GRAPHIFY_GH_REPO_ALLOWLIST environment variable:
   comma-separated owner/name values, whitespace trimmed, compared
   case-insensitively after the same normalization.

A caller-supplied repo in any form gh accepts (owner/name,
host/owner/name, or a git/https URL) is that same repository or it is
not. Anything else must be ignored before gh is spawned. Ignoring it
may query only the served origin, or it may refuse. It must not leave
a gh process whose arguments or GH_REPO value name the rejected
repository, and the tool text must not contain that repository's pull
request title, author, review or CI state, or changed-file names.

Omitting repo queries the served origin and no other repository.

These tests fail while a caller-supplied name is passed through to gh.
The CLI (`graphify prs --repo`) is operator-supplied and is not covered.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("starlette")

from mcp import types  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from graphify import serve as serve_mod  # noqa: E402

SERVED = "served-owner/served-repo"
FOREIGN = "other-org/private-repo"
SUBSTRING = "xserved-owner/served-repo"  # contains SERVED as a substring
ALLOWED = "allowed-org/extra-repo"

# Markers that only exist in the gh stand-in's fixture for that repository.
FOREIGN_MARKERS = (
    "FOREIGN_PR_TITLE",
    "foreign-author",
    "secrets/credentials.py",
)
SUBSTRING_MARKERS = (
    "SUBSTRING_PR_TITLE",
    "substring-author",
    "internal/other.py",
)
SERVED_TITLE = "SERVED_PR_TITLE"
ALLOWED_TITLE = "ALLOWED_PR_TITLE"

# Placeholder only. The assertion is that this string is not a tool argument
# and is not placed on the gh command line; the stand-in records a boolean.
_GH_TOKEN_PLACEHOLDER = "placeholder-not-a-secret"

_GH_STANDIN = r"""#!/usr/bin/env python3
# Stand-in for the gh commands graphify invokes.
# Models cli/cli v2.103.0 and go-gh v2.17.0: --repo / GH_REPO / checkout origin
# selects the repository, and the fixture row is the JSON gh would print for
# a login that can read it. The token value is never written.
import json
import os
import sys
from urllib.parse import urlparse

LOG = os.environ["GH_FAKE_LOG"]
FIXTURE = os.environ["GH_FAKE_FIXTURE"]


def normalize(value):
    # OWNER/REPO, HOST/OWNER/REPO, or a git URL -> owner/name.
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("git@") and ":" in raw and "://" not in raw:
        host, path = raw.split(":", 1)
        raw = "ssh://" + host + "/" + path
    if "://" in raw or raw.startswith(("http:", "https:", "ssh:", "git:")):
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        if host == "" or len(parts) != 2:
            return None
        owner, name = parts
    else:
        parts = [p for p in raw.split("/") if p]
        if len(parts) == 2:
            owner, name = parts
        elif len(parts) == 3:
            owner, name = parts[1], parts[2]
        else:
            return None
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        return None
    return owner.lower() + "/" + name.lower()


def cwd_origin():
    import subprocess
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return normalize(result.stdout.strip())


def selected_repo(argv):
    # --repo / -R, else GH_REPO, else the current checkout's origin.
    for index, arg in enumerate(argv):
        if arg == "--repo" and index + 1 < len(argv):
            return normalize(argv[index + 1]), argv[index + 1]
        if arg.startswith("--repo="):
            return normalize(arg.split("=", 1)[1]), arg.split("=", 1)[1]
        if arg == "-R" and index + 1 < len(argv):
            return normalize(argv[index + 1]), argv[index + 1]
    override = os.environ.get("GH_REPO")
    if override:
        return normalize(override), override
    return cwd_origin(), None


def main():
    argv = sys.argv
    repo, raw = selected_repo(argv)
    record = {
        "argv": argv,
        "repo": repo,
        "raw_repo": raw,
        "gh_repo_env": os.environ.get("GH_REPO"),
        "ambient_gh_token": bool(os.environ.get("GH_TOKEN")),
        "ambient_github_token": bool(os.environ.get("GITHUB_TOKEN")),
    }
    line = (json.dumps(record) + "\n").encode()
    fd = os.open(LOG, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)

    fixture = json.loads(open(FIXTURE, encoding="utf-8").read())
    row = fixture.get(repo) if repo else None
    if len(argv) >= 3 and argv[1] == "repo" and argv[2] == "view":
        if row is None:
            sys.exit(1)
        json.dump({"defaultBranchRef": {"name": row["default_branch"]}}, sys.stdout)
        return
    if len(argv) >= 3 and argv[1] == "pr" and argv[2] == "list":
        json.dump(row["prs"] if row else [], sys.stdout)
        return
    if len(argv) >= 3 and argv[1] == "pr" and argv[2] == "view":
        if row is None:
            sys.exit(1)
        number = int(argv[3])
        match = next((pr for pr in row["prs"] if pr["number"] == number), None)
        if match is None:
            sys.exit(1)
        json.dump(match, sys.stdout)
        return
    if len(argv) >= 3 and argv[1] == "pr" and argv[2] == "diff":
        if row is None:
            sys.exit(1)
        sys.stdout.write("\n".join(row["files"]) + "\n")
        return
    sys.exit(1)


if __name__ == "__main__":
    main()
"""


def _pr(number: int, title: str, author: str) -> dict:
    return {
        "number": number,
        "title": title,
        "headRefName": "feature",
        "baseRefName": "main",
        "author": {"login": author},
        "isDraft": False,
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
        "updatedAt": "2026-11-18T12:00:00Z",
    }


def _fixture() -> dict:
    def row(title: str, author: str, filename: str) -> dict:
        return {
            "default_branch": "main",
            "prs": [_pr(7, title, author)],
            "files": [filename],
        }

    return {
        FOREIGN: row("FOREIGN_PR_TITLE", "foreign-author", "secrets/credentials.py"),
        SERVED: row("SERVED_PR_TITLE", "served-author", "graphify/serve.py"),
        ALLOWED: row("ALLOWED_PR_TITLE", "allowed-author", "docs/README.md"),
        SUBSTRING: row("SUBSTRING_PR_TITLE", "substring-author", "internal/other.py"),
    }


def _install_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "gh"
    script.write_text(_GH_STANDIN, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log_path = tmp_path / "gh.log"
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(_fixture()), encoding="utf-8")
    monkeypatch.setenv("GH_FAKE_LOG", str(log_path))
    monkeypatch.setenv("GH_FAKE_FIXTURE", str(fixture_path))
    monkeypatch.setenv("GH_TOKEN", _GH_TOKEN_PLACEHOLDER)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_ENTERPRISE_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ENTERPRISE_TOKEN", raising=False)
    monkeypatch.delenv("GH_REPO", raising=False)
    monkeypatch.delenv("GRAPHIFY_GH_REPO_ALLOWLIST", raising=False)
    # Prepend the stand-in. git stays reachable for the served-checkout setup.
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return log_path


def _served_graph(tmp_path: Path) -> str:
    """graph.json inside a checkout whose origin is SERVED."""
    project = tmp_path / "project"
    graph_dir = project / "graphify-out"
    graph_dir.mkdir(parents=True)
    graph = {
        "directed": True,
        "nodes": [
            {"id": "n", "label": "Serve", "community": 0, "source_file": "graphify/serve.py"},
        ],
        "edges": [],
    }
    graph_path = graph_dir / "graph.json"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/served-owner/served-repo.git"],
        cwd=project, check=True, capture_output=True,
    )
    return str(graph_path)


def _records(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    text = log_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _assert_gh_scoped(log_path: Path, allowed: set[str]) -> list[dict]:
    """Every gh invocation targeted an allowed repository, and did so before use.

    The stand-in logs at process start, so a check that happens after gh has
    already been spawned still fails. raw_repo, the normalized repo, argv, and
    GH_REPO are all the value gh would act on.
    """
    records = _records(log_path)
    for record in records:
        acted_on = record["repo"]
        assert acted_on in allowed, record
        if record["raw_repo"] is not None:
            # The raw argument gh received must normalize to the same object.
            assert record["repo"] in allowed
        assert record["gh_repo_env"] in (None, "") or record["repo"] in allowed
        blob = " ".join(record["argv"])
        assert _GH_TOKEN_PLACEHOLDER not in blob
        assert "--token" not in record["argv"]
        for marker in FOREIGN_MARKERS + SUBSTRING_MARKERS:
            assert marker not in blob
    return records


def _assert_no_markers(text: str, markers: tuple[str, ...]) -> None:
    for marker in markers:
        assert marker not in text


def _call(server, name: str, arguments: dict) -> str:
    request = types.CallToolRequest(
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    handler = server.request_handlers[types.CallToolRequest]
    result = asyncio.run(handler(request))
    content = result.root.content[0]
    return content.text


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    log_path = _install_gh(tmp_path, monkeypatch)
    graph = _served_graph(tmp_path)
    server = serve_mod._build_server(graph)
    return server, log_path, graph


def _clear_log(log_path: Path) -> None:
    if log_path.exists():
        log_path.unlink()


@pytest.mark.parametrize("tool,arguments", [
    ("list_prs", {"repo": FOREIGN}),
    ("list_prs", {"repo": f"https://github.com/{FOREIGN}.git"}),
    ("list_prs", {"repo": f"github.com/{FOREIGN}"}),
    ("list_prs", {"repo": SUBSTRING}),
    ("get_pr_impact", {"repo": FOREIGN, "pr_number": 7}),
    ("get_pr_impact", {"repo": f"https://github.com/{FOREIGN}", "pr_number": 7}),
    ("triage_prs", {"repo": FOREIGN}),
    ("triage_prs", {"repo": f"git@github.com:{FOREIGN}.git"}),
])
def test_caller_repo_is_not_passed_to_gh(scoped, tool, arguments):
    server, log_path, _graph = scoped
    text = _call(server, tool, arguments)
    _assert_no_markers(text, FOREIGN_MARKERS + SUBSTRING_MARKERS)
    _assert_gh_scoped(log_path, {SERVED})


def test_omitted_repo_queries_the_served_origin(scoped):
    server, log_path, _graph = scoped
    text = _call(server, "list_prs", {})
    assert SERVED_TITLE in text
    assert "served-author" in text
    records = _assert_gh_scoped(log_path, {SERVED})
    assert records, "omitted repo should query the served origin"
    assert all(record["repo"] == SERVED for record in records)


def test_explicit_served_repo_is_queried(scoped):
    server, log_path, _graph = scoped
    text = _call(server, "get_pr_impact", {"repo": SERVED, "pr_number": 7})
    assert SERVED_TITLE in text
    assert "served-author" in text
    assert "graphify/serve.py" in text
    _assert_no_markers(text, FOREIGN_MARKERS)
    records = _assert_gh_scoped(log_path, {SERVED})
    assert {record["repo"] for record in records} == {SERVED}


def test_case_variant_of_served_repo_is_the_same_repository(scoped):
    server, log_path, _graph = scoped
    text = _call(server, "list_prs", {"repo": "Served-Owner/Served-Repo"})
    assert SERVED_TITLE in text
    records = _assert_gh_scoped(log_path, {SERVED})
    assert {record["repo"] for record in records} == {SERVED}


def test_allowlisted_repo_is_queried_and_others_are_not(scoped, monkeypatch):
    server, log_path, _graph = scoped
    monkeypatch.setenv("GRAPHIFY_GH_REPO_ALLOWLIST", f" {ALLOWED} , {SERVED} ")
    text = _call(server, "list_prs", {"repo": f"https://github.com/{ALLOWED}.git"})
    assert ALLOWED_TITLE in text
    assert "allowed-author" in text
    _assert_no_markers(text, FOREIGN_MARKERS + SUBSTRING_MARKERS)
    records = _assert_gh_scoped(log_path, {ALLOWED, SERVED})
    assert {record["repo"] for record in records} == {ALLOWED}

    _clear_log(log_path)
    rejected = _call(server, "triage_prs", {"repo": FOREIGN})
    _assert_no_markers(rejected, FOREIGN_MARKERS)
    _assert_gh_scoped(log_path, {ALLOWED, SERVED})
    assert all(record["repo"] != FOREIGN for record in _records(log_path))


def test_get_pr_impact_does_not_return_unrelated_filenames(scoped):
    server, log_path, _graph = scoped
    text = _call(server, "get_pr_impact", {"repo": FOREIGN, "pr_number": 7})
    assert "secrets/credentials.py" not in text
    assert "FOREIGN_PR_TITLE" not in text
    _assert_gh_scoped(log_path, {SERVED})


# --- HTTP transport: a client that is not the gh login ----------------------

_INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _http_call(client, headers, name, arguments, rid) -> str:
    response = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert response.status_code == 200
    body = response.json()
    assert _GH_TOKEN_PLACEHOLDER not in json.dumps(body)
    return body["result"]["content"][0]["text"]


def _session(client, api_key: str | None) -> dict:
    headers = dict(_MCP_HEADERS)
    if api_key is not None:
        headers["X-API-Key"] = api_key
    init = client.post("/mcp", headers=headers, json=_INIT_BODY)
    assert init.status_code == 200, init.text
    headers["mcp-session-id"] = init.headers.get("mcp-session-id")
    client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    return headers


@pytest.mark.parametrize("api_key", [None, "graph-api-key-placeholder"])
@pytest.mark.parametrize("tool,arguments", [
    ("list_prs", {"repo": FOREIGN}),
    ("get_pr_impact", {"repo": FOREIGN, "pr_number": 7}),
    ("triage_prs", {"repo": f"https://github.com/{FOREIGN}.git"}),
])
def test_http_client_cannot_read_an_unrelated_repo(scoped, tmp_path, api_key, tool, arguments):
    _server, log_path, graph = scoped
    _clear_log(log_path)
    app = serve_mod._build_http_app(graph, api_key=api_key, json_response=True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = _session(client, api_key)
        text = _http_call(client, headers, tool, arguments, rid=2)
    _assert_no_markers(text, FOREIGN_MARKERS + SUBSTRING_MARKERS)
    _assert_gh_scoped(log_path, {SERVED})


def test_http_allowlisted_repo_still_answers(scoped, monkeypatch):
    _server, log_path, graph = scoped
    monkeypatch.setenv("GRAPHIFY_GH_REPO_ALLOWLIST", ALLOWED)
    _clear_log(log_path)
    app = serve_mod._build_http_app(graph, json_response=True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = _session(client, None)
        text = _http_call(client, headers, "get_pr_impact", {"repo": ALLOWED, "pr_number": 7}, rid=2)
    assert ALLOWED_TITLE in text
    assert "docs/README.md" in text
    records = _assert_gh_scoped(log_path, {ALLOWED, SERVED})
    assert {record["repo"] for record in records} == {ALLOWED}
