"""Write graph outputs without following a symlink planted at the destination.

A repository can commit ``graphify-out/<name>`` (or ``graphify-out`` itself, or
a subdirectory of it) as a symlink, and git checks it out as one even when the
directory is gitignored. Opening such a path for writing follows the link and
truncates whatever it points at.

Every writer here binds the destination directory once, walking from the
directory that contains the output root with ``O_NOFOLLOW`` on each component
(a symlink where a directory belongs is unlinked and replaced by a real
directory), then acts only through that directory descriptor: content goes to
a fresh ``O_CREAT|O_EXCL|O_NOFOLLOW`` temp file which is renamed over the final
name. ``rename`` replaces a symlink at the destination instead of following it.

Paths outside the output root (e.g. ``--out ~/Desktop/graph.html``) are the
user's choice: their parent directory is opened as given, and only the final
name is protected.

Platforms without ``dir_fd`` support (Windows) apply the same rules by path:
linked directories under the output root are replaced, and content goes to a
``mkstemp`` file that ``os.replace`` moves over the final name.
"""
from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
import tempfile
from pathlib import Path, PurePath
from typing import IO

from graphify import paths as _paths

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_HAVE_DIR_FD = bool(
    _O_NOFOLLOW
    and _O_DIRECTORY
    and os.open in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
)
_MAX_LINK_RETRIES = 8


def _split_at_out_root(dir_abs: str) -> tuple[str, list[str]]:
    """Split an absolute directory into (anchor, components to bind no-follow).

    The anchor is the directory that contains the output root; it is opened
    following symlinks. Everything from the output root down is bound
    component by component without following links. An absolute
    ``GRAPHIFY_OUT`` is the user's own configuration, so it is the anchor
    itself. A directory outside the output root is its own anchor.
    """
    out = _paths.GRAPHIFY_OUT
    if os.path.isabs(out):
        root = os.path.abspath(out)
        try:
            rel = os.path.relpath(dir_abs, root)
        except ValueError:
            return dir_abs, []
        if rel == os.curdir:
            return root, []
        if rel == os.pardir or rel.startswith(os.pardir + os.sep):
            return dir_abs, []
        return root, list(PurePath(rel).parts)
    out_parts = PurePath(os.path.normpath(out)).parts
    parts = PurePath(dir_abs).parts
    n = len(out_parts)
    for i in range(len(parts) - n, 0, -1):
        if tuple(parts[i:i + n]) == out_parts:
            return str(PurePath(*parts[:i])), list(parts[i:])
    return dir_abs, []


def _is_symlink_at(dir_fd: int, name: str) -> bool:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(st.st_mode)


def _open_child_dir(dir_fd: int, name: str) -> int:
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    for _ in range(_MAX_LINK_RETRIES):
        try:
            return os.open(name, flags, dir_fd=dir_fd)
        except FileNotFoundError:
            with contextlib.suppress(FileExistsError):
                os.mkdir(name, 0o777, dir_fd=dir_fd)
        except OSError:
            if not _is_symlink_at(dir_fd, name):
                raise
            os.unlink(name, dir_fd=dir_fd)
    raise OSError(errno.ELOOP, "output directory keeps being replaced by a symlink", name)


def _open_dir(directory: str | os.PathLike[str]) -> int:
    """Return a descriptor for ``directory``, bound as described in the module doc."""
    anchor, components = _split_at_out_root(os.path.abspath(os.fspath(directory)))
    os.makedirs(anchor, exist_ok=True)
    fd = os.open(anchor, os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC)
    try:
        for component in components:
            child = _open_child_dir(fd, component)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def _is_link(path: str) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return os.path.islink(path) or bool(isjunction and isjunction(path))


def _makedirs_by_path(directory: str | os.PathLike[str]) -> None:
    """Fallback for platforms without ``dir_fd`` (Windows): same layout rules, by path."""
    anchor, components = _split_at_out_root(os.path.abspath(os.fspath(directory)))
    os.makedirs(anchor, exist_ok=True)
    current = anchor
    for component in components:
        current = os.path.join(current, component)
        if _is_link(current):
            try:
                os.unlink(current)
            except (IsADirectoryError, PermissionError):
                os.rmdir(current)
        with contextlib.suppress(FileExistsError):
            os.mkdir(current)


