"""Out-of-repo ``graphify-out/.graphify_root`` must not become the scan root.

``graphify update`` with no path, and the post-checkout and post-commit hooks,
read that marker and extract whatever directory it names. ``graphify-out/`` is
gitignored, but a tracked copy committed with ``git add -f`` is still checked
out. These tests fail while that marker is followed: the foreign tree is
walked and files are written under its ``graphify-out/``. They pass once a
marker that resolves outside the repository is ignored and the scan stays on
the repository.

A marker that resolves inside the repository, and a path passed on the command
line for that run, remain the scan root. The symlink case starts inside the
repository and is retargeted outside on first access, so a containment check
that looks the path up again before the walk still fails.
"""
from __future__ import annotations

import hashlib
import os
import site
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from graphify.hooks import install

REPO_ROOT = Path(__file__).resolve().parents[1]
INSIDE_SYMBOL = "inside_marker_symbol"
OUTSIDE_SYMBOL = "outside_marker_symbol"
NESTED_SYMBOL = "nested_marker_symbol"
ROOT_ONLY_SYMBOL = "root_only_marker_symbol"
CANARY_NAME = "CANARY.txt"
CANARY_TEXT = "canary-do-not-touch\n"

# Interposes readlink. The first resolution of the marker symlink still returns
# the in-repo target; the symlink is then retargeted so a later lookup — the
# walk or the mkdir of graphify-out — follows the foreign tree. Python's
# os.stat uses a direct system call, which this cannot see; os.path.realpath
# (Path.resolve) calls readlink through libc, which is the lookup the product
# repeats between a containment check and the scan.
_READLINK_PRELOAD = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int matches(const char *path, const char *link_abs) {
    const char *base;
    size_t n, m;
    if (path == NULL || link_abs == NULL || path[0] == '\0')
        return 0;
    if (strcmp(path, link_abs) == 0)
        return 1;
    base = strrchr(link_abs, '/');
    base = base ? base + 1 : link_abs;
    if (strcmp(path, base) == 0)
        return 1;
    n = strlen(path);
    m = strlen(base);
    if (n > m && path[n - m - 1] == '/' && strcmp(path + (n - m), base) == 0)
        return 1;
    return 0;
}

static void retarget(const char *path) {
    const char *link_abs = getenv("GRAPHIFY_TEST_LINK");
    const char *outside = getenv("GRAPHIFY_TEST_OUTSIDE");
    static int done;
    if (done || !matches(path, link_abs) || outside == NULL)
        return;
    done = 1;
    unlink(link_abs);
    symlink(outside, link_abs);
}

