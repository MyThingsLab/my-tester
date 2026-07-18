from __future__ import annotations

from pathlib import Path

from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.testing import FakeGh, make_git_repo

from mytester.green import MAX_ATTEMPTS, NEEDS_HUMAN_LABEL, Green

_OPS = "def sub(a, b):\n    return a - b\n"
_PASSING_TEST = "from calc.ops import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"
_FAILING_TEST = "from calc.ops import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 999\n"

_NODEID = "tests/test_ops.py::test_sub"


def _target_repo(tmp_path: Path, *, passing: bool) -> Path:
    return make_git_repo(
        tmp_path,
        files={
            "calc/__init__.py": "",
            "calc/ops.py": _OPS,
            "tests/test_ops.py": _PASSING_TEST if passing else _FAILING_TEST,
        },
    ).path


def _seed_red_filed(ledger: Ledger, *, issue: int, nodeid: str = _NODEID) -> None:
    ledger.record(
        tool="mytester", kind="run", outcome="red_filed", detail=nodeid, target=nodeid, issue=issue
    )


def _green(repo: Path, tmp_path: Path, fake: FakeGh) -> tuple[Green, Ledger]:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    green = Green(repo=repo, ledger=ledger, github=GitHub("owner/name", runner=fake), runner=fake)
    return green, ledger


def test_not_found_when_issue_was_never_red_filed(tmp_path: Path) -> None:
    repo = _target_repo(tmp_path, passing=True)
    green, ledger = _green(repo, tmp_path, FakeGh())

    result = green.run(42)

    assert result.outcome == "not_found"


def test_now_passing_is_verified(tmp_path: Path) -> None:
    repo = _target_repo(tmp_path, passing=True)
    green, ledger = _green(repo, tmp_path, FakeGh())
    _seed_red_filed(ledger, issue=9)

    result = green.run(9)

    assert result.outcome == "verified"
    entries = [e for e in ledger if e.outcome == "verified"]
    assert entries[0].data["issue"] == 9
    assert entries[0].data["target"] == _NODEID


def test_still_failing_reopens_the_issue(tmp_path: Path) -> None:
    repo = _target_repo(tmp_path, passing=False)
    fake = FakeGh({("issue", "reopen"): ""})
    green, ledger = _green(repo, tmp_path, fake)
    _seed_red_filed(ledger, issue=9)

    result = green.run(9)

    assert result.outcome == "reopened"
    assert any(c[:2] == ["issue", "reopen"] and "9" in c for c in fake.calls)
    entries = [e for e in ledger if e.outcome == "reopened"]
    assert entries[0].data["issue"] == 9


def test_escalates_to_needs_human_past_max_attempts(tmp_path: Path) -> None:
    repo = _target_repo(tmp_path, passing=False)
    fake = FakeGh({("issue", "edit"): "", ("label", "create"): ""})
    green, ledger = _green(repo, tmp_path, fake)
    _seed_red_filed(ledger, issue=9)
    for _ in range(MAX_ATTEMPTS):
        ledger.record(tool="mytester", kind="run", outcome="reopened", detail=_NODEID, issue=9)

    result = green.run(9)

    assert result.outcome == "needs_human"
    assert any(c[:2] == ["label", "create"] for c in fake.calls)
    label_call = next(c for c in fake.calls if c[:2] == ["issue", "edit"])
    assert NEEDS_HUMAN_LABEL in label_call
    assert not any(c[:2] == ["issue", "reopen"] for c in fake.calls)


def test_uses_the_latest_red_filed_record_for_the_issue(tmp_path: Path) -> None:
    # A stale nodeid from an earlier run for the same issue must not shadow the
    # current one -- the most recent red_filed record wins.
    repo = _target_repo(tmp_path, passing=True)
    green, ledger = _green(repo, tmp_path, FakeGh())
    _seed_red_filed(ledger, issue=9, nodeid="tests/test_ops.py::test_old")
    _seed_red_filed(ledger, issue=9, nodeid=_NODEID)

    result = green.run(9)

    assert result.outcome == "verified"
    assert list(ledger)[-1].data["target"] == _NODEID