def ensure_dir(directory: str | os.PathLike[str]) -> None:
    """Create ``directory`` (like ``mkdir(parents=True, exist_ok=True)``).

    Inside the output root, a symlink standing in for any directory is
    replaced by a real directory rather than followed.
    """
    if not _HAVE_DIR_FD:
        _makedirs_by_path(directory)
        return
    os.close(_open_dir(directory))


def _bind_parent(path: str | os.PathLike[str]) -> tuple[int, str]:
    abs_path = os.path.abspath(os.fspath(path))
    name = os.path.basename(abs_path)
    if not name or name in (os.curdir, os.pardir):
        raise IsADirectoryError(errno.EISDIR, "not a file path", os.fspath(path))
    return _open_dir(os.path.dirname(abs_path)), name


def _write_via_rename(path: str | os.PathLike[str], write) -> None:
    if not _HAVE_DIR_FD:
        parent = os.path.dirname(os.path.abspath(os.fspath(path)))
        _makedirs_by_path(parent)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".graphify-", suffix=".tmp")
        try:
            write(fd)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return
    dir_fd, name = _bind_parent(path)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
        while True:
            tmp = f".{name}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(tmp, flags, 0o666, dir_fd=dir_fd)
                break
            except FileExistsError:
                continue
        try:
            write(fd)
            os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dir_fd)
            raise
    finally:
        os.close(dir_fd)


def write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Replace ``path`` with ``data`` without writing through a symlink."""
    def _write(fd: int) -> None:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    _write_via_rename(path, _write)


def write_text(
    path: str | os.PathLike[str],
    text: str,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> None:
    """Replace ``path`` with ``text`` without writing through a symlink.

    Arguments match :meth:`pathlib.Path.write_text`.
    """
    def _write(fd: int) -> None:
        with os.fdopen(fd, "w", encoding=encoding, errors=errors, newline=newline) as fh:
            fh.write(text)
    _write_via_rename(path, _write)


def _open_nofollow(path: str | os.PathLike[str], flags: int) -> int:
    if not _HAVE_DIR_FD:
        _makedirs_by_path(os.path.dirname(os.path.abspath(os.fspath(path))))
        if _is_link(os.fspath(path)):
            os.unlink(path)
        return os.open(path, flags | _O_CLOEXEC, 0o666)
    dir_fd, name = _bind_parent(path)
    try:
        for _ in range(_MAX_LINK_RETRIES):
            try:
                return os.open(name, flags | _O_NOFOLLOW | _O_CLOEXEC, 0o666, dir_fd=dir_fd)
            except OSError:
                if not _is_symlink_at(dir_fd, name):
                    raise
                os.unlink(name, dir_fd=dir_fd)
        raise OSError(errno.ELOOP, "output file keeps being replaced by a symlink", os.fspath(path))
    finally:
        os.close(dir_fd)


def open_append(path: str | os.PathLike[str], encoding: str = "utf-8", **kwargs) -> IO[str]:
    """``open(path, "a")`` that replaces a symlink at ``path`` instead of following it."""
    fd = _open_nofollow(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    return os.fdopen(fd, "a", encoding=encoding, **kwargs)


def open_append_read(path: str | os.PathLike[str], encoding: str = "utf-8") -> IO[str]:
    """``open(path, "a+")`` that replaces a symlink at ``path`` instead of following it."""
    fd = _open_nofollow(path, os.O_RDWR | os.O_APPEND | os.O_CREAT)
    return os.fdopen(fd, "a+", encoding=encoding)


class OutputPath(type(Path())):  # type: ignore[misc]
    """A ``pathlib.Path`` whose ``write_text``/``write_bytes`` use the writers above.

    The installed skill imports it as ``Path`` so its generated snippets keep
    the familiar ``Path(...).write_text(...)`` form.
    """

    def write_text(self, data, encoding=None, errors=None, newline=None):  # type: ignore[override]
        if not isinstance(data, str):
            raise TypeError(f"data must be str, not {type(data).__name__}")
        write_text(self, data, encoding=encoding, errors=errors, newline=newline)
        return len(data)

    def write_bytes(self, data):  # type: ignore[override]
        view = memoryview(data)
        write_bytes(self, bytes(view))
        return view.nbytes
