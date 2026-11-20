"""Spec for graph-output writes that follow symlinks.

A file planted in the output directory (including a symlink committed under a
gitignore rule and checked out by git) must not be opened for writing in a way
that follows the link. These tests fail while that is still possible.

The race helper re-creates the symlink in front of any write that does not pass
O_NOFOLLOW, so unlinking the link and then calling write_text / open("w") /
a shell redirect still overwrites the outside file.

Not covered here, because this host cannot run them or they are separate
writers the build/update/skill path does not reach with the fixtures below:

- graphify/skill-windows.md PowerShell Out-File (no PowerShell on this host)
- graphify/wiki.py, graphify/reflect.py, graphify/transcribe.py,
  graphify/ingest.py, graphify/global_graph.py
- graphify/export.py obsidian vault, canvas, Cypher, SVG, and GraphML writers
- graphify/tree_html.py
- graphify export callflow-html as its own command (the update path's callflow
  write is covered; it calls the same write_callflow_html)
- graphify/watch.py _notify_only (needs_update)
- graphify/__main__.py merge-graphs, merge-chunks, merge-semantic, cache-check
- .graphify_semantic_marker (written only when a semantic token count is non-zero)
- tools/skillgen snapshots and fragments (not the files the product executes)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / "graphify"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

MARKER = b"OUTSIDE_FILE_PLACEHOLDER\n"
PRELOAD_C = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>

static int (*real_open)(const char *, int, ...) = NULL;
static int (*real_open64)(const char *, int, ...) = NULL;
static int (*real_openat)(int, const char *, int, ...) = NULL;
static int (*real_openat64)(int, const char *, int, ...) = NULL;
static __thread int busy = 0;

static int write_without_nofollow(int flags) {
    if (flags & O_NOFOLLOW) return 0;
    int acc = flags & O_ACCMODE;
    return acc == O_WRONLY || acc == O_RDWR;
}

static int abs_path(int dirfd, const char *path, char *out, size_t out_len) {
    if (!path || !path[0]) return -1;
    if (path[0] == '/') {
        return snprintf(out, out_len, "%s", path) < (int)out_len ? 0 : -1;
    }
    char dir[4096];
    if (dirfd == AT_FDCWD) {
        if (!getcwd(dir, sizeof dir)) return -1;
    } else {
        char fdpath[64];
        snprintf(fdpath, sizeof fdpath, "/proc/self/fd/%d", dirfd);
        ssize_t n = readlink(fdpath, dir, sizeof dir - 1);
        if (n < 0) return -1;
        dir[n] = 0;
    }
    const char *rel = path;
    if (rel[0] == '.' && rel[1] == '/') rel += 2;
    return snprintf(out, out_len, "%s/%s", dir, rel) < (int)out_len ? 0 : -1;
}

static int is_target(int dirfd, const char *path) {
    const char *want = getenv("GRAPHIFY_ATTACK_OUTPUT");
    if (!want || !path) return 0;
    char abs[4096];
    if (abs_path(dirfd, path, abs, sizeof abs) != 0) return 0;
    return strcmp(abs, want) == 0;
}

static void replant(const char *path) {
    const char *victim = getenv("GRAPHIFY_ATTACK_VICTIM");
    if (!victim || !path) return;
    unlink(path);
    symlink(victim, path);
}

static void maybe_replant(int dirfd, const char *path, int flags) {
    if (busy || !write_without_nofollow(flags) || !is_target(dirfd, path)) return;
    busy = 1;
    replant(path);
    busy = 0;
}

static int call_open(int (*fn)(const char *, int, ...), const char *path, int flags, int mode, int has_mode) {
    if (has_mode) return fn(path, flags, mode);
    return fn(path, flags);
}

static int call_openat(int (*fn)(int, const char *, int, ...), int dirfd, const char *path, int flags, int mode, int has_mode) {
    if (has_mode) return fn(dirfd, path, flags, mode);
    return fn(dirfd, path, flags);
}

int open(const char *path, int flags, ...) {
    if (!real_open) real_open = dlsym(RTLD_NEXT, "open");
    int has_mode = (flags & O_CREAT) != 0;
    int mode = 0;
    if (has_mode) {
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, int);
        va_end(ap);
    }
    maybe_replant(AT_FDCWD, path, flags);
    return call_open(real_open, path, flags, mode, has_mode);
}

int open64(const char *path, int flags, ...) {
    if (!real_open64) real_open64 = dlsym(RTLD_NEXT, "open64");
    int has_mode = (flags & O_CREAT) != 0;
    int mode = 0;
    if (has_mode) {
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, int);
        va_end(ap);
    }
    maybe_replant(AT_FDCWD, path, flags);
    return call_open(real_open64, path, flags, mode, has_mode);
}

int openat(int dirfd, const char *path, int flags, ...) {
    if (!real_openat) real_openat = dlsym(RTLD_NEXT, "openat");
    int has_mode = (flags & O_CREAT) != 0;
    int mode = 0;
    if (has_mode) {
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, int);
        va_end(ap);
    }
    maybe_replant(dirfd, path, flags);
    return call_openat(real_openat, dirfd, path, flags, mode, has_mode);
}

int openat64(int dirfd, const char *path, int flags, ...) {
    if (!real_openat64) real_openat64 = dlsym(RTLD_NEXT, "openat64");
    int has_mode = (flags & O_CREAT) != 0;
    int mode = 0;
    if (has_mode) {
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, int);
        va_end(ap);
    }
    maybe_replant(dirfd, path, flags);
    return call_openat(real_openat64, dirfd, path, flags, mode, has_mode);
}
"""


