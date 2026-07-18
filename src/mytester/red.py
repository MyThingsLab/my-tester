from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from myguard import Guard
from mythings.github import GitHub, GitHubError, Issue, Runner, _gh
from mythings.isolation import Workspace, in_github_actions
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, Policy

from mytester.coverage import parse_failures, run_suite

# A GAP-drafted test earns coverage; a RED-filed issue reports a suite already
# failing on its base branch -- distinct label so `mytester green` (and anyone
# skimming the backlog) can tell which loop phase an issue belongs to.
TEST_DRIVEN_LABEL = "test-driven"
_LABEL_DESCRIPTION = "A currently-failing test, filed by mytester red"
_LABEL_COLOR = "e11d21"


@dataclass(frozen=True)
class RedResult:
    outcome: str  # clean | filed | failure
    filed: tuple[int, ...] = field(default_factory=tuple)
    detail: str = ""


def _issue_title(nodeid: str) -> str:
    return f"test: {nodeid} is failing"


def _ensure_label(runner: Runner, repo: str | None) -> None:
    argv = [
        "label",
        "create",
        TEST_DRIVEN_LABEL,
        "--description",
        _LABEL_DESCRIPTION,
        "--color",
        _LABEL_COLOR,
        "--force",
    ]
    if repo:
        argv += ["--repo", repo]
    runner(argv)


class Red:
    def __init__(
        self,
        *,
        repo: str | Path,
        ledger: Ledger,
        github: GitHub,
        base: str = "main",
        policy: Policy | None = None,
        runner: Runner = _gh,
    ) -> None:
        self.repo = Path(repo)
        self.ledger = ledger
        self.github = github
        self.base = base
        self.policy: Policy = policy or Guard()
        self.runner = runner

    def run(self) -> RedResult:
        with Workspace(self.repo, self.base) as tree:
            return self._run_in(tree)

    def _run_in(self, tree: Path) -> RedResult:
        proc = run_suite(tree)
        failures = parse_failures(proc.stdout + proc.stderr)
        if not failures:
            self.ledger.record(tool="mytester", kind="run", outcome="clean", detail="suite passing")
            return RedResult("clean", detail="suite passing")

        already_open = self._open_titles()
        filed: list[int] = []
        for nodeid, reason in failures:
            title = _issue_title(nodeid)
            if title in already_open:
                continue
            issue = self._file(title, nodeid, reason)
            if issue is not None:
                filed.append(issue.number)

        skipped = len(failures) - len(filed)
        detail = f"{len(filed)} issue(s) filed, {skipped} already open"
        return RedResult("filed" if filed else "clean", filed=tuple(filed), detail=detail)

    def _open_titles(self) -> set[str]:
        try:
            issues = self.github.list_issues(labels=[TEST_DRIVEN_LABEL], state="open", limit=100)
        except GitHubError:
            return set()
        return {i.title for i in issues}

    def _file(self, title: str, nodeid: str, reason: str) -> Issue | None:
        action = Action(kind="issue-create", payload={"title": title, "label": TEST_DRIVEN_LABEL})
        gate = self.policy.evaluate(action).under(unattended=in_github_actions())
        if gate is not Decision.ALLOW:
            return None

        body = f"`{nodeid}` is failing on `{self.base}`.\n\n```\n{reason or 'see CI output'}\n```"
        created = self.github.create_issue(title=title, body=body)
        try:
            self.github.add_labels(created.number, [TEST_DRIVEN_LABEL])
        except GitHubError:
            # First failure filed against a fresh repo: the label doesn't exist yet.
            _ensure_label(self.runner, self.github.repo)
            self.github.add_labels(created.number, [TEST_DRIVEN_LABEL])

        self.ledger.record(
            tool="mytester",
            kind="run",
            outcome="red_filed",
            detail=title,
            target=nodeid,
            issue=created.number,
        )
        return created
