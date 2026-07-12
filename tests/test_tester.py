from __future__ import annotations

from pathlib import Path

from mythings.engine import EngineRequest, EngineResult
from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult

from conftest import FakeGh, fake_gh, make_target_repo
from mytester.tester import Tester, _append_test, _has_real_assertion, _strip_code_fence


def test_strip_code_fence_removes_language_tagged_fence() -> None:
    text = "```python\ndef test_x():\n    assert True\n```"
    assert _strip_code_fence(text) == "def test_x():\n    assert True"


def test_strip_code_fence_removes_bare_fence() -> None:
    text = "```\ndef test_x():\n    assert True\n```"
    assert _strip_code_fence(text) == "def test_x():\n    assert True"


def test_strip_code_fence_passes_through_unfenced_text() -> None:
    text = "def test_x():\n    assert True"
    assert _strip_code_fence(text) == text


def test_has_real_assertion_accepts_pytest_raises() -> None:
    src = "def test_x():\n    with pytest.raises(ValueError):\n        f()"
    assert _has_real_assertion(src)


def test_has_real_assertion_rejects_trivial_assert_true() -> None:
    src = "def test_x():\n    assert True"
    assert not _has_real_assertion(src)


def test_has_real_assertion_rejects_body_with_no_assertion() -> None:
    src = "def test_x():\n    f()"
    assert not _has_real_assertion(src)


def test_append_test_renames_generated_test_colliding_with_existing_one(tmp_path: Path) -> None:
    # A generated test reusing an existing test's name would otherwise be a
    # later `def` at module scope, silently shadowing the original -- pytest
    # would never collect it again, with no error anywhere in the pipeline.
    path = tmp_path / "tests" / "test_ops.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "def test_sub():\n    assert True  # pre-existing test, must survive\n",
        encoding="utf-8",
    )

    written = _append_test(path, "def test_sub():\n    assert 1 == 1\n")

    combined = path.read_text(encoding="utf-8")
    assert combined.count("def test_sub()") == 1  # original keeps its name, isn't overwritten
    assert "must survive" in combined  # original body is untouched
    assert "def test_sub_2()" in combined  # new test appended under a renamed, unique name
    assert "def test_sub_2()" in written  # caller sees the renamed source, not the original


def _tester(repo: Path, tmp_path: Path, **kw) -> tuple[Tester, FakeGh, Ledger]:
    fake = fake_gh()
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


class _SpyEngine:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def run(self, request: EngineRequest) -> EngineResult:
        return EngineResult(text=self.reply)


def test_fenced_engine_reply_is_stripped_before_appending(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path, fully_covered=False)
    fenced = (
        "```python\nfrom calc.ops import sub\n\n\ndef test_sub():\n"
        "    assert sub(3, 1) == 2\n```"
    )
    tester, fake, ledger = _tester(repo, tmp_path, engine=_SpyEngine(fenced))

    result = tester.run(issue=5, local_only=True)

    assert result.outcome == "success"
    assert "```" not in result.test
    assert result.test == "from calc.ops import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2"


def test_vacuous_generated_test_is_rejected(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path, fully_covered=False)
    trivial = "def test_sub():\n    assert True"
    tester, fake, ledger = _tester(repo, tmp_path, engine=_SpyEngine(trivial))

    result = tester.run(issue=5)

    assert result.outcome == "failure"
    assert "no real assertion" in result.detail
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)
    assert list(ledger)[0].outcome == "failure"


def test_failing_generated_test_opens_pr_flagged_as_bug_found(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path, fully_covered=False)
    wrong = "from calc.ops import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 999"
    tester, fake, ledger = _tester(repo, tmp_path, engine=_SpyEngine(wrong))

    result = tester.run(issue=5)

    assert result.outcome == "bug_found"
    assert result.pr == 7
    pr_call = next(c for c in fake.calls if c[:2] == ["pr", "create"])
    title = pr_call[pr_call.index("--title") + 1]
    body = pr_call[pr_call.index("--body") + 1]
    assert "possible bug" in title
    assert "investigate before merging" in body
    assert list(ledger)[0].outcome == "bug_found"


def test_test_with_no_import_is_rejected_not_flagged_as_bug(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path, fully_covered=False)
    broken = "def test_sub():\n    assert sub(3, 1) == 2"  # sub never imported
    tester, fake, ledger = _tester(repo, tmp_path, engine=_SpyEngine(broken))

    result = tester.run(issue=5)

    assert result.outcome == "failure"
    assert "could not run" in result.detail
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)


def test_long_test_name_missing_import_is_still_rejected_not_a_bug(tmp_path: Path) -> None:
    # pytest's "short test summary info" line is elided by terminal-width
    # truncation once the node id itself is long enough -- a realistic
    # generated test name is long enough to drop the "- NameError: ..."
    # suffix entirely, so classification must not depend on that line.
    repo = make_target_repo(tmp_path, fully_covered=False)
    broken = (
        "def test_sub_handles_negative_and_boundary_inputs_correctly_for_real():\n"
        "    assert sub(3, 1) == 2"
    )  # sub never imported
    tester, fake, ledger = _tester(repo, tmp_path, engine=_SpyEngine(broken))

    result = tester.run(issue=5)

    assert result.outcome == "failure"
    assert "could not run" in result.detail
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)


def test_assertion_message_mentioning_nameerror_is_still_a_bug_found(tmp_path: Path) -> None:
    # A failing assertion whose *message* happens to contain the word
    # "NameError" must not be misread as the test itself having a missing
    # import -- pytest's short summary prefixes real NameErrors with
    # "NameError:", not "AssertionError:", so this should still surface as a
    # real (if fabricated, for this test) bug in the target code.
    repo = make_target_repo(tmp_path, fully_covered=False)
    wrong = (
        "from calc.ops import sub\n\n\n"
        'def test_sub():\n    assert sub(3, 1) == 999, "sub raised NameError in legacy branch"\n'
    )
    tester, fake, ledger = _tester(repo, tmp_path, engine=_SpyEngine(wrong))

    result = tester.run(issue=5)

    assert result.outcome == "bug_found"
    assert result.pr == 7


def test_real_engine_empty_reply_is_rejected_not_silently_success(tmp_path: Path) -> None:
    # A non-Noop engine that fails to produce a reply falls back to the same
    # placeholder text NoopEngine uses -- but only NoopEngine is exempt from
    # the quality gate, so this must be rejected, not treated as a success.
    repo = make_target_repo(tmp_path, fully_covered=False)
    tester, fake, ledger = _tester(repo, tmp_path, engine=_SpyEngine(""))

    result = tester.run(issue=5)

    assert result.outcome == "failure"
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)


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
