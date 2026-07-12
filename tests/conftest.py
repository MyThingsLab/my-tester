from __future__ import annotations

from pathlib import Path

import pytest

# Shared fakes come from mythings.testing (plain imports; aliased fixture
# re-export + getfixturevalue wrapper per core docs/CONVENTIONS.md).
from mythings.testing import FakeGh, make_git_repo
from mythings.testing import clean_git_env as _shared_clean_git_env  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_git_env(request: pytest.FixtureRequest) -> None:
    # Real git worktrees in every test; hook-launched pytest (pre-commit)
    # must not leak GIT_* into them.
    request.getfixturevalue("_shared_clean_git_env")


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


def fake_gh() -> FakeGh:
    return FakeGh({("pr", "create"): "https://github.com/owner/name/pull/7\n"})


def make_target_repo(tmp_path: Path, *, fully_covered: bool) -> Path:
    return make_git_repo(
        tmp_path,
        files={
            "calc/__init__.py": "",
            "calc/ops.py": _OPS,
            "tests/test_ops.py": _TEST_BOTH if fully_covered else _TEST_ADD,
        },
    ).path