ssize_t readlink(const char *path, char *buf, size_t sz) {
    static ssize_t (*real_readlink)(const char *, char *, size_t) = NULL;
    ssize_t n;
    if (real_readlink == NULL)
        real_readlink = dlsym(RTLD_NEXT, "readlink");
    n = real_readlink(path, buf, sz);
    if (n >= 0)
        retarget(path);
    return n;
}
"""


def _env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Captured before HOME is redirected. The hook log lives under HOME, and a
    # redirected HOME would also hide the user site that holds runtime deps.
    user_site = site.getusersitepackages()
    previous = env.get("PYTHONPATH", "")
    parts = [str(REPO_ROOT)]
    if user_site:
        parts.append(user_site)
    if previous:
        parts.append(previous)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["HOME"] = str(home)
    env["GRAPHIFY_NO_TIPS"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env.pop("GRAPHIFY_OUT", None)
    env.pop("GRAPHIFY_CHANGED", None)
    return env


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _init_repo(path: Path, env: dict[str, str]) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True, text=True)
    _git(path, "config", "user.email", "graphify-test@example.com", env=env)
    _git(path, "config", "user.name", "graphify test", env=env)


def _write_inside(repo: Path) -> None:
    pkg = repo / "pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "inside.py").write_text(
        f"def {INSIDE_SYMBOL}():\n    return 'inside'\n",
        encoding="utf-8",
    )


def _write_outside(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    # The filename must not look like a credential store: detect() skips those
    # before extraction, which would hide whether the foreign tree was walked.
    (path / "layout.py").write_text(
        f"def {OUTSIDE_SYMBOL}():\n    return 'outside'\n",
        encoding="utf-8",
    )
    (path / CANARY_NAME).write_text(CANARY_TEXT, encoding="utf-8")


def _snapshot(path: Path) -> dict[str, str]:
    """Capture every entry under ``path`` so a created or deleted file fails."""
    rows: dict[str, str] = {}
    if not path.exists():
        return rows
    for entry in sorted(path.rglob("*")):
        rel = entry.relative_to(path).as_posix()
        if entry.is_symlink():
            rows[rel] = "symlink:" + os.readlink(entry)
        elif entry.is_dir():
            rows[rel] = "dir"
        else:
            digest = hashlib.sha256(entry.read_bytes()).hexdigest()
            rows[rel] = "file:" + digest
    return rows


def _commit_marker(origin: Path, marker_text: str, env: dict[str, str], extra: list[Path] | None = None) -> None:
    """Commit ``marker_text`` as a tracked ``graphify-out/.graphify_root``.

    The directory is gitignored. ``git add -f`` is how a committer puts the
    marker on a branch the developer will check out.
    """
    gitignore = origin / ".gitignore"
    gitignore.write_text("graphify-out/\n", encoding="utf-8")
    _write_inside(origin)
    _git(origin, "add", ".gitignore", "pkg/inside.py", env=env)
    for path in extra or []:
        _git(origin, "add", str(path.relative_to(origin)), env=env)
    _git(origin, "commit", "-m", "initial tree", env=env)
    marker = origin / "graphify-out" / ".graphify_root"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(marker_text, encoding="utf-8")
    _git(origin, "add", "-f", "graphify-out/.graphify_root", env=env)
    _git(origin, "commit", "-m", "record scan root", env=env)
    tracked = _git(origin, "ls-files", "graphify-out/.graphify_root", env=env).stdout.strip()
    assert tracked == "graphify-out/.graphify_root"


def _clone(origin: Path, victim: Path, env: dict[str, str]) -> None:
    result = subprocess.run(
        ["git", "clone", str(origin), str(victim)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(f"git clone failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    marker = victim / "graphify-out" / ".graphify_root"
    assert marker.is_file(), "committed marker was not checked out"
    _git(victim, "config", "user.email", "graphify-test@example.com", env=env)
    _git(victim, "config", "user.name", "graphify test", env=env)


def _run_update(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "graphify", "update", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _assert_update_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, (
        f"graphify update exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _assert_outside_unchanged(outside: Path, before: dict[str, str]) -> None:
    canary = outside / CANARY_NAME
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT
    after = _snapshot(outside)
    if after == before:
        return
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(key for key in set(after) & set(before) if after[key] != before[key])
    raise AssertionError(
        "scan wrote or deleted files outside the repository\n"
        f"added={added}\nremoved={removed}\nchanged={changed}"
    )


def _assert_repo_graph_is_local(repo: Path) -> None:
    graph = repo / "graphify-out" / "graph.json"
    assert graph.is_file(), "expected the graph to be written inside the repository"
    text = graph.read_text(encoding="utf-8")
    assert INSIDE_SYMBOL in text, "repository source was not extracted"
    assert OUTSIDE_SYMBOL not in text, "foreign symbol names were copied into the graph"
    report = repo / "graphify-out" / "GRAPH_REPORT.md"
    if report.exists():
        report_text = report.read_text(encoding="utf-8")
        assert OUTSIDE_SYMBOL not in report_text


def _prepare(tmp_path: Path, marker_text: str, *, extra_setup=None) -> tuple[Path, Path, dict[str, str], dict[str, str]]:
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    outside = tmp_path / "work" / "proj-secret"
    _write_outside(outside)
    origin = tmp_path / "origin"
    _init_repo(origin, env)
    extra: list[Path] = []
    if extra_setup is not None:
        extra = list(extra_setup(origin, outside) or [])
    _commit_marker(origin, marker_text, env, extra=extra)
    victim = tmp_path / "work" / "proj"
    _clone(origin, victim, env)
    # Sibling of the victim whose name shares the victim path as a string prefix.
    assert str(outside).startswith(str(victim))
    before = _snapshot(outside)
    return victim, outside, env, before


def test_committed_absolute_marker_is_checked_out_despite_gitignore(tmp_path: Path) -> None:
    """The attacker can deliver the marker: gitignore does not drop a tracked file."""
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    outside = tmp_path / "private-project"
    _write_outside(outside)
    origin = tmp_path / "origin"
    _init_repo(origin, env)
    _commit_marker(origin, str(outside) + "\n", env)
    victim = tmp_path / "victim"
    _clone(origin, victim, env)
    text = (victim / "graphify-out" / ".graphify_root").read_text(encoding="utf-8").strip()
    assert text == str(outside)
    ignored = subprocess.run(
        ["git", "-C", str(victim), "check-ignore", "--no-index", "graphify-out/.graphify_root"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert ignored.returncode == 0, ignored.stderr
    assert "graphify-out" in ignored.stdout


def test_update_without_path_does_not_follow_absolute_marker(tmp_path: Path) -> None:
    victim, outside, env, before = _prepare(tmp_path, str(tmp_path / "work" / "proj-secret") + "\n")
    result = _run_update(victim, env)
    _assert_update_ok(result)
    _assert_outside_unchanged(outside, before)
    _assert_repo_graph_is_local(victim)


def test_update_without_path_does_not_follow_dotdot_marker(tmp_path: Path) -> None:
    # Relative to the victim checkout (work/proj), this is work/proj-secret.
    victim, outside, env, before = _prepare(tmp_path, "../proj-secret\n")
    assert (victim / ".." / "proj-secret").resolve() == outside.resolve()
    result = _run_update(victim, env)
    _assert_update_ok(result)
    _assert_outside_unchanged(outside, before)
    _assert_repo_graph_is_local(victim)


def test_update_without_path_does_not_follow_prefix_or_parent_marker(tmp_path: Path) -> None:
    """A string-prefix check accepts both the sibling and the ``..`` form.

    ``<repo>/../proj-secret``.startswith(``<repo>``) is true, and
    ``<repo>-secret``.startswith(``<repo>``) is true. Containment has to be
    the resolved path.
    """
    outside_text = str(tmp_path / "work" / "proj") + "/../proj-secret\n"
    victim, outside, env, before = _prepare(tmp_path, outside_text)
    marker = (victim / "graphify-out" / ".graphify_root").read_text(encoding="utf-8").strip()
    assert marker.startswith(str(victim))
    assert Path(marker).resolve() == outside.resolve()
    assert not Path(marker).resolve().is_relative_to(victim.resolve())
    result = _run_update(victim, env)
    _assert_update_ok(result)
    _assert_outside_unchanged(outside, before)
    _assert_repo_graph_is_local(victim)


def test_update_without_path_does_not_follow_symlink_marker(tmp_path: Path) -> None:
    """The marker names an in-repo path. That path is a symlink to the outside tree."""

    def extra(origin: Path, outside: Path) -> list[Path]:
        link = origin / "scan-alias"
        link.symlink_to(outside, target_is_directory=True)
        return [link]

    victim, outside, env, before = _prepare(tmp_path, "scan-alias\n", extra_setup=extra)
    link = victim / "scan-alias"
    assert link.is_symlink()
    assert link.resolve() == outside.resolve()
    result = _run_update(victim, env)
    _assert_update_ok(result)
    _assert_outside_unchanged(outside, before)
    _assert_repo_graph_is_local(victim)


def _preload(tmp_path: Path) -> Path:
    source = tmp_path / "readlink_preload.c"
    library = tmp_path / "readlink_preload.so"
    source.write_text(textwrap.dedent(_READLINK_PRELOAD), encoding="utf-8")
    compiled = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(library), str(source), "-ldl"],
        capture_output=True,
        text=True,
    )
    if compiled.returncode != 0:
        raise AssertionError(f"gcc failed\n{compiled.stderr}")
    return library


def test_update_does_not_use_a_marker_retargeted_between_check_and_walk(tmp_path: Path) -> None:
    """The marker symlink resolves inside the repo on the first lookup.

    After that readlink returns, the symlink is pointed at the foreign tree.
    A scan that looks the marker path up again writes ``graphify-out`` there.
    Resolving once and scanning that resolved directory keeps the write inside
    the repository.
    """
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    outside = tmp_path / "outside"
    _write_outside(outside)
    repo = tmp_path / "repo"
    _init_repo(repo, env)
    inside = repo / "scoped"
    inside.mkdir()
    (inside / "nested.py").write_text(
        f"def {INSIDE_SYMBOL}():\n    return 'scoped'\n",
        encoding="utf-8",
    )
    link = repo / "scan-alias"
    link.symlink_to(inside, target_is_directory=True)
    gitignore = repo / ".gitignore"
    gitignore.write_text("graphify-out/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "scoped/nested.py", "scan-alias", env=env)
    _git(repo, "commit", "-m", "scoped tree", env=env)
    marker = repo / "graphify-out" / ".graphify_root"
    marker.parent.mkdir()
    marker.write_text("scan-alias\n", encoding="utf-8")
    before = _snapshot(outside)
    env["LD_PRELOAD"] = str(_preload(tmp_path))
    env["GRAPHIFY_TEST_LINK"] = str(link)
    env["GRAPHIFY_TEST_OUTSIDE"] = str(outside)
    result = _run_update(repo, env)
    _assert_update_ok(result)
    _assert_outside_unchanged(outside, before)
    graphs = list(repo.rglob("graph.json"))
    assert graphs, "expected a graph of the in-repo tree"
    combined = "\n".join(path.read_text(encoding="utf-8") for path in graphs)
    assert INSIDE_SYMBOL in combined
    assert OUTSIDE_SYMBOL not in combined


def test_checkout_hook_does_not_follow_committed_marker(tmp_path: Path) -> None:
    victim, outside, env, before = _prepare(tmp_path, str(tmp_path / "work" / "proj-secret") + "\n")
    # Land on the parent commit (no marker) before the hook is installed, then
    # switch back so the installed post-checkout hook runs the way git runs it.
    _git(victim, "checkout", "-b", "clean", "HEAD~1", env=env)
    assert not (victim / "graphify-out" / ".graphify_root").exists()
    install(victim)
    hook = victim / ".git" / "hooks" / "post-checkout"
    assert hook.is_file()
    assert ".graphify_root" in hook.read_text(encoding="utf-8")
    _git(victim, "checkout", "main", env=env)
    assert (victim / "graphify-out" / ".graphify_root").is_file()
    _wait_for_rebuild(env, victim, outside)
    _assert_outside_unchanged(outside, before)
    _assert_repo_graph_is_local(victim)


def test_post_commit_hook_does_not_follow_committed_marker(tmp_path: Path) -> None:
    victim, outside, env, before = _prepare(tmp_path, str(tmp_path / "work" / "proj-secret") + "\n")
    install(victim)
    hook = victim / ".git" / "hooks" / "post-commit"
    assert ".graphify_root" in hook.read_text(encoding="utf-8")
    inside = victim / "pkg" / "inside.py"
    inside.write_text(
        inside.read_text(encoding="utf-8") + "\ndef extra_marker_symbol():\n    return 2\n",
        encoding="utf-8",
    )
    _git(victim, "add", "pkg/inside.py", env=env)
    _git(victim, "commit", "-m", "edit inside", env=env)
    _wait_for_rebuild(env, victim, outside)
    _assert_outside_unchanged(outside, before)
    _assert_repo_graph_is_local(victim)


def test_update_honors_in_repo_marker(tmp_path: Path) -> None:
    """A marker that stays inside the work tree still selects that subtree."""
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    repo = tmp_path / "repo"
    _init_repo(repo, env)
    (repo / ".gitignore").write_text("graphify-out/\n", encoding="utf-8")
    (repo / "root_only.py").write_text(
        f"def {ROOT_ONLY_SYMBOL}():\n    return 'root'\n",
        encoding="utf-8",
    )
    (repo / "sub").mkdir()
    (repo / "sub" / "nested.py").write_text(
        f"def {NESTED_SYMBOL}():\n    return 'nested'\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".gitignore", "root_only.py", "sub/nested.py", env=env)
    _git(repo, "commit", "-m", "tree", env=env)
    marker = repo / "graphify-out" / ".graphify_root"
    marker.parent.mkdir()
    marker.write_text("sub\n", encoding="utf-8")
    result = _run_update(repo, env)
    _assert_update_ok(result)
    graph = repo / "sub" / "graphify-out" / "graph.json"
    assert graph.is_file(), "in-repo marker was not used as the scan root"
    text = graph.read_text(encoding="utf-8")
    assert NESTED_SYMBOL in text
    assert ROOT_ONLY_SYMBOL not in text


def test_update_explicit_path_is_the_scan_root(tmp_path: Path) -> None:
    """A path passed on the command line is the directory the user asked to scan."""
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    outside = tmp_path / "chosen"
    _write_outside(outside)
    repo = tmp_path / "repo"
    _init_repo(repo, env)
    _write_inside(repo)
    (repo / ".gitignore").write_text("graphify-out/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "pkg/inside.py", env=env)
    _git(repo, "commit", "-m", "tree", env=env)
    marker = repo / "graphify-out" / ".graphify_root"
    marker.parent.mkdir()
    # The committed marker names the repo. The CLI argument must win.
    marker.write_text(".\n", encoding="utf-8")
    result = _run_update(repo, env, str(outside))
    _assert_update_ok(result)
    graph = outside / "graphify-out" / "graph.json"
    assert graph.is_file(), "explicit path was not scanned"
    text = graph.read_text(encoding="utf-8")
    assert OUTSIDE_SYMBOL in text
    assert INSIDE_SYMBOL not in text


def _wait_for_rebuild(env: dict[str, str], repo: Path, outside: Path) -> None:
    log = Path(env["HOME"]) / ".cache" / "graphify-rebuild.log"
    deadline = time.time() + 120
    while time.time() < deadline:
        if _rebuild_settled(log, repo, outside):
            # Let the detached interpreter flush its atexit cache write.
            time.sleep(0.5)
            return
        time.sleep(0.1)
    logged = log.read_text(encoding="utf-8", errors="replace") if log.exists() else "<no log>"
    pytest.fail(f"hook rebuild did not finish\nlog:\n{logged}")


def _rebuild_settled(log: Path, repo: Path, outside: Path) -> bool:
    if log.exists():
        text = log.read_text(encoding="utf-8", errors="replace")
        needles = (
            "updated in",
            "Rebuild failed",
            "No code files",
            "skipping rebuild",
            "left untouched",
            "Traceback",
            "could not locate a Python",
        )
        if any(needle in text for needle in needles):
            return True
    if (repo / "graphify-out" / "graph.json").is_file():
        return True
    if (outside / "graphify-out" / "graph.json").is_file():
        return True
    return False
