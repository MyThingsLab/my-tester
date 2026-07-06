from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clean_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # pre-commit runs hooks with GIT_DIR/GIT_INDEX_FILE set; they leak into the
    # git subprocesses these tests spawn (and into isolation.Workspace) and break
    # worktree ops on the throwaway repo. Real MyTester runs aren't inside a hook.
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(var, raising=False)

_OPS = """def add(a, b):
    return a + b


def sub(a, b):
    return a - b


def _private(x):
    return x
"""

_TEST_ADD = """from calc.ops import add


def test_add():
    assert add(1, 2) == 3
"""

_TEST_BOTH = """from calc.ops import add, sub


def test_add():
    assert add(1, 2) == 3


def test_sub():
    assert sub(3, 1) == 2
"""


def _git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True)


class FakeRunner:
    # Mocks only the `gh` subprocess boundary.
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:2] == ["pr", "create"]:
            return "https://github.com/owner/name/pull/7\n"
        raise AssertionError(f"unexpected gh call: {argv}")


def make_target_repo(tmp_path: Path, *, fully_covered: bool) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    repo = tmp_path / "work"
    repo.mkdir()
    (repo / "calc").mkdir()
    (repo / "calc" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "calc" / "ops.py").write_text(_OPS, encoding="utf-8")
    (repo / "tests").mkdir()
    test_src = _TEST_BOTH if fully_covered else _TEST_ADD
    (repo / "tests" / "test_ops.py").write_text(test_src, encoding="utf-8")

    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return repo