@pytest.fixture(scope="session")
def preload(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("preload")
    source = directory / "nofollow_race.c"
    library = directory / "nofollow_race.so"
    source.write_text(PRELOAD_C, encoding="utf-8")
    subprocess.check_call(
        ["gcc", "-shared", "-fPIC", "-o", str(library), str(source), "-ldl"],
    )
    return library


def _bash_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        if lines[index].startswith("```bash"):
            index += 1
            body: list[str] = []
            while index < len(lines) and not lines[index].startswith("```"):
                body.append(lines[index])
                index += 1
            blocks.append("".join(body))
        index += 1
    return blocks


def _skills_with(snippet: str) -> list[str]:
    names: list[str] = []
    for path in sorted(SKILL_DIR.glob("skill*.md")):
        if any(snippet in block for block in _bash_blocks(path.read_text(encoding="utf-8"))):
            names.append(path.name)
    if not names:
        raise RuntimeError(f"no skill bash block contains {snippet!r}")
    return names


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "src" / "hello.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "marker.txt"
    victim.write_bytes(MARKER)
    return project, victim


def _plant(project: Path, name: str, victim: Path) -> Path:
    link = project / "graphify-out" / name
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(os.path.relpath(victim, start=link.parent))
    return link


def _env(preload: Path, output: Path | None, victim: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONSAFEPATH"] = "1"
    env["GRAPHIFY_NO_TIPS"] = "1"
    env["GRAPHIFY_NO_BACKUP"] = "1"
    env["LD_PRELOAD"] = str(preload)
    for key in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MOONSHOT_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        env.pop(key, None)
    if output is not None and victim is not None:
        env["GRAPHIFY_ATTACK_OUTPUT"] = os.path.abspath(output)
        env["GRAPHIFY_ATTACK_VICTIM"] = os.path.abspath(victim)
    else:
        env.pop("GRAPHIFY_ATTACK_OUTPUT", None)
        env.pop("GRAPHIFY_ATTACK_VICTIM", None)
    return env


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _assert_safe(proc: subprocess.CompletedProcess[str], victim: Path, output: Path, marker: bytes = MARKER) -> None:
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert victim.is_file() and not victim.is_symlink()
    found = victim.read_bytes()
    assert found == marker, f"write followed {output} into {victim}: {found[:180]!r}"
    assert output.exists(), f"{output} was not written"
    assert not output.is_symlink(), f"{output} is still a symlink"
    assert output.is_file()
    assert output.stat().st_ino != victim.stat().st_ino
    assert output.parent.name == "graphify-out"
    assert not output.parent.is_symlink()
    assert output.read_bytes() != marker


def _graphify(project: Path, args: list[str], preload: Path, output: Path, victim: Path) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, "-m", "graphify", *args], project, _env(preload, output, victim))


