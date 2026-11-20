"""Spec: repository shortcuts must not export attacker-chosen Drive file ids.

Google Workspace import reads ``doc_id`` / ``file_id`` / ``fileId`` / ``id`` /
``url`` / ``resource_id`` from a ``.gdoc``, ``.gsheet``, or ``.gslides`` file in
the scan tree and passes that id to ``gws drive files export``. ``gws`` (the
googleworkspace CLI, not a Python dependency) then downloads that Drive file
with the ambient logged-in Google account and graphify puts the bytes in
``graphify-out/converted`` and in the semantic-extraction prompt.

These tests are the required end state. They fail while that behavior remains.

Authorization is not the shortcut file. Two environment variables, both
comma-separated Drive file ids and both empty when unset, are the only
authorization:

* ``GRAPHIFY_GOOGLE_WORKSPACE_ALLOW_FILE_IDS`` — ids that may be passed to
  ``gws drive files export``. Any other id must not be exported. The id that is
  exported must be the id that was checked. Reading the shortcut path again
  between the check and the export and using the later id is still a failure.
* ``GRAPHIFY_GOOGLE_WORKSPACE_CORPUS_FILE_IDS`` — ids whose exported text may
  be added to the detect() corpus and sent to the semantic LLM. Exporting an
  allowlisted id must not put its bytes in the corpus or the LLM request
  unless the id is in this list as well.

An unlisted id must produce no ``gws`` invocation and no export file create,
write, or delete.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import graphify.google_workspace as gw
from graphify.detect import detect, detect_incremental

ALLOW_ENV = "GRAPHIFY_GOOGLE_WORKSPACE_ALLOW_FILE_IDS"
CORPUS_ENV = "GRAPHIFY_GOOGLE_WORKSPACE_CORPUS_FILE_IDS"
SENTINEL = "PRIVATE_DRIVE_EXPORT"

# Mirrors gws v0.22.5 argument handling enough to show which fileId graphify
# asked to export, then writes the export body to the relative -o path (the
# CLI writes the Drive response bytes there). It does not call Google.
_GWS_STUB = textwrap.dedent(
    """\
    import json, os, sys
    from pathlib import Path

    def main() -> int:
        log_path = os.environ["GWS_STUB_LOG"]
        args = sys.argv[1:]
        params = None
        output = None
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--params" and i + 1 < len(args):
                params = json.loads(args[i + 1])
                i += 2
                continue
            if arg in ("-o", "--output") and i + 1 < len(args):
                output = args[i + 1]
                i += 2
                continue
            i += 1
        file_id = "" if not isinstance(params, dict) else str(params.get("fileId") or "")
        mime_type = "" if not isinstance(params, dict) else str(params.get("mimeType") or "")
        record = {
            "argv": args,
            "fileId": file_id,
            "mimeType": mime_type,
            "output": output,
            "cwd": os.getcwd(),
        }
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\\n")
        if args[:3] != ["drive", "files", "export"] or not file_id or not output:
            sys.stderr.write("gws stub: not a drive files export with fileId\\n")
            return 2
        # fileId is the path parameter of files/{fileId}/export; mimeType is the
        # query. The body below stands in for that response.
        body = f"{os.environ['GWS_STUB_SENTINEL']}:{file_id}\\n"
        dest = Path(output)
        if not dest.is_absolute():
            dest = Path(os.getcwd()) / dest
        if mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            import openpyxl
            book = openpyxl.Workbook()
            book.active.title = "Sheet"
            book.active["A1"] = body.strip()
            book.save(dest)
        else:
            dest.write_text(body, encoding="utf-8")
        return 0

    if __name__ == "__main__":
        raise SystemExit(main())
    """
)


def _shortcut_cases() -> list[tuple[str, str, dict]]:
    """Every extension and every id field the parser accepts."""
    cases = []
    fields = (
        "doc_id",
        "file_id",
        "fileId",
        "id",
        "url_path",
        "url_query",
        "url_non_google_host",
        "resource_id",
    )
    for ext in (".gdoc", ".gsheet", ".gslides"):
        for field in fields:
            file_id = f"attacker-{ext[1:]}-{field}"
            if field == "url_path":
                payload = {"url": f"https://docs.google.com/document/d/{file_id}/edit"}
            elif field == "url_query":
                payload = {"url": f"https://drive.google.com/open?id={file_id}"}
            elif field == "url_non_google_host":
                payload = {"url": f"https://evil.example/document/d/{file_id}/edit"}
            elif field == "resource_id":
                payload = {"resource_id": f"document:{file_id}"}
            else:
                payload = {field: file_id}
            cases.append((ext, field, payload))
    return cases


def _install_gws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "gws-invocations.log"
    script = bin_dir / "gws"
    script.write_text(f"#!{sys.executable}\n" + _GWS_STUB, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GWS_STUB_LOG", str(log_path))
    monkeypatch.setenv("GWS_STUB_SENTINEL", SENTINEL)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    monkeypatch.delenv(CORPUS_ENV, raising=False)
    return log_path


def _invocations(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    rows = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_shortcut(root: Path, name: str, payload: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _SideEffects:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []


@pytest.fixture
def export_side_effects(monkeypatch: pytest.MonkeyPatch):
    """Record export-file create, write, and delete inside google workspace import."""
    observed = _SideEffects()
    orig_temp = gw.tempfile.NamedTemporaryFile
    orig_unlink = Path.unlink
    orig_write_text = Path.write_text
    orig_write_bytes = Path.write_bytes
    orig_mkdir = Path.mkdir

    def tracked_temp(*args, **kwargs):
        observed.events.append(("create", str(kwargs.get("dir") or kwargs.get("suffix") or "tempfile")))
        return orig_temp(*args, **kwargs)

    def tracked_unlink(self, *args, **kwargs):
        observed.events.append(("unlink", str(self)))
        return orig_unlink(self, *args, **kwargs)

    def tracked_write_text(self, data, *args, **kwargs):
        if "graphify-out" in Path(self).parts:
            observed.events.append(("write", str(self)))
        return orig_write_text(self, data, *args, **kwargs)

    def tracked_write_bytes(self, data, *args, **kwargs):
        if "graphify-out" in Path(self).parts:
            observed.events.append(("write", str(self)))
        return orig_write_bytes(self, data, *args, **kwargs)

    def tracked_mkdir(self, *args, **kwargs):
        if self.name == "converted" or "converted" in self.parts:
            observed.events.append(("mkdir", str(self)))
        return orig_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(gw.tempfile, "NamedTemporaryFile", tracked_temp)
    monkeypatch.setattr(Path, "unlink", tracked_unlink)
    monkeypatch.setattr(Path, "write_text", tracked_write_text)
    monkeypatch.setattr(Path, "write_bytes", tracked_write_bytes)
    monkeypatch.setattr(Path, "mkdir", tracked_mkdir)
    return observed


def _listed_corpus_text(result: dict) -> str:
    """Text of files detect() actually put in the corpus."""
    chunks = []
    for paths in result.get("files", {}).values():
        for raw in paths:
            path = Path(raw)
            if path.is_file():
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    for kind in ("new_files", "unchanged_files"):
        for paths in result.get(kind, {}).values():
            for raw in paths:
                path = Path(raw)
                if path.is_file():
                    chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _converted_text(root: Path) -> str:
    converted = root / "graphify-out" / "converted"
    if not converted.exists():
        return ""
    chunks = []
    for path in converted.rglob("*"):
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _corpus_text(root: Path, result: dict) -> str:
    return _listed_corpus_text(result) + "\n" + _converted_text(root)


def _assert_untrusted_not_exported(log_path: Path, root: Path, result: dict, side_effects: _SideEffects) -> None:
    invocations = _invocations(log_path)
    assert invocations == [], (
        "repository shortcuts exported Drive file ids without an allowlist entry: "
        f"{invocations}"
    )
    assert side_effects.events == [], (
        "export created or deleted a file before refusing the untrusted Drive id: "
        f"{side_effects.events}"
    )
    blob = _corpus_text(root, result)
    assert SENTINEL not in blob
    for file_id in {row["fileId"] for row in invocations}:
        assert file_id not in blob


def test_repository_shortcuts_are_not_exported_for_any_id_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, export_side_effects: _SideEffects
):
    log_path = _install_gws(tmp_path, monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    for ext, field, payload in _shortcut_cases():
        _write_shortcut(root, f"notes-{field}{ext}", payload)

    result = detect(root, google_workspace=True)

    _assert_untrusted_not_exported(log_path, root, result, export_side_effects)


def test_env_enable_does_not_export_repository_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, export_side_effects: _SideEffects
):
    log_path = _install_gws(tmp_path, monkeypatch)
    monkeypatch.setenv("GRAPHIFY_GOOGLE_WORKSPACE", "1")
    root = tmp_path / "repo"
    root.mkdir()
    _write_shortcut(root, "Notes.gdoc", {"doc_id": "attacker-from-env"})

    result = detect(root)

    _assert_untrusted_not_exported(log_path, root, result, export_side_effects)


def test_incremental_detect_does_not_export_repository_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, export_side_effects: _SideEffects
):
    log_path = _install_gws(tmp_path, monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    _write_shortcut(root, "Budget.gsheet", {"file_id": "attacker-incremental-sheet"})
    _write_shortcut(root, "Deck.gslides", {"id": "attacker-incremental-slides"})

    result = detect_incremental(
        root,
        str(tmp_path / "missing-manifest.json"),
        google_workspace=True,
    )

    _assert_untrusted_not_exported(log_path, root, result, export_side_effects)
    new_docs = " ".join(result.get("new_files", {}).get("document", []))
    assert SENTINEL not in new_docs
    assert "attacker-incremental" not in new_docs


@pytest.mark.parametrize("field", ["doc_id", "file_id", "fileId", "id"])
def test_explicit_id_is_not_exported_when_only_the_url_id_is_allowlisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
):
    """The URL id is the authorized object. A different id in the shortcut is not."""
    log_path = _install_gws(tmp_path, monkeypatch)
    allowed = "allowlisted-url-id"
    attacker = f"attacker-via-{field}"
    monkeypatch.setenv(ALLOW_ENV, allowed)
    root = tmp_path / "repo"
    root.mkdir()
    _write_shortcut(
        root,
        "Planning.gdoc",
        {
            "url": f"https://docs.google.com/document/d/{allowed}/edit",
            field: attacker,
        },
    )

    result = detect(root, google_workspace=True)

    exported = {row["fileId"] for row in _invocations(log_path)}
    assert attacker not in exported, exported
    assert attacker not in _converted_text(root)
    # Corpus opt-in is unset, so an allowlisted export still must not be sent on.
    listed = _listed_corpus_text(result)
    assert SENTINEL not in listed
    assert attacker not in listed


def test_reread_between_check_and_export_cannot_swap_file_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A second read of the shortcut must not supply the id that is exported.

    The first read returns the allowlisted id and then the on-disk shortcut
    becomes a different id. Exporting that later id fails this test. Putting
    either export into the corpus also fails, because the corpus allowlist is
    empty.
    """
    log_path = _install_gws(tmp_path, monkeypatch)
    allowed = "allowlisted-stable-id"
    attacker = "attacker-swapped-id"
    monkeypatch.setenv(ALLOW_ENV, allowed)
    root = tmp_path / "repo"
    root.mkdir()
    shortcut = _write_shortcut(
        root,
        "Planning.gdoc",
        {
            "doc_id": allowed,
            "url": f"https://docs.google.com/document/d/{allowed}/edit",
        },
    )
    allowed_text = shortcut.read_text(encoding="utf-8")
    attacker_text = json.dumps(
        {
            "doc_id": attacker,
            "url": f"https://docs.google.com/document/d/{attacker}/edit",
        }
    )
    state = {"reads": 0}
    original = Path.read_text

    def flipping_read_text(self, *args, **kwargs):
        if self.name == shortcut.name and self.parent == shortcut.parent:
            state["reads"] += 1
            if state["reads"] == 1:
                fd = os.open(shortcut, os.O_WRONLY | os.O_TRUNC)
                try:
                    os.write(fd, attacker_text.encode("utf-8"))
                finally:
                    os.close(fd)
                return allowed_text
            return attacker_text
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flipping_read_text)

    result = detect(root, google_workspace=True)

    exported = {row["fileId"] for row in _invocations(log_path)}
    assert attacker not in exported, exported
    assert exported <= {allowed}
    assert attacker not in _converted_text(root)
    listed = _listed_corpus_text(result)
    assert SENTINEL not in listed
    assert attacker not in listed


