from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mythings.github import GitHub, Runner, _gh
from mythings.isolation import Workspace
from mythings.ledger import Ledger

from mytester.coverage import run_single_test

# Mirrors my-fleet's dispatch-loop cap: a permanently-broken fix must not loop
# forever, it escalates to a human past this many failed re-verifications.
MAX_ATTEMPTS = 3

NEEDS_HUMAN_LABEL = "needs-human"
_NEEDS_HUMAN_DESCRIPTION = f"A test-driven fix failed verification {MAX_ATTEMPTS} times"
_NEEDS_HUMAN_COLOR = "d93f0b"


@dataclass(frozen=True)
class GreenResult:
    outcome: str  # verified | reopened | needs_human | not_found
    detail: str = ""


def _repo_argv(argv: list[str], repo: str | None) -> list[str]:
    return [*argv, "--repo", repo] if repo else argv


class Green:
    def __init__(
        self,
        *,
        repo: str | Path,
        ledger: Ledger,
        github: GitHub,
        base: str = "main",
        runner: Runner = _gh,
    ) -> None:
        self.repo = Path(repo)
        self.ledger = ledger
        self.github = github
        self.base = base
        self.runner = runner

    def run(self, issue: int) -> GreenResult:
        nodeid = self._filed_nodeid(issue)
        if nodeid is None:
            return GreenResult("not_found", detail=f"no red-filed record for #{issue}")

        relpath, _, test_name = nodeid.partition("::")
        with Workspace(self.repo, self.base) as tree:
            proc = run_single_test(tree, relpath, test_name)

        if proc.returncode == 0:
            self.ledger.record(
                tool="mytester",
                kind="run",
                outcome="verified",
                detail=nodeid,
                target=nodeid,
                issue=issue,
            )
            return GreenResult("verified", detail=f"{nodeid} now passes")

        return self._escalate_or_reopen(issue, nodeid)

    def _filed_nodeid(self, issue: int) -> str | None:
        # RED's own ledger record is the source of truth for which node id a
        # test-driven issue concerns -- not the PR's own "Closes #N" claim,
        # per the fleet-wide principle of auditing with git/gh/ledger state.
        for entry in reversed(list(self.ledger)):
            if entry.tool == "mytester" and entry.outcome == "red_filed":
                if entry.data.get("issue") == issue:
                    return entry.data.get("target")
        return None

    def _attempts(self, issue: int) -> int:
        return sum(
            1
            for entry in self.ledger
            if entry.tool == "mytester"
            and entry.outcome in ("reopened", "needs_human")
            and entry.data.get("issue") == issue
        )

    def _escalate_or_reopen(self, issue: int, nodeid: str) -> GreenResult:
        attempts = self._attempts(issue)
        if attempts >= MAX_ATTEMPTS:
            self._ensure_needs_human_label()
            self.github.add_labels(issue, [NEEDS_HUMAN_LABEL])
            self.ledger.record(
                tool="mytester",
                kind="run",
                outcome="needs_human",
                detail=nodeid,
                target=nodeid,
                issue=issue,
            )
            return GreenResult(
                "needs_human", detail=f"{nodeid} still failing after {attempts} attempts"
            )

        self.runner(_repo_argv(["issue", "reopen", str(issue)], self.github.repo))
        self.ledger.record(
            tool="mytester",
            kind="run",
            outcome="reopened",
            detail=nodeid,
            target=nodeid,
            issue=issue,
        )
        return GreenResult("reopened", detail=f"{nodeid} still failing -- reopened #{issue}")

    def _ensure_needs_human_label(self) -> None:
        argv = _repo_argv(
            [
                "label",
                "create",
                NEEDS_HUMAN_LABEL,
                "--description",
                _NEEDS_HUMAN_DESCRIPTION,
                "--color",
                _NEEDS_HUMAN_COLOR,
                "--force",
            ],
            self.github.repo,
        )
        self.runner(argv)