def _write_skill_inputs(project: Path) -> None:
    from graphify.analyze import god_nodes, surprising_connections
    from graphify.build import build_from_json
    from graphify.cluster import cluster, score_all
    from graphify.detect import detect
    from graphify.extract import extract

    source = project / "src" / "hello.py"
    extracted = extract([source], cache_root=project)
    detected = detect(project)
    graph = build_from_json(extracted, root=project)
    communities = cluster(graph)
    cohesion = score_all(graph, communities)
    analysis = {
        "communities": {str(key): value for key, value in communities.items()},
        "cohesion": {str(key): value for key, value in cohesion.items()},
        "gods": god_nodes(graph),
        "surprises": surprising_connections(graph, communities),
        "questions": [],
    }
    out = project / "graphify-out"
    out.mkdir(parents=True, exist_ok=True)
    (out / ".graphify_python").write_text(sys.executable, encoding="utf-8")
    (out / ".graphify_extract.json").write_text(json.dumps(extracted), encoding="utf-8")
    (out / ".graphify_detect.json").write_text(json.dumps(detected), encoding="utf-8")
    (out / ".graphify_analysis.json").write_text(json.dumps(analysis), encoding="utf-8")


def _skill_block(skill_name: str, snippet: str) -> str:
    text = (SKILL_DIR / skill_name).read_text(encoding="utf-8")
    matches = [block for block in _bash_blocks(text) if snippet in block]
    assert len(matches) == 1, f"{skill_name} has {len(matches)} bash blocks containing {snippet!r}"
    return matches[0]


def _run_skill(
    project: Path,
    skill_name: str,
    snippet: str,
    preload: Path,
    output: Path,
    victim: Path,
) -> subprocess.CompletedProcess[str]:
    block = _skill_block(skill_name, snippet)
    block = block.replace("INPUT_PATH", str(project.resolve()))
    block = block.replace("IS_DIRECTED", "False")
    block = block.replace("LABELS_DICT", "{0: 'Hello'}")
    return _run(["bash", "-c", block], project, _env(preload, output, victim))


def test_preload_plain_open_follows_symlink(tmp_path: Path, preload: Path) -> None:
    """The race helper itself follows a non-NOFOLLOW write. This is the harness, not the product."""
    victim = tmp_path / "victim.txt"
    victim.write_bytes(MARKER)
    output = tmp_path / "link.txt"
    output.symlink_to(victim)
    proc = _run(
        [sys.executable, "-c", "import sys; open(sys.argv[1], 'w', encoding='utf-8').write('replaced\\n')", str(output)],
        tmp_path,
        _env(preload, output, victim),
    )
    assert proc.returncode == 0, proc.stderr
    assert victim.read_text(encoding="utf-8") == "replaced\n"


