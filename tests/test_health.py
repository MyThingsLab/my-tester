from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger
from mythings.testing import make_git_repo

from mytester.health import Health, parse_totals, quality_counts

_OPS = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"

_CLEAN_TEST = (
    "from calc.ops import add, sub\n\n\n"
    "def test_add():\n    assert add(1, 2) == 3\n\n\n"
    "def test_sub():\n    assert sub(3, 1) == 2\n"
)

_ONE_VACUOUS_TEST = (
    "from calc.ops import add, sub\n\n\n"
    "def test_add():\n    assert add(1, 2) == 3\n\n\n"
    "def test_sub():\n    assert True\n"
)

_ONE_FAILING_TEST = (
    "from calc.ops import add, sub\n\n\n"
    "def test_add():\n    assert add(1, 2) == 3\n\n\n"
    "def test_sub():\n    assert sub(3, 1) == 999\n"
)


def test_parse_totals_reads_the_final_summary_line() -> None:
    output = "F.\nFAILED x - AssertionError\n1 failed, 1 passed in 0.01s\n"
    assert parse_totals(output) == {
        "passed": 1,
        "failed": 1,
        "error": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
    }


def test_parse_totals_handles_all_passing() -> None:
    assert parse_totals("..\n2 passed in 0.01s\n")["passed"] == 2


def _repo(tmp_path: Path, test_source: str) -> Path:
    return make_git_repo(
        tmp_path,
        files={"calc/__init__.py": "", "calc/ops.py": _OPS, "tests/test_ops.py": test_source},
    ).path


def test_quality_counts_all_clean(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _CLEAN_TEST)
    assert quality_counts(repo) == (2, 2)


def test_quality_counts_flags_vacuous_test(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _ONE_VACUOUS_TEST)
    assert quality_counts(repo) == (1, 2)


def test_health_run_reports_totals_scores_and_loop(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _CLEAN_TEST)
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.record(tool="mytester", kind="run", outcome="verified", detail="d", target="t", issue=1)
    ledger.record(tool="mytester", kind="run", outcome="reopened", detail="d", target="t", issue=2)

    result = Health(repo=repo, ledger=ledger).run()

    assert result.totals["passed"] == 2
    assert result.totals["failed"] == 0
    assert result.scores["pass_rate"] == 1.0
    assert result.scores["quality"] == 1.0
    assert result.scores["verified_rate"] == 0.5
    assert result.loop["verified"] == 1
    assert result.loop["reopened"] == 1


def test_health_run_with_failing_suite_and_empty_ledger(tmp_path: Path) -> None:
    repo = _repo(tmp_path, _ONE_FAILING_TEST)
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Health(repo=repo, ledger=ledger).run()

    assert result.totals["failed"] == 1
    assert result.totals["passed"] == 1
    assert result.scores["pass_rate"] == 0.5
    assert result.scores["verified_rate"] is None  # no loop activity at all yet