def _start_llm_server():
    bodies: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            bodies.append(self.rfile.read(length).decode("utf-8", errors="replace"))
            content = json.dumps(
                {
                    "nodes": [
                        {
                            "id": "note",
                            "label": "Note",
                            "file_type": "document",
                            "source_file": "README.md",
                            "source_location": None,
                        }
                    ],
                    "edges": [],
                    "hyperedges": [],
                }
            )
            payload = json.dumps(
                {
                    "id": "stub",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):  # noqa: ARG002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, bodies


def _run_extract(root: Path, monkeypatch: pytest.MonkeyPatch, extra_args: list[str]) -> list[str]:
    server, bodies = _start_llm_server()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    monkeypatch.setenv("OLLAMA_BASE_URL", base)
    monkeypatch.setenv("OLLAMA_MODEL", "stub-model")
    import graphify.llm as llm

    llm.BACKENDS["ollama"]["base_url"] = base
    llm.BACKENDS["ollama"]["default_model"] = "stub-model"
    import graphify.__main__ as mainmod

    monkeypatch.setattr(
        mainmod.sys,
        "argv",
        ["graphify", "extract", str(root), "--backend", "ollama", "--no-cluster", *extra_args],
    )
    try:
        try:
            mainmod.main()
        except SystemExit:
            pass
    finally:
        server.shutdown()
        server.server_close()
    return bodies


def test_extract_cli_does_not_send_untrusted_drive_export_to_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    log_path = _install_gws(tmp_path, monkeypatch)
    monkeypatch.setenv("GRAPHIFY_GOOGLE_WORKSPACE", "1")
    root = tmp_path / "repo"
    root.mkdir()
    _write_shortcut(root, "Notes.gdoc", {"doc_id": "attacker-cli-doc"})
    _write_shortcut(root, "Budget.gsheet", {"file_id": "attacker-cli-sheet"})
    _write_shortcut(root, "Deck.gslides", {"id": "attacker-cli-slides"})
    (root / "README.md").write_text("# Notes\nPublic project readme.\n", encoding="utf-8")

    bodies = _run_extract(root, monkeypatch, ["--google-workspace"])

    exported = {row["fileId"] for row in _invocations(log_path)}
    combined = "\n".join(bodies)
    assert exported == set() and SENTINEL not in combined, {
        "exported": sorted(exported),
        "llm_request_contains_export": SENTINEL in combined,
    }
    assert "attacker-cli-doc" not in combined
    assert "attacker-cli-sheet" not in combined
    assert "attacker-cli-slides" not in combined
    converted = root / "graphify-out" / "converted"
    leftover = []
    if converted.exists():
        leftover = [p.name for p in converted.rglob("*") if p.is_file()]
    assert leftover == []


def test_extract_cli_incremental_does_not_export_untrusted_drive_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    log_path = _install_gws(tmp_path, monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    _write_shortcut(root, "Notes.gdoc", {"doc_id": "attacker-incremental-cli"})
    (root / "README.md").write_text("# Notes\nPublic project readme.\n", encoding="utf-8")
    out = root / "graphify-out"
    out.mkdir()
    (out / "manifest.json").write_text("{}", encoding="utf-8")
    (out / "graph.json").write_text('{"nodes": [], "links": []}', encoding="utf-8")

    bodies = _run_extract(root, monkeypatch, ["--google-workspace"])

    exported = {row["fileId"] for row in _invocations(log_path)}
    assert "attacker-incremental-cli" not in exported
    assert exported == set(), exported
    assert SENTINEL not in "\n".join(bodies)
    assert "attacker-incremental-cli" not in "\n".join(bodies)
