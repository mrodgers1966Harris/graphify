"""Git hooks must not execute an interpreter path read from the work tree.

`graphify hook install` writes post-commit and post-checkout scripts that, when
the interpreter pinned at install time is missing or was skipped, read
`graphify-out/.graphify_python` and run that path. These tests install the real
hooks, commit a wrapper the way a repository can (the path is gitignored and
force-added), then commit or switch branches.

The wrapper exits 0 when its arguments contain the `find_spec` probe, and on
that same invocation replaces itself with a second script. A fix that runs the
probe and then looks the path up again still executes the replacement. The
tests pass only when the installed hook never executes the work-tree path, so
the invocation log is absent and the original wrapper is unchanged.

They fail while that execution is still present.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from graphify.hooks import install

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="The installed hook is a POSIX shell script",
)

_CANARY = "# graphify-hook-interpreter-canary"
_PATH_ALLOWLIST = re.compile(r"^[A-Za-z0-9/_.@:\\-]+$")

# The probe string is the one embedded in the hook. The replacement is a
# different program at the same path, so re-resolving the path between the
# probe and the launch still runs attacker-controlled code.
_WRAPPER = r"""#!/bin/sh
# graphify-hook-interpreter-canary
printf 'invoked\n' >> graphify-out/interpreter-invoked
case "$*" in
  *find_spec*)
    printf '%s\n' '#!/bin/sh' '# replaced-payload' "printf '%s\n' payload >> graphify-out/interpreter-invoked" 'exit 0' > graphify-out/pwn.next
    chmod 755 graphify-out/pwn.next
    mv -f graphify-out/pwn.next graphify-out/pwn
    exit 0
    ;;
esac
printf 'payload\n' >> graphify-out/interpreter-invoked
exit 0
"""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    (repo / ".gitignore").write_text("graphify-out/\n", encoding="utf-8")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md", ".gitignore")
    _git(repo, "commit", "-m", "init")
    return repo


def _install(repo: Path, monkeypatch: pytest.MonkeyPatch, pin: str) -> None:
    """Install hooks whose pinned interpreter is skipped or no longer exists."""
    if pin == "disallowed-character":
        # install() drops a pinned path that contains a space, leaving the
        # work-tree file as the next candidate.
        monkeypatch.setattr(sys, "executable", "/opt/Program Files/Python/python3")
    elif pin == "removed":
        monkeypatch.setattr(sys, "executable", "/opt/graphify-removed/bin/python3")
    else:
        raise AssertionError(pin)
    install(repo)
    note = "echo ran >> hook-ran\n"
    for name in ("post-commit", "post-checkout"):
        hook = repo / ".git" / "hooks" / name
        text = hook.read_text(encoding="utf-8")
        prefix = "#!/bin/sh\n"
        assert text.startswith(prefix), f"{name} was not installed as a shell hook"
        hook.write_text(prefix + note + text[len(prefix):], encoding="utf-8")


def _interpreter_path(repo: Path, kind: str) -> str:
    if kind == "relative":
        return "graphify-out/pwn"
    if kind == "dot-relative":
        return "./graphify-out/pwn"
    if kind == "parent-relative":
        return "graphify-out/../graphify-out/pwn"
    if kind == "absolute-inside-repo":
        path = str(repo / "graphify-out" / "pwn")
        assert _PATH_ALLOWLIST.fullmatch(path), path
        return path
    raise AssertionError(kind)


def _plant(repo: Path, kind: str) -> None:
    out = repo / "graphify-out"
    out.mkdir(exist_ok=True)
    wrapper = out / "pwn"
    wrapper.write_text(_WRAPPER, encoding="utf-8")
    wrapper.chmod(0o755)
    recorded = _interpreter_path(repo, kind)
    (out / ".graphify_python").write_text(recorded + "\n", encoding="utf-8")
    _git(repo, "add", "-f", "graphify-out/pwn", "graphify-out/.graphify_python")
    _git(repo, "commit", "-m", "record interpreter path")


def _assert_worktree_interpreter_not_executed(repo: Path, hook_result: subprocess.CompletedProcess[str]) -> None:
    ran = repo / "hook-ran"
    log = repo / "graphify-out" / "interpreter-invoked"
    wrapper = repo / "graphify-out" / "pwn"
    assert ran.is_file(), (
        "git did not run the installed hook\n"
        f"stdout:\n{hook_result.stdout}\nstderr:\n{hook_result.stderr}"
    )
    body = wrapper.read_text(encoding="utf-8") if wrapper.exists() else ""
    recorded = log.read_text(encoding="utf-8") if log.exists() else ""
    assert recorded == "" and _CANARY in body, (
        "the hook executed the interpreter path from graphify-out/.graphify_python\n"
        f"invocation log:\n{recorded}\n"
        f"wrapper:\n{body[:500]}\n"
        f"stderr:\n{hook_result.stderr}"
    )


@pytest.mark.parametrize("pin", ["disallowed-character", "removed"])
@pytest.mark.parametrize(
    "path_kind",
    ["relative", "dot-relative", "parent-relative", "absolute-inside-repo"],
)
def test_post_commit_does_not_execute_worktree_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pin: str, path_kind: str
) -> None:
    repo = _repo(tmp_path)
    _install(repo, monkeypatch, pin)
    _git(repo, "checkout", "-b", "attacker")
    _plant(repo, path_kind)
    marker = repo / "hook-ran"
    if marker.exists():
        marker.unlink()
    (repo / "README.md").write_text("developer change\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    result = _git(repo, "commit", "-m", "developer change")
    _assert_worktree_interpreter_not_executed(repo, result)


@pytest.mark.parametrize("pin", ["disallowed-character", "removed"])
@pytest.mark.parametrize(
    "path_kind",
    ["relative", "dot-relative", "parent-relative", "absolute-inside-repo"],
)
def test_post_checkout_does_not_execute_worktree_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pin: str, path_kind: str
) -> None:
    repo = _repo(tmp_path)
    _install(repo, monkeypatch, pin)
    _git(repo, "checkout", "-b", "attacker")
    _plant(repo, path_kind)
    _git(repo, "checkout", "master")
    marker = repo / "hook-ran"
    if marker.exists():
        marker.unlink()
    result = _git(repo, "checkout", "attacker")
    _assert_worktree_interpreter_not_executed(repo, result)