def test_preload_nofollow_create_leaves_symlink_target(tmp_path: Path, preload: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_bytes(MARKER)
    output = tmp_path / "link.txt"
    output.symlink_to(victim)
    script = (
        "import os, sys\n"
        "path = sys.argv[1]\n"
        "os.unlink(path)\n"
        "fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)\n"
        "os.write(fd, b'safe-output\\n')\n"
        "os.close(fd)\n"
    )
    proc = _run([sys.executable, "-c", script, str(output)], tmp_path, _env(preload, output, victim))
    assert proc.returncode == 0, proc.stderr
    assert victim.read_bytes() == MARKER
    assert output.is_file() and not output.is_symlink()
    assert output.read_bytes() == b"safe-output\n"


@pytest.mark.parametrize(
    ("label", "args", "output_name"),
    [
        ("update-root", ["update", "."], ".graphify_root"),
        ("update-report", ["update", "."], "GRAPH_REPORT.md"),
        ("update-labels", ["update", "."], ".graphify_labels.json"),
        ("update-manifest", ["update", "."], "manifest.json"),
        ("update-html", ["update", "."], "graph.html"),
        ("update-callflow", ["update", "."], "proj-callflow.html"),
        ("update-nocluster-graph", ["update", ".", "--no-cluster"], "graph.json"),
        ("update-nocluster-root", ["update", ".", "--no-cluster"], ".graphify_root"),
        ("extract-nocluster-graph", ["extract", ".", "--no-cluster"], "graph.json"),
        ("extract-graph", ["extract", "."], "graph.json"),
        ("extract-analysis", ["extract", "."], ".graphify_analysis.json"),
    ],
)
def test_cli_output_symlink_is_replaced_without_touching_target(
    tmp_path: Path,
    preload: Path,
    label: str,
    args: list[str],
    output_name: str,
) -> None:
    project, victim = _make_project(tmp_path)
    output = _plant(project, output_name, victim)
    proc = _graphify(project, args, preload, output, victim)
    _assert_safe(proc, victim, output)


def test_clustered_update_temp_graph_symlink_does_not_receive_json(tmp_path: Path, preload: Path) -> None:
    project, victim = _make_project(tmp_path)
    output = _plant(project, ".graph.tmp.json", victim)
    proc = _graphify(project, ["update", "."], preload, output, victim)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert victim.read_bytes() == MARKER, victim.read_bytes()[:180]
    assert not output.is_symlink()
    graph = project / "graphify-out" / "graph.json"
    assert graph.is_file() and not graph.is_symlink()
    assert graph.stat().st_ino != victim.stat().st_ino
    assert b'"nodes"' in graph.read_bytes()


def test_clustered_update_replaces_graph_json_symlink_in_place(tmp_path: Path, preload: Path) -> None:
    """graph.json on the clustered rebuild is renamed into place. The outside file stays put."""
    project, victim = _make_project(tmp_path)
    output = _plant(project, "graph.json", victim)
    proc = _graphify(project, ["update", "."], preload, output, victim)
    _assert_safe(proc, victim, output)


def _seed_graph(project: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["GRAPHIFY_NO_TIPS"] = "1"
    env["GRAPHIFY_NO_BACKUP"] = "1"
    env.pop("LD_PRELOAD", None)
    proc = _run([sys.executable, "-m", "graphify", "update", "."], project, env)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr


@pytest.mark.parametrize(
    "output_name",
    [
        "GRAPH_REPORT.md",
        ".graphify_labels.json",
        ".graphify_analysis.json",
        ".graphify_labels.json.sig",
    ],
)
def test_cluster_only_output_symlink(tmp_path: Path, preload: Path, output_name: str) -> None:
    project, victim = _make_project(tmp_path)
    _seed_graph(project)
    output = _plant(project, output_name, victim)
    proc = _graphify(project, ["cluster-only", ".", "--no-viz"], preload, output, victim)
    _assert_safe(proc, victim, output)


def test_cluster_only_graph_json_symlink_keeps_prior_bytes(tmp_path: Path, preload: Path) -> None:
    project, victim = _make_project(tmp_path)
    _seed_graph(project)
    seeded = json.loads((project / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    seeded["outside_placeholder"] = "OUTSIDE_FILE_PLACEHOLDER"
    marker = (json.dumps(seeded) + "\n").encode()
    victim.write_bytes(marker)
    output = _plant(project, "graph.json", victim)
    proc = _graphify(project, ["cluster-only", ".", "--no-viz"], preload, output, victim)
    _assert_safe(proc, victim, output, marker)


def test_export_html_symlink(tmp_path: Path, preload: Path) -> None:
    project, victim = _make_project(tmp_path)
    _seed_graph(project)
    output = _plant(project, "graph.html", victim)
    proc = _graphify(project, ["export", "html"], preload, output, victim)
    _assert_safe(proc, victim, output)


def test_update_does_not_write_through_symlinked_output_directory(tmp_path: Path) -> None:
    project, victim = _make_project(tmp_path)
    outside = victim.parent
    before = sorted(path.name for path in outside.iterdir())
    output_dir = project / "graphify-out"
    output_dir.symlink_to(os.path.relpath(outside, start=project), target_is_directory=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["GRAPHIFY_NO_TIPS"] = "1"
    env["GRAPHIFY_NO_BACKUP"] = "1"
    env.pop("LD_PRELOAD", None)
    proc = _run([sys.executable, "-m", "graphify", "update", "."], project, env)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert victim.read_bytes() == MARKER
    assert sorted(path.name for path in outside.iterdir()) == before
    assert output_dir.is_dir() and not output_dir.is_symlink()
    assert (output_dir / "graph.json").is_file()
    assert (output_dir / ".graphify_root").is_file()
    assert not (output_dir / "graph.json").is_symlink()


def test_cloned_gitignored_root_symlink_is_not_followed(tmp_path: Path, preload: Path) -> None:
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "marker.txt"
    victim.write_bytes(MARKER)
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(origin)])
    subprocess.check_call(["git", "init", "-b", "main", str(work)])
    subprocess.check_call(["git", "-C", str(work), "config", "user.email", "attacker@example.com"])
    subprocess.check_call(["git", "-C", str(work), "config", "user.name", "Attacker"])
    (work / "src").mkdir()
    (work / "src" / "hello.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (work / ".gitignore").write_text("graphify-out/\n", encoding="utf-8")
    link = _plant(work, ".graphify_root", victim)
    subprocess.check_call(["git", "-C", str(work), "add", "-f", ".gitignore", "src/hello.py", "graphify-out/.graphify_root"])
    subprocess.check_call(["git", "-C", str(work), "commit", "-m", "plant output symlink"])
    subprocess.check_call(["git", "-C", str(work), "push", str(origin), "HEAD:main"])
    clone = tmp_path / "clone"
    subprocess.check_call(["git", "-c", "core.symlinks=true", "clone", str(origin), str(clone)])
    cloned = clone / "graphify-out" / ".graphify_root"
    assert cloned.is_symlink(), "git did not check out the committed symlink"
    assert os.path.abspath(cloned.resolve()) == os.path.abspath(victim)
    proc = _graphify(clone, ["update", "."], preload, cloned, victim)
    _assert_safe(proc, victim, cloned)
    assert link.is_symlink()


@pytest.mark.parametrize("body_name", ["_REBUILD_BODY_CHECKOUT", "_REBUILD_BODY_COMMIT"])
def test_hook_rebuild_does_not_follow_report_symlink(tmp_path: Path, preload: Path, body_name: str) -> None:
    project, victim = _make_project(tmp_path)
    out = project / "graphify-out"
    out.mkdir()
    (out / ".graphify_root").write_text(str(project.resolve()) + "\n", encoding="utf-8")
    output = _plant(project, "GRAPH_REPORT.md", victim)
    env = _env(preload, output, victim)
    env["GRAPHIFY_FORCE"] = "1"
    env["GRAPHIFY_CHANGED"] = "src/hello.py"
    script = f"from graphify.hooks import {body_name}; exec({body_name})"
    proc = _run([sys.executable, "-c", script], project, env)
    _assert_safe(proc, victim, output)


@pytest.mark.parametrize("skill_name", _skills_with("> graphify-out/.graphify_root"))
@pytest.mark.parametrize("output_name", [".graphify_python", ".graphify_root"])
def test_skill_step1_symlink(tmp_path: Path, preload: Path, skill_name: str, output_name: str) -> None:
    project, victim = _make_project(tmp_path)
    output = _plant(project, output_name, victim)
    proc = _run_skill(project, skill_name, "> graphify-out/.graphify_root", preload, output, victim)
    _assert_safe(proc, victim, output)


@pytest.mark.parametrize("skill_name", _skills_with("if [ ! -f graphify-out/.graphify_python ]"))
def test_skill_interpreter_guard_dangling_symlink(tmp_path: Path, preload: Path, skill_name: str) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    missing = outside / "not-created.txt"
    output = _plant(project, ".graphify_python", missing)
    proc = _run_skill(
        project,
        skill_name,
        "if [ ! -f graphify-out/.graphify_python ]",
        preload,
        output,
        missing,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert not missing.exists(), "interpreter write created the symlink target"
    assert output.is_file() and not output.is_symlink()
    assert output.stat().st_size > 0


@pytest.mark.parametrize("skill_name", _skills_with("> graphify-out/.graphify_detect.json"))
def test_skill_detect_redirect_symlink(tmp_path: Path, preload: Path, skill_name: str) -> None:
    project, victim = _make_project(tmp_path)
    out = project / "graphify-out"
    out.mkdir()
    (out / ".graphify_python").write_text(sys.executable, encoding="utf-8")
    output = _plant(project, ".graphify_detect.json", victim)
    proc = _run_skill(project, skill_name, "> graphify-out/.graphify_detect.json", preload, output, victim)
    _assert_safe(proc, victim, output)


@pytest.mark.parametrize(
    "skill_name",
    _skills_with("{'nodes':[],'edges':[],'hyperedges':[],'input_tokens':0,'output_tokens':0}"),
)
def test_skill_empty_semantic_symlink(tmp_path: Path, preload: Path, skill_name: str) -> None:
    project, victim = _make_project(tmp_path)
    out = project / "graphify-out"
    out.mkdir()
    (out / ".graphify_python").write_text(sys.executable, encoding="utf-8")
    output = _plant(project, ".graphify_semantic.json", victim)
    proc = _run_skill(
        project,
        skill_name,
        "{'nodes':[],'edges':[],'hyperedges':[],'input_tokens':0,'output_tokens':0}",
        preload,
        output,
        victim,
    )
    _assert_safe(proc, victim, output)


@pytest.mark.parametrize("skill_name", _skills_with("Path('graphify-out/.graphify_ast.json').write_text"))
def test_skill_ast_symlink(tmp_path: Path, preload: Path, skill_name: str) -> None:
    project, victim = _make_project(tmp_path)
    out = project / "graphify-out"
    out.mkdir()
    (out / ".graphify_python").write_text(sys.executable, encoding="utf-8")
    detected = {
        "files": {"code": [str(project / "src" / "hello.py")], "document": [], "paper": [], "image": []},
        "total_files": 1,
    }
    (out / ".graphify_detect.json").write_text(json.dumps(detected), encoding="utf-8")
    output = _plant(project, ".graphify_ast.json", victim)
    proc = _run_skill(
        project,
        skill_name,
        "Path('graphify-out/.graphify_ast.json').write_text",
        preload,
        output,
        victim,
    )
    _assert_safe(proc, victim, output)


@pytest.mark.parametrize("skill_name", _skills_with("to_json(G, communities, 'graphify-out/graph.json')"))
@pytest.mark.parametrize("output_name", ["graph.json", "GRAPH_REPORT.md", ".graphify_analysis.json"])
def test_skill_step4_symlink(tmp_path: Path, preload: Path, skill_name: str, output_name: str) -> None:
    project, victim = _make_project(tmp_path)
    _write_skill_inputs(project)
    output = _plant(project, output_name, victim)
    proc = _run_skill(
        project,
        skill_name,
        "to_json(G, communities, 'graphify-out/graph.json')",
        preload,
        output,
        victim,
    )
    _assert_safe(proc, victim, output)


@pytest.mark.parametrize("skill_name", _skills_with("LABELS_DICT"))
@pytest.mark.parametrize("output_name", ["GRAPH_REPORT.md", ".graphify_labels.json"])
def test_skill_step5_symlink(tmp_path: Path, preload: Path, skill_name: str, output_name: str) -> None:
    project, victim = _make_project(tmp_path)
    _write_skill_inputs(project)
    output = _plant(project, output_name, victim)
    proc = _run_skill(project, skill_name, "LABELS_DICT", preload, output, victim)
    _assert_safe(proc, victim, output)


@pytest.mark.parametrize("skill_name", _skills_with("cost_path.write_text"))
def test_skill_step9_cost_symlink(tmp_path: Path, preload: Path, skill_name: str) -> None:
    project, victim = _make_project(tmp_path)
    _write_skill_inputs(project)
    marker = b'{"outside_placeholder":"OUTSIDE_FILE_PLACEHOLDER","runs":[],"total_input_tokens":0,"total_output_tokens":0}\n'
    victim.write_bytes(marker)
    output = _plant(project, "cost.json", victim)
    proc = _run_skill(project, skill_name, "cost_path.write_text", preload, output, victim)
    _assert_safe(proc, victim, output, marker)


@pytest.mark.parametrize("skill_name", _skills_with("cost_path.write_text"))
def test_skill_step9_manifest_symlink(tmp_path: Path, preload: Path, skill_name: str) -> None:
    project, victim = _make_project(tmp_path)
    _write_skill_inputs(project)
    output = _plant(project, "manifest.json", victim)
    proc = _run_skill(project, skill_name, "cost_path.write_text", preload, output, victim)
    _assert_safe(proc, victim, output)
