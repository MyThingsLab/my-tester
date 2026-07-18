from __future__ import annotations

from pathlib import Path

from mythings.github import GitHub, GitHubError
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult
from mythings.testing import FakeGh, make_git_repo

from mytester.coverage import parse_failures
from mytester.red import TEST_DRIVEN_LABEL, Red


def test_parse_failures_extracts_nodeid_and_reason() -> None:
    output = (
        "=== FAILURES ===\n...\n=== short test summary info ===\n"
        "FAILED tests/test_ops.py::test_sub - AssertionError: assert 4 == 5\n"
        "1 failed in 0.01s\n"
    )
    assert parse_failures(output) == [
        ("tests/test_ops.py::test_sub", "AssertionError: assert 4 == 5")
    ]


def test_parse_failures_empty_when_nothing_failed() -> None:
    assert parse_failures("2 passed in 0.01s\n") == []


_OPS = "def sub(a, b):\n    return a - b\n"

_PASSING_TEST = "from calc.ops import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"
_FAILING_TEST = "from calc.ops import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 999\n"


def _target_repo(tmp_path: Path, *, passing: bool) -> Path:
    return make_git_repo(
        tmp_path,
        files={
            "calc/__init__.py": "",
            "calc/ops.py": _OPS,
            "tests/test_ops.py": _PASSING_TEST if passing else _FAILING_TEST,
        },
    ).path


def _red(repo: Path, tmp_path: Path, fake: FakeGh, **kw) -> tuple[Red, Ledger]:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    red = Red(repo=repo, ledger=ledger, github=GitHub("owner/name", runner=fake), runner=fake, **kw)
    return red, ledger


def test_clean_suite_reports_clean_and_files_nothing(tmp_path: Path) -> None:
    repo = _target_repo(tmp_path, passing=True)
    fake = FakeGh()
    red, ledger = _red(repo, tmp_path, fake)

    result = red.run()

    assert result.outcome == "clean"
    assert result.filed == ()
    assert fake.calls == []  # no gh call at all -- nothing to file
    assert list(ledger)[0].outcome == "clean"


def test_failing_suite_files_one_issue(tmp_path: Path) -> None:
    repo = _target_repo(tmp_path, passing=False)
    fake = FakeGh(
        {
            ("issue", "list"): "[]",
            ("issue", "create"): "https://github.com/owner/name/issues/9\n",
            ("issue", "edit"): "",
        }
    )
    red, ledger = _red(repo, tmp_path, fake)

    result = red.run()

    assert result.outcome == "filed"
    assert result.filed == (9,)
    create_call = next(c for c in fake.calls if c[:2] == ["issue", "create"])
    title = create_call[create_call.index("--title") + 1]
    assert "tests/test_ops.py::test_sub" in title
    entries = list(ledger)
    assert entries[0].outcome == "red_filed"
    assert entries[0].data["issue"] == 9


def test_already_open_issue_is_not_refiled(tmp_path: Path) -> None:
    repo = _target_repo(tmp_path, passing=False)
    title = "test: tests/test_ops.py::test_sub is failing"
    fake = FakeGh(
        {
            (
                "issue",
                "list",
            ): f'[{{"number": 3, "title": "{title}", "body": "", "labels": [], "url": "https://x/3"}}]'
        }
    )  # noqa: E501
    red, ledger = _red(repo, tmp_path, fake)

    result = red.run()

    assert result.outcome == "clean"
    assert result.filed == ()
    assert not any(c[:2] == ["issue", "create"] for c in fake.calls)


class _DenyAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY, reason="locked down", rule="deny_all")


def test_policy_deny_skips_filing(tmp_path: Path) -> None:
    repo = _target_repo(tmp_path, passing=False)
    fake = FakeGh({("issue", "list"): "[]"})
    red, ledger = _red(repo, tmp_path, fake, policy=_DenyAll())

    result = red.run()

    assert result.filed == ()
    assert not any(c[:2] == ["issue", "create"] for c in fake.calls)


def test_missing_label_is_created_lazily(tmp_path: Path) -> None:
    repo = _target_repo(tmp_path, passing=False)
    edit_calls = {"n": 0}

    def _edit(argv: list[str]) -> str:
        edit_calls["n"] += 1
        if edit_calls["n"] == 1:
            raise GitHubError("label not found")
        return ""

    fake = FakeGh(
        {
            ("issue", "list"): "[]",
            ("issue", "create"): "https://github.com/owner/name/issues/9\n",
            ("issue", "edit"): _edit,
            ("label", "create"): "",
        }
    )
    red, ledger = _red(repo, tmp_path, fake)

    result = red.run()

    assert result.filed == (9,)
    assert any(c[:2] == ["label", "create"] for c in fake.calls)
    assert TEST_DRIVEN_LABEL in next(c for c in fake.calls if c[:2] == ["label", "create"])
