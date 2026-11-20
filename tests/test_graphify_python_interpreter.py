"""Repository-controlled interpreter paths must not be executed.

The graphify skill tells a coding agent to keep graphify-out/.graphify_python
when that file already exists, then to run its contents, unquoted, as the
program for query, path, explain, update, add, and the other fenced commands
that use the same substitution. A commit can plant that file so it points at
an executable in the same tree (gitignored paths still check out once they
are tracked). These tests run those skill commands.

They fail while a planted in-repo path is executed. They pass once every
substitution refuses to execute a path that resolves inside the project, and
still refuses when the file is replaced between the interpreter guard and the
command that uses it. An absolute interpreter outside the project tree remains
the supported case and is covered separately.

Run:
    pytest tests/test_graphify_python_interpreter.py
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPHIFY = ROOT / "graphify"

# Skill commands that expand this unquoted substitution execute the stored path.
_EXEC = "$(cat graphify-out/.graphify_python)"
_GUARD_NEEDLE = "if [ ! -f graphify-out/.graphify_python ]; then"


def _bash_fences(text: str) -> list[tuple[int, str]]:
    """Return (1-based body line, body) for each ```bash fence."""
    fences: list[tuple[int, str]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if lines[index].startswith("```bash"):
            body_line = index + 2
            index += 1
            body: list[str] = []
            while index < len(lines) and not lines[index].startswith("```"):
                body.append(lines[index])
                index += 1
            fences.append((body_line, "\n".join(body) + "\n"))
        index += 1
    return fences


def _exec_fences(text: str) -> list[tuple[int, str]]:
    return [(line, body) for line, body in _bash_fences(text) if _EXEC in body]


def _guard_script(text: str, *, required: bool = True) -> str:
    for _line, body in _bash_fences(text):
        if _GUARD_NEEDLE in body:
            return body
    if required:
        raise AssertionError("skill is missing the .graphify_python existence guard")
    # Aider's monolith has no subcommand guard. Later fences still expand the file.
    return "true\n"


def _skill_for_bundle(bundle: str) -> Path:
    named = GRAPHIFY / f"skill-{bundle}.md"
    if named.is_file():
        return named
    if bundle == "claude":
        return GRAPHIFY / "skill.md"
    raise AssertionError(f"no skill file for references bundle {bundle}")


def _payload_script(log: Path) -> str:
    # Quoted so the test's log path cannot be reparsed as shell syntax.
    return "#!/bin/sh\n" f"printf '%s\\n' \"$0\" >> {shlex.quote(str(log))}\n" "exit 0\n"


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "graphify-test",
            "GIT_AUTHOR_EMAIL": "graphify-test@example.com",
            "GIT_COMMITTER_NAME": "graphify-test",
            "GIT_COMMITTER_EMAIL": "graphify-test@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        }
    )
    return env


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True)


def plant_cloned_repo(tmp: Path, log: Path) -> Path:
    """Commit a planted interpreter and return a fresh clone of that repo.

    graphify-out/ is gitignored. The files are force-added, which is enough
    for a later clone to check them out, executable bit included.
    """
    origin = tmp / "origin"
    clone = tmp / "clone"
    out = origin / "graphify-out"
    out.mkdir(parents=True)
    (origin / ".gitignore").write_text("graphify-out/\n", encoding="utf-8")
    (out / "graph.json").write_text(
        '{"nodes":[{"id":"a","label":"alpha"}],"links":[]}\n',
        encoding="utf-8",
    )
    payload = out / "payload"
    payload.write_text(_payload_script(log), encoding="utf-8")
    payload.chmod(0o755)
    (out / ".graphify_python").write_text("graphify-out/payload\n", encoding="utf-8")

    env = _git_env()
    _run(["git", "init", "-q", "-b", "main", str(origin)], env=env)
    _run(
        [
            "git",
            "-C",
            str(origin),
            "add",
            "-f",
            "--",
            ".gitignore",
            "graphify-out/graph.json",
            "graphify-out/payload",
            "graphify-out/.graphify_python",
        ],
        env=env,
    )
    _run(["git", "-C", str(origin), "commit", "-q", "-m", "plant interpreter"], env=env)
    _run(["git", "clone", "-q", str(origin), str(clone)], env=env)

    checked = clone / "graphify-out" / ".graphify_python"
    cloned_payload = clone / "graphify-out" / "payload"
    assert checked.read_text(encoding="utf-8") == "graphify-out/payload\n"
    mode = cloned_payload.stat().st_mode
    assert mode & stat.S_IXUSR, "clone dropped the executable bit the commit recorded"
    return clone


