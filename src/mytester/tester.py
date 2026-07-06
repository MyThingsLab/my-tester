from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

from myguard import Guard
from mythings.engine import Engine, EngineRequest, NoopEngine
from mythings.github import GitHub, PullRequest
from mythings.isolation import Workspace, in_github_actions
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, Policy

from mytester.coverage import Unit, measure_coverage, pick_uncovered_unit, total_percent

_PLACEHOLDER = "def test_noop_placeholder() -> None:\n    assert True\n"


class PolicyDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class Result:
    outcome: str  # success | skipped | failure
    target: str | None
    pr: int | None
    detail: str
    test: str = ""


def infer_package(tree: Path) -> str:
    roots = [tree / "src", tree]
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if (child / "__init__.py").exists():
                return child.name
    raise RuntimeError(f"could not infer target package under {tree}")


def locate_test_file(tree: Path, unit: Unit) -> tuple[str, str | None]:
    basename = unit.module.rsplit(".", 1)[-1]
    relpath = f"tests/test_{basename}.py"
    path = tree / relpath
    if not path.exists():
        return relpath, None
    source = path.read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        is_func = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        if is_func and node.name.startswith("test"):
            return relpath, ast.get_source_segment(source, node)
    return relpath, None


def _build_prompt(unit: Unit, sample: str | None) -> str:
    parts = [
        f"Write one pytest test for {unit.qualname}.",
        "Source under test:",
        unit.source,
    ]
    if sample:
        parts += ["Match the style of this existing test:", sample]
    return "\n\n".join(parts)


def _append_test(path: Path, new_test: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    combined = f"{existing}\n\n{new_test.strip()}\n" if existing else f"{new_test.strip()}\n"
    ast.parse(combined)  # the edited file must stay importable
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(combined, encoding="utf-8")


class Tester:
    __test__ = False  # not a pytest test class despite the name

    def __init__(
        self,
        *,
        repo: str | Path,
        ledger: Ledger,
        github: GitHub,
        base: str = "main",
        package: str | None = None,
        engine: Engine | None = None,
        policy: Policy | None = None,
    ) -> None:
        self.repo = Path(repo)
        self.ledger = ledger
        self.github = github
        self.base = base
        self.package = package
        self.engine: Engine = engine or NoopEngine(reply=_PLACEHOLDER)
        self.policy: Policy = policy or Guard()

    def run(self, issue: int | None = None, *, local_only: bool = False) -> Result:
        try:
            with Workspace(self.repo, self.base) as tree:
                return self._run_in(tree, issue, local_only)
        except PolicyDenied as denied:
            return self._fail(str(denied))

    def _run_in(self, tree: Path, issue: int | None, local_only: bool) -> Result:
        package = self.package or infer_package(tree)
        coverage = measure_coverage(tree, package)
        before = total_percent(coverage)
        unit = pick_uncovered_unit(tree, coverage)
        if unit is None:
            self._record("skipped", None, "fully covered", before, before, None)
            return Result("skipped", None, None, "fully covered")

        relpath, sample = locate_test_file(tree, unit)
        new_test = self.engine.run(
            EngineRequest(
                prompt=_build_prompt(unit, sample),
                context={"target": unit.qualname, "existing_test_file": relpath},
            )
        ).text.strip() or _PLACEHOLDER
        _append_test(tree / relpath, new_test)

        if local_only:
            self._record("success", unit.qualname, unit.qualname, before, None, None)
            detail = f"generated test for {unit.qualname}"
            return Result("success", unit.qualname, None, detail, new_test)

        pr = self._open_pr(tree, issue, unit, relpath)
        self._record("success", unit.qualname, unit.qualname, before, None, pr.number)
        detail = f"opened PR for {unit.qualname}"
        return Result("success", unit.qualname, pr.number, detail, new_test)

    def _open_pr(self, tree: Path, issue: int | None, unit: Unit, relpath: str) -> PullRequest:
        branch = f"my-tester/{issue}" if issue is not None else "my-tester/auto"
        self._git(tree, ["checkout", "-b", branch])
        self._git(tree, ["add", relpath])
        self._git(tree, ["commit", "-m", f"test: cover {unit.qualname}"])
        self._git(tree, ["push", "-u", "origin", branch])
        title = f"test: cover {unit.qualname}"
        body = f"Adds a test for `{unit.qualname}`."
        if issue is not None:
            body += f"\n\nCloses #{issue}."
        self._guard(f"gh pr create --head {branch} --base {self.base}")
        return self.github.open_pr(title=title, body=body, base=self.base, head=branch)

    def _git(self, tree: Path, argv: list[str]) -> None:
        self._guard("git " + " ".join(argv))
        proc = subprocess.run(["git", "-C", str(tree), *argv], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(argv)} failed: {proc.stderr.strip()}")

    def _guard(self, command: str) -> None:
        result = self.policy.evaluate(Action(kind="bash", payload={"command": command}))
        if result.under(unattended=in_github_actions()) is not Decision.ALLOW:
            raise PolicyDenied(f"policy blocked: {command} ({result.reason or result.decision})")

    def _fail(self, detail: str) -> Result:
        self._record("failure", None, detail, None, None, None)
        return Result("failure", None, None, detail)

    def _record(
        self,
        outcome: str,
        target: str | None,
        detail: str,
        before: float | None,
        after: float | None,
        pr: int | None,
    ) -> None:
        self.ledger.record(
            tool="mytester",
            kind="run",
            outcome=outcome,
            detail=detail,
            target=target,
            coverage_before=before,
            coverage_after=after,
            pr=pr,
        )
