"""Optional Google Workspace shortcut export support.

Google Drive for desktop stores native Docs, Sheets, and Slides as small JSON
shortcut files (.gdoc, .gsheet, .gslides). Those files are pointers, not the
document content. This module exports them to Markdown sidecars via the
googleworkspace CLI (`gws`) so Graphify can extract their actual contents.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from typing import Callable, Any


GOOGLE_WORKSPACE_EXTENSIONS = {".gdoc", ".gsheet", ".gslides"}

# Shortcut files come from the scanned tree, so anyone who can commit one picks
# the Drive id. Export runs with the user's own gws login, so the user must name
# every id that may be exported, and separately every id whose text may enter
# the extraction corpus (and from there the configured LLM).
ALLOW_FILE_IDS_ENV = "GRAPHIFY_GOOGLE_WORKSPACE_ALLOW_FILE_IDS"
CORPUS_FILE_IDS_ENV = "GRAPHIFY_GOOGLE_WORKSPACE_CORPUS_FILE_IDS"

_GOOGLE_SHORTCUT_HOSTS = frozenset({"docs.google.com", "drive.google.com"})
_DRIVE_FILE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")


class GoogleWorkspaceExportNotAllowed(RuntimeError):
    """Raised when a shortcut names a Drive file the user has not allowlisted."""


def google_workspace_enabled(value: str | None = None) -> bool:
    """Return True when Google Workspace shortcut export is enabled."""
    raw = value if value is not None else os.environ.get("GRAPHIFY_GOOGLE_WORKSPACE", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_file_ids(name: str) -> frozenset[str]:
    raw = os.environ.get(name, "")
    return frozenset(part for part in re.split(r"[\s,]+", raw) if part)


def allowed_export_file_ids() -> frozenset[str]:
    """Drive file ids the user allows graphify to export with their gws login."""
    return _env_file_ids(ALLOW_FILE_IDS_ENV)


def allowed_corpus_file_ids() -> frozenset[str]:
    """Drive file ids whose exported text the user allows into the corpus."""
    return _env_file_ids(CORPUS_FILE_IDS_ENV)


def _safe_yaml_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _parse_google_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _GOOGLE_SHORTCUT_HOSTS:
        raise RuntimeError(f"shortcut URL is not a Google Docs/Drive URL: {url!r}")
    return parsed


def _file_ids_from_url(url: str) -> list[str]:
    """Return every Drive file ID named by a Google Docs/Drive URL."""
    parsed = _parse_google_url(url)
    ids = list(urllib.parse.parse_qs(parsed.query).get("id", []))
    match = re.search(r"/(?:document|spreadsheets|presentation|file)/d/([^/?#]+)", parsed.path)
    if match:
        ids.append(match.group(1))
    return ids


def _extract_file_id_from_url(url: str) -> str | None:
    """Extract a Drive file ID from common Google Docs/Drive URL shapes."""
    if not url:
        return None
    try:
        ids = _file_ids_from_url(url)
    except RuntimeError:
        return None
    return ids[0] if ids else None


def _extract_resource_key(url: str, data: dict[str, Any]) -> str | None:
    for key in ("resource_key", "resourceKey"):
        value = data.get(key)
        if value:
            return str(value)
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("resourcekey"):
        return query["resourcekey"][0]
    return None


def read_google_shortcut(path: Path) -> dict[str, str | None]:
    """Read a .gdoc/.gsheet/.gslides shortcut and return export metadata."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"could not read Google Workspace shortcut {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Google Workspace shortcut {path} is not a JSON object")

    # Every field that can name a file must name the same one; otherwise the id
    # the user checks and the id that gets exported could differ.
    candidates: set[str] = set()
    for key in ("doc_id", "file_id", "fileId", "id"):
        value = data.get(key)
        if value:
            candidates.add(str(value))
    url = str(data.get("url") or "")
    if url:
        try:
            candidates.update(_file_ids_from_url(url))
        except RuntimeError as exc:
            raise RuntimeError(f"Google Workspace shortcut {path}: {exc}") from exc
    resource_id = str(data.get("resource_id") or "")
    if ":" in resource_id:
        candidates.add(resource_id.split(":", 1)[1])

    if not candidates:
        raise RuntimeError(f"Google Workspace shortcut {path} does not include a Drive file ID")
    if len(candidates) > 1:
        raise RuntimeError(
            f"Google Workspace shortcut {path} names more than one Drive file ID: "
            + ", ".join(sorted(repr(c) for c in candidates))
        )
    file_id = next(iter(candidates))
    if not _DRIVE_FILE_ID_RE.fullmatch(file_id):
        raise RuntimeError(f"Google Workspace shortcut {path} has an invalid Drive file ID: {file_id!r}")

    return {
        "file_id": file_id,
        "url": url or None,
        "resource_key": _extract_resource_key(url, data),
        "account": str(data.get("email")) if data.get("email") else None,
    }


def _run_gws_export(file_id: str, mime_type: str, output: Path, resource_key: str | None = None) -> None:
    exe = shutil.which("gws")
    if not exe:
        raise RuntimeError(
            "gws is required for Google Workspace export. Install it from "
            "https://github.com/googleworkspace/cli and run `gws auth login -s drive`."
        )

    params: dict[str, str] = {"fileId": file_id, "mimeType": mime_type}
    # Drive resource keys are sent via X-Goog-Drive-Resource-Keys. The current
    # gws export command has no custom-header flag, so do not pass resourceKey
    # as an unsupported query parameter.
    _ = resource_key
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    timeout = int(os.environ.get("GRAPHIFY_GOOGLE_WORKSPACE_TIMEOUT", "120"))
    result = subprocess.run(
        [exe, "drive", "files", "export", "--params", json.dumps(params), "-o", output.name],
        capture_output=True,
        cwd=output.parent,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        if len(stderr) > 1200:
            stderr = stderr[:1200] + "..."
        raise RuntimeError(f"gws export failed for {file_id}: {stderr}")


def _sidecar_path(path: Path, out_dir: Path) -> Path:
    name_hash = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:8]
    return out_dir / f"{path.stem}_{name_hash}.md"


def _with_frontmatter(path: Path, shortcut: dict[str, str | None], body: str, exported_mime_type: str) -> str:
    source_url = shortcut.get("url") or ""
    account = shortcut.get("account") or ""
    account_line = ""
    if account:
        account_hash = hashlib.sha256(account.encode()).hexdigest()[:12]
        account_line = f'google_account_hash: "{account_hash}"\n'
    return (
        "---\n"
        f'source_file: "{_safe_yaml_str(str(path))}"\n'
        'source_type: "google_workspace"\n'
        f'google_file_id: "{_safe_yaml_str(shortcut["file_id"] or "")}"\n'
        f'google_export_mime_type: "{_safe_yaml_str(exported_mime_type)}"\n'
        f'source_url: "{_safe_yaml_str(source_url)}"\n'
        f"{account_line}"
        "---\n\n"
        f"<!-- converted from Google Workspace shortcut: {path.name} -->\n\n"
        f"{body.strip()}\n"
    )


def authorize_google_workspace_export(
    shortcut: dict[str, str | None],
    allowed_file_ids: frozenset[str] | set[str] | None = None,
) -> str:
    """Return the shortcut's Drive file id if the user allowlisted it for export."""
    allowed = allowed_export_file_ids() if allowed_file_ids is None else allowed_file_ids
    file_id = shortcut.get("file_id") or ""
    if not file_id or file_id not in allowed:
        raise GoogleWorkspaceExportNotAllowed(
            f"Drive file id {file_id!r} is not in {ALLOW_FILE_IDS_ENV}"
        )
    return file_id


def google_workspace_corpus_allowed(
    shortcut: dict[str, str | None],
    corpus_file_ids: frozenset[str] | set[str] | None = None,
) -> bool:
    """Return True if the user allowed this shortcut's export into the corpus."""
    allowed = allowed_corpus_file_ids() if corpus_file_ids is None else corpus_file_ids
    file_id = shortcut.get("file_id") or ""
    return bool(file_id) and file_id in allowed


def convert_google_workspace_file(
    path: Path,
    out_dir: Path,
    *,
    xlsx_to_markdown: Callable[[Path], str] | None = None,
    shortcut: dict[str, str | None] | None = None,
    allowed_file_ids: frozenset[str] | set[str] | None = None,
) -> Path | None:
    """Export a Google Workspace shortcut to a Markdown sidecar.

    ``shortcut`` is the parsed shortcut to act on; when omitted the file is read
    once here. The export uses that record's file id, and only if the id is in
    ``allowed_file_ids`` (default: ``GRAPHIFY_GOOGLE_WORKSPACE_ALLOW_FILE_IDS``).

    Returns the converted Markdown path, or None when conversion is unsupported
    or produced no readable content. Raises GoogleWorkspaceExportNotAllowed
    without touching the filesystem or running gws when the id is not allowed.
    """
    ext = path.suffix.lower()
    if ext not in GOOGLE_WORKSPACE_EXTENSIONS:
        return None

    if shortcut is None:
        shortcut = read_google_shortcut(path)
    file_id = authorize_google_workspace_export(shortcut, allowed_file_ids)
    shortcut = {**shortcut, "file_id": file_id}
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _sidecar_path(path, out_dir)

    if ext == ".gdoc":
        with tempfile.NamedTemporaryFile("w+b", suffix=".md", delete=False, dir=out_dir) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _run_gws_export(file_id, "text/markdown", tmp_path, shortcut.get("resource_key"))
            body = tmp_path.read_text(encoding="utf-8", errors="replace")
        finally:
            tmp_path.unlink(missing_ok=True)
        if not body.strip():
            return None
        out_path.write_text(_with_frontmatter(path, shortcut, body, "text/markdown"), encoding="utf-8")
        return out_path

    if ext == ".gslides":
        with tempfile.NamedTemporaryFile("w+b", suffix=".txt", delete=False, dir=out_dir) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _run_gws_export(file_id, "text/plain", tmp_path, shortcut.get("resource_key"))
            body = tmp_path.read_text(encoding="utf-8", errors="replace")
        finally:
            tmp_path.unlink(missing_ok=True)
        if not body.strip():
            return None
        out_path.write_text(_with_frontmatter(path, shortcut, body, "text/plain"), encoding="utf-8")
        return out_path

    if ext == ".gsheet":
        if xlsx_to_markdown is None:
            raise RuntimeError("Google Sheets export requires the office extra: pip install graphifyy[office,google]")
        with tempfile.NamedTemporaryFile("w+b", suffix=".xlsx", delete=False, dir=out_dir) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _run_gws_export(
                file_id,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                tmp_path,
                shortcut.get("resource_key"),
            )
            body = xlsx_to_markdown(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        if not body.strip():
            return None
        out_path.write_text(
            _with_frontmatter(
                path,
                shortcut,
                body,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            encoding="utf-8",
        )
        return out_path

    return None
