from __future__ import annotations

from pathlib import Path

from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult

from conftest import FakeRunner, make_target_repo
from mytester.tester import Tester


def _tester(repo: Path, tmp_path: Path, **kw) -> tuple[Tester, FakeRunner, Ledger]:
    fake = FakeRunner()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    tester = Tester(repo=repo, ledger=ledger, github=GitHub("owner/name", runner=fake), **kw)
    return tester, fake, ledger


def test_happy_path_opens_pr_for_uncovered_unit(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path, fully_covered=False)
    tester, fake, ledger = _tester(repo, tmp_path)

    result = tester.run(issue=5)

    assert result.outcome == "success"
    assert result.target == "calc.ops:sub"  # first uncovered, non-private unit
    assert result.pr == 7
    assert any(c[:2] == ["pr", "create"] for c in fake.calls)

    entry = list(ledger)[0]
    assert entry.kind == "run"
    assert entry.outcome == "success"
    assert entry.data["target"] == "calc.ops:sub"
    assert entry.data["pr"] == 7
    assert entry.data["coverage_before"] < 100.0


def test_fully_covered_is_a_noop(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path, fully_covered=True)
    tester, fake, ledger = _tester(repo, tmp_path)

    result = tester.run(issue=1)

    assert result.outcome == "skipped"
    assert result.target is None
    assert result.pr is None
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)  # no PR created
    assert list(ledger)[0].outcome == "skipped"
    assert list(ledger)[0].detail == "fully covered"


def test_local_only_prints_test_without_pr(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path, fully_covered=False)
    tester, fake, ledger = _tester(repo, tmp_path)

    result = tester.run(issue=None, local_only=True)

    assert result.outcome == "success"
    assert result.pr is None
    assert "test_noop_placeholder" in result.test  # NoopEngine placeholder
    assert fake.calls == []
    assert list(ledger)[0].data["pr"] is None


class _DenyAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY, reason="locked down", rule="deny_all")


def test_policy_deny_aborts_with_failure(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path, fully_covered=False)
    tester, fake, ledger = _tester(repo, tmp_path, policy=_DenyAll())

    result = tester.run(issue=5)

    assert result.outcome == "failure"
    assert result.pr is None
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)  # denied before the PR
    assert list(ledger)[0].outcome == "failure"
