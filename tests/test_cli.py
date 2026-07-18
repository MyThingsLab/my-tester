from __future__ import annotations

import pytest
from mythings.engine import ClaudeCLIEngine

from mytester import cli
from mytester.red import RedResult
from mytester.tester import Result


def _stub_tester(monkeypatch: pytest.MonkeyPatch, result: Result) -> dict:
    captured: dict = {}

    class _Stub:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        def run(self, issue: int | None = None, *, local_only: bool = False) -> Result:
            captured["run"] = {"issue": issue, "local_only": local_only}
            return result

    monkeypatch.setattr(cli, "Tester", _Stub)
    return captured


def test_render_plain_outcome() -> None:
    assert cli._render(Result("skipped", None, None, "fully covered")) == "skipped: fully covered"


def test_render_includes_pr_number() -> None:
    out = cli._render(Result("success", "pkg:f", 7, "opened PR"))
    assert "(PR #7)" in out


def test_render_appends_generated_test_body() -> None:
    out = cli._render(Result("success", "pkg:f", None, "generated", "def test_x():\n    assert 1"))
    assert "---\ndef test_x():" in out


@pytest.mark.parametrize(
    ("outcome", "code"),
    [("success", 0), ("skipped", 0), ("failure", 1), ("bug_found", 2)],
)
def test_exit_code_maps_outcome(monkeypatch: pytest.MonkeyPatch, outcome: str, code: int) -> None:
    _stub_tester(monkeypatch, Result(outcome, None, None, "detail"))
    assert cli.main(["run", "--local-only"]) == code


def test_run_threads_issue_and_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_tester(monkeypatch, Result("success", None, None, "d"))

    cli.main(["run", "--issue", "5", "--local-only"])

    assert captured["run"] == {"issue": 5, "local_only": True}


def test_claude_cli_engine_is_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_tester(monkeypatch, Result("success", None, None, "d"))

    cli.main(["run", "--engine", "claude-cli", "--local-only"])

    assert isinstance(captured["kwargs"]["engine"], ClaudeCLIEngine)


def test_default_engine_is_none_so_tester_uses_its_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_tester(monkeypatch, Result("success", None, None, "d"))

    cli.main(["run", "--local-only"])

    assert captured["kwargs"]["engine"] is None


def test_base_and_package_flags_reach_the_tester(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_tester(monkeypatch, Result("success", None, None, "d"))

    cli.main(["run", "--base", "dev", "--package", "pkg", "--local-only"])

    assert captured["kwargs"]["base"] == "dev"
    assert captured["kwargs"]["package"] == "pkg"


def test_missing_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def test_render_red_plain_outcome() -> None:
    assert cli._render_red(RedResult("clean", detail="suite passing")) == "clean: suite passing"


def test_render_red_includes_filed_issue_numbers() -> None:
    out = cli._render_red(RedResult("filed", filed=(3, 9), detail="2 issue(s) filed"))
    assert "(issues: #3, #9)" in out


def _stub_red(monkeypatch: pytest.MonkeyPatch, result: RedResult) -> dict:
    captured: dict = {}

    class _Stub:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        def run(self) -> RedResult:
            captured["ran"] = True
            return result

    monkeypatch.setattr(cli, "Red", _Stub)
    return captured


def test_red_subcommand_runs_and_prints(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_red(monkeypatch, RedResult("clean", detail="suite passing"))

    assert cli.main(["red"]) == 0
    assert captured["ran"]


def test_red_subcommand_nonzero_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_red(monkeypatch, RedResult("failure", detail="could not run suite"))

    assert cli.main(["red"]) == 1


def test_red_subcommand_threads_base_and_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _stub_red(monkeypatch, RedResult("clean", detail="d"))

    cli.main(["red", "--base", "dev", "--repo", "o/r"])

    assert captured["kwargs"]["base"] == "dev"
    assert captured["kwargs"]["github"].repo == "o/r"