def _hits(log: Path) -> list[str]:
    if not log.is_file():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _clear(log: Path) -> None:
    log.write_text("", encoding="utf-8")


def _restore_payload(clone: Path, log: Path) -> None:
    payload = clone / "graphify-out" / "payload"
    payload.write_text(_payload_script(log), encoding="utf-8")
    payload.chmod(0o755)


def _write_interpreter(clone: Path, value: str) -> None:
    text = value if value.endswith("\n") else value + "\n"
    (clone / "graphify-out" / ".graphify_python").write_text(text, encoding="utf-8")


class BashResult:
    def __init__(self, returncode: int | None, timed_out: bool, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.timed_out = timed_out
        self.stdout = stdout
        self.stderr = stderr


def run_bash(
    clone: Path,
    script: str,
    *,
    env_extra: dict[str, str] | None = None,
    timeout: float = 8,
) -> BashResult:
    """Run a skill bash fence in the cloned repo. Kill the whole process group on timeout."""
    env = os.environ.copy()
    env.pop("BASH_ENV", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(
        ["bash", "--noprofile", "--norc", "-c", script],
        cwd=clone,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        return BashResult(None, True, stdout or "", stderr or "")
    return BashResult(proc.returncode, False, stdout, stderr)


def _dangerous_values(clone: Path) -> list[tuple[str, str]]:
    payload = (clone / "graphify-out" / "payload").resolve()
    return [
        ("relative", "graphify-out/payload"),
        ("parent-segment", "graphify-out/../graphify-out/payload"),
        ("proc-self-cwd", "/proc/self/cwd/graphify-out/payload"),
        ("absolute-inside-repo", str(payload)),
        ("word-split", "graphify-out/payload extra"),
    ]


def _command_pairs() -> list[tuple[Path, Path, int, str]]:
    """(skill file that holds the guard, file that holds the command, line, body)."""
    pairs: list[tuple[Path, Path, int, str]] = []
    for skill in sorted(GRAPHIFY.glob("skill*.md")):
        text = skill.read_text(encoding="utf-8")
        for line, body in _exec_fences(text):
            pairs.append((skill, skill, line, body))
    refs_root = GRAPHIFY / "skills"
    for ref in sorted(refs_root.glob("*/references/*.md")):
        bundle = ref.relative_to(refs_root).parts[0]
        skill = _skill_for_bundle(bundle)
        for line, body in _exec_fences(ref.read_text(encoding="utf-8")):
            pairs.append((skill, ref, line, body))
    assert pairs, "no skill commands substitute .graphify_python"
    return pairs


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _run_guard_then_command(
    clone: Path,
    log: Path,
    guard: str,
    command: str,
    *,
    interpreter: str | None = None,
    swap_after_guard: str | None = None,
) -> list[str]:
    """Return human-readable failures. Empty means the payload did not run.

    ``interpreter`` is written before the guard. ``swap_after_guard`` is written
    after the guard returns and before the command, so a guard that rewrites the
    file still loses if the command looks the path up again.
    """
    _restore_payload(clone, log)
    if interpreter is not None:
        _write_interpreter(clone, interpreter)
    _clear(log)
    run_bash(clone, guard)
    guard_hits = _hits(log)
    if guard_hits:
        return [f"guard executed the stored interpreter ({guard_hits})"]
    if swap_after_guard is not None:
        _write_interpreter(clone, swap_after_guard)
        _clear(log)
    run_bash(clone, command)
    command_hits = _hits(log)
    if command_hits:
        return [f"command executed the stored interpreter ({command_hits})"]
    return []


def test_query_does_not_execute_committed_interpreter(tmp_path: Path) -> None:
    """Attack path: committed graph, payload, and .graphify_python survive clone.

    The subcommand guard sees the file and leaves it. The query reference then
    runs that path.
    """
    log = tmp_path / "payload.log"
    clone = plant_cloned_repo(tmp_path, log)
    skill = (GRAPHIFY / "skill.md").read_text(encoding="utf-8")
    query = (GRAPHIFY / "skills" / "claude" / "references" / "query.md").read_text(
        encoding="utf-8"
    )
    guard = _guard_script(skill)
    existence = next(body for line, body in _exec_fences(query) if "No graph found" in body)
    assert "graphify-out/graph.json" in existence

    failures = _run_guard_then_command(clone, log, guard, existence)
    assert not failures, (
        "query executed the interpreter path committed in the repository: "
        + "; ".join(failures)
    )


def test_query_paths_inside_the_repo_are_not_executed(tmp_path: Path) -> None:
    """Relative, rewritten, /proc/self/cwd, absolute, and word-split paths stay inert.

    The value is swapped in after the guard so the command's own lookup is what
    decides. Quoting the substitution is not enough: a relative path still runs.
    """
    log = tmp_path / "payload.log"
    clone = plant_cloned_repo(tmp_path, log)
    failures: list[str] = []
    outside = sys_executable()
    for skill, source, line, body in _command_pairs():
        if source.name != "query.md" and "No graph found" not in body:
            continue
        guard = _guard_script(skill.read_text(encoding="utf-8"), required=False)
        for label, value in _dangerous_values(clone):
            found = _run_guard_then_command(
                clone,
                log,
                guard,
                body,
                interpreter=outside,
                swap_after_guard=value,
            )
            for item in found:
                failures.append(f"{_rel(source)}:{line} [{label}] {item}")
    assert not failures, (
        f"{len(failures)} query interpreter lookups executed an in-repo path:\n"
        + "\n".join(failures[:60])
    )


def test_every_substitution_rechecks_safely_after_the_guard(tmp_path: Path) -> None:
    """Every fenced substitution shares the query bug, including path, explain, update, and add.

    The guard runs first while the file names an outside interpreter. The file is
    then replaced with the in-repo payload. A fix that only rewrites the file inside
    the guard still fails, because the command looks the path up again.
    """
    log = tmp_path / "payload.log"
    clone = plant_cloned_repo(tmp_path, log)
    outside = sys_executable()
    failures: list[str] = []
    seen: set[tuple[str, int]] = set()
    for skill, source, line, body in _command_pairs():
        key = (_rel(source), line)
        if key in seen:
            continue
        seen.add(key)
        guard = _guard_script(skill.read_text(encoding="utf-8"), required=False)
        found = _run_guard_then_command(
            clone,
            log,
            guard,
            body,
            interpreter=outside,
            swap_after_guard="graphify-out/payload",
        )
        for item in found:
            failures.append(f"{_rel(source)}:{line} {item}")
    assert not failures, (
        f"{len(failures)} commands executed graphify-out/payload after the guard:\n"
        + "\n".join(failures[:80])
    )


def test_second_cat_of_interpreter_file_is_not_executed(tmp_path: Path) -> None:
    """A second cat of .graphify_python in the same command must not win.

    The first read returns an absolute interpreter outside the repo. Any later
    read returns the in-repo payload. The shipped query check reads the file once,
    so this stays green until a change executes a later read.
    """
    log = tmp_path / "payload.log"
    clone = plant_cloned_repo(tmp_path, log)
    query = (GRAPHIFY / "skills" / "claude" / "references" / "query.md").read_text(
        encoding="utf-8"
    )
    existence = next(body for _line, body in _exec_fences(query) if "No graph found" in body)
    guard = _guard_script((GRAPHIFY / "skill.md").read_text(encoding="utf-8"))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    count = tmp_path / "cat-count"
    wrapper = bindir / "cat"
    wrapper.write_text(
        "#!/bin/sh\n"
        "hit=\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in\n"
        "    graphify-out/.graphify_python|./graphify-out/.graphify_python) hit=1 ;;\n"
        "  esac\n"
        "done\n"
        "if [ \"$hit\" = 1 ]; then\n"
        "  n=0\n"
        "  if [ -f \"$GRAPHIFY_CAT_COUNT\" ]; then n=$(/bin/cat \"$GRAPHIFY_CAT_COUNT\"); fi\n"
        "  n=$((n + 1))\n"
        "  printf '%s' \"$n\" > \"$GRAPHIFY_CAT_COUNT\"\n"
        "  if [ \"$n\" -eq 1 ]; then\n"
        "    printf '%s\\n' \"graphify-out/payload\" > graphify-out/.graphify_python\n"
        "    printf '%s\\n' \"$GRAPHIFY_SAFE_INTERPRETER\"\n"
        "  else\n"
        "    printf '%s\\n' 'graphify-out/payload'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "exec /bin/cat \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    outside = sys_executable()
    _restore_payload(clone, log)
    _write_interpreter(clone, outside)
    _clear(log)
    count.write_text("0", encoding="utf-8")
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
        "GRAPHIFY_CAT_COUNT": str(count),
        "GRAPHIFY_SAFE_INTERPRETER": outside,
    }
    run_bash(clone, guard, env_extra=env)
    assert not _hits(log), "guard executed the in-repo payload"
    _clear(log)
    result = run_bash(clone, existence, env_extra=env)
    assert not _hits(log), "a later read of .graphify_python was executed"
    assert result.returncode == 0, result.stderr


def test_absolute_interpreter_outside_the_repo_still_runs(tmp_path: Path) -> None:
    """The supported value is an absolute interpreter that lives outside the tree."""
    log = tmp_path / "payload.log"
    clone = plant_cloned_repo(tmp_path, log)
    skill = (GRAPHIFY / "skill.md").read_text(encoding="utf-8")
    query = (GRAPHIFY / "skills" / "claude" / "references" / "query.md").read_text(
        encoding="utf-8"
    )
    outside = sys_executable()
    assert Path(outside).is_absolute()
    assert not _is_inside(Path(outside), clone)
    guard = _guard_script(skill)
    existence = next(body for _line, body in _exec_fences(query) if "No graph found" in body)
    _restore_payload(clone, log)
    _write_interpreter(clone, outside)
    _clear(log)
    run_bash(clone, guard)
    assert not _hits(log)
    # The guard must not have replaced the outside interpreter with the payload.
    stored = (clone / "graphify-out" / ".graphify_python").read_text(encoding="utf-8").strip()
    assert stored == outside
    result = run_bash(clone, existence)
    assert not _hits(log), "outside interpreter path executed the in-repo payload"
    assert result.returncode == 0, result.stderr


def test_full_build_step_rewrites_interpreter_before_detect(tmp_path: Path) -> None:
    """A fresh full build writes the running interpreter before later steps use the file.

    The subcommand guard does not. This test locks the full-build order: detect must
    not observe the planted payload.
    """
    log = tmp_path / "payload.log"
    clone = plant_cloned_repo(tmp_path, log)
    (clone / "INPUT_PATH").mkdir()
    skill = (GRAPHIFY / "skill.md").read_text(encoding="utf-8")
    step1 = next(
        body
        for _line, body in _bash_fences(skill)
        if "open('graphify-out/.graphify_python'" in body
    )
    step2 = next(body for _line, body in _exec_fences(skill) if "from graphify.detect import detect" in body)
    _clear(log)
    env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
    run_bash(clone, step1, env_extra=env, timeout=30)
    assert not _hits(log), "step 1 executed the planted interpreter"
    stored = (clone / "graphify-out" / ".graphify_python").read_text(encoding="utf-8").strip()
    assert os.path.realpath(stored) == sys_executable()
    assert not _is_inside(Path(stored), clone)
    run_bash(clone, step2, env_extra=env, timeout=30)
    assert not _hits(log), "detect step executed the planted interpreter"


def test_cluster_only_command_does_not_launch_stored_interpreter(tmp_path: Path) -> None:
    """--cluster-only is named by the guard, but its command does not expand the file."""
    log = tmp_path / "payload.log"
    clone = plant_cloned_repo(tmp_path, log)
    update = (GRAPHIFY / "skills" / "claude" / "references" / "update.md").read_text(
        encoding="utf-8"
    )
    cluster = next(body for _line, body in _bash_fences(update) if "graphify cluster-only" in body)
    assert _EXEC not in cluster
    guard = _guard_script((GRAPHIFY / "skill.md").read_text(encoding="utf-8"))
    failures = _run_guard_then_command(clone, log, guard, cluster)
    assert not failures, "; ".join(failures)


def test_skill_query_cli_line_does_not_launch_stored_interpreter(tmp_path: Path) -> None:
    """skill.md's `graphify query` line does not itself expand .graphify_python.

    The execution is in the query reference that this section tells the agent to load.
    """
    log = tmp_path / "payload.log"
    clone = plant_cloned_repo(tmp_path, log)
    skill = (GRAPHIFY / "skill.md").read_text(encoding="utf-8")
    cli = next(
        body
        for _line, body in _bash_fences(skill)
        if body.strip().startswith("graphify query ")
    )
    assert _EXEC not in cli
    guard = _guard_script(skill)
    failures = _run_guard_then_command(clone, log, guard, cli)
    assert not failures, "; ".join(failures)


def sys_executable() -> str:
    found = shutil.which("python3")
    if found is None:
        raise AssertionError("python3 is not on PATH")
    return os.path.realpath(found)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
