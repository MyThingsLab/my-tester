from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from myguard import Guard
from mythings.engine import Engine, EngineRequest, NoopEngine
from mythings.github import GitHub, PullRequest
from mythings.isolation import Workspace, in_github_actions
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, Policy

from mytester.coverage import (
    Unit,
    measure_coverage,
    pick_uncovered_unit,
    run_single_test,
    total_percent,
)

_PLACEHOLDER = "def test_noop_placeholder() -> None:\n    assert True\n"

_SYSTEM = (
    "You write pytest tests that earn their coverage: assert specific, concrete "
    "behavior of the function under test rather than merely calling it. Choose "
    "inputs that would catch a plausible real bug -- an off-by-one, a swapped "
    "operand, a wrong comparison, an unhandled edge case (empty, zero, negative, "
    "or boundary input) -- rather than only the most obvious happy-path call. "
    "Never write a test whose only assertion is trivially true, and never assert "
    "that a value is merely not-None without pinning down what it must equal. If "
    "the function can raise, prefer asserting the exception over re-covering a "
    "case the existing tests already exercise."
)


class PolicyDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class Result:
    outcome: str  # success | skipped | failure | bug_found
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
        "Reply with only the raw Python source for the test function — no markdown "
        "code fences, no explanation, nothing but the code.",
        "Source under test:",
        unit.source,
    ]
    if sample:
        parts += ["Match the style of this existing test:", sample]
    return "\n\n".join(parts)


def _strip_code_fence(text: str) -> str:
    # Defense in depth: the prompt asks for raw code, but a model reply can
    # still arrive wrapped in a ```python ... ``` fence. Strip one if present.
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[-1].strip() == "```":
        lines = lines[1:-1]
    else:
        lines = lines[1:]
    return "\n".join(lines)


def _first_test_name(test_src: str) -> str | None:
    try:
        tree = ast.parse(test_src)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            return node.name
    return None


def _is_trivial_assert(node: ast.Assert) -> bool:
    test = node.test
    return isinstance(test, ast.Constant) and bool(test.value)


def _is_raises_context(expr: ast.expr) -> bool:
    if not isinstance(expr, ast.Call):
        return False
    func = expr.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return name == "raises"


def _has_real_assertion(test_src: str) -> bool:
    try:
        tree = ast.parse(test_src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and not _is_trivial_assert(node):
            return True
        if isinstance(node, ast.With) and any(
            _is_raises_context(item.context_expr) for item in node.items
        ):
            return True
    return False


@dataclass(frozen=True)
class Validation:
    verdict: str  # passed | failed | rejected | errored
    output: str = ""


# pytest's "short test summary info" reports an uncaught non-assert exception
# as "FAILED <nodeid> - <ExceptionType>: ...". A plain `assert` failure never
# starts that way (it's either "- assert <expr>" from rewriting, or, with an
# explicit assert message, "- AssertionError: <message>") -- so anchoring on
# this line, rather than searching the whole output, tells apart a generated
# test that forgot an import from one whose assertion message merely mentions
# "NameError" in passing.
_NAME_ERROR_SUMMARY = re.compile(r"^FAILED \S+ - NameError:", re.MULTILINE)


def _validate(tree: Path, relpath: str, new_test: str) -> Validation:
    name = _first_test_name(new_test)
    if name is None or not _has_real_assertion(new_test):
        return Validation("rejected")
    proc = run_single_test(tree, relpath, name)
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode == 0:
        return Validation("passed", output)
    if proc.returncode == 1 and not _NAME_ERROR_SUMMARY.search(output):
        return Validation("failed", output)  # ran, assertion failed -- possible real bug
    return Validation("errored", output)  # collection/usage/internal error -- bad generated code


def _existing_test_names(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _dedupe_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    suffix = 2
    while f"{name}_{suffix}" in taken:
        suffix += 1
    return f"{name}_{suffix}"


def _append_test(path: Path, new_test: str) -> str:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    # Python keeps only the last `def` binding at module scope, so a generated
    # test that happens to reuse an existing test's name would silently shadow
    # it -- the old test becomes dead code pytest never collects again.
    name = _first_test_name(new_test)
    if name is not None:
        unique = _dedupe_name(name, _existing_test_names(existing))
        if unique != name:
            new_test = re.sub(rf"\bdef {re.escape(name)}\b", f"def {unique}", new_test, count=1)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    combined = f"{existing}\n\n{new_test.strip()}\n" if existing else f"{new_test.strip()}\n"
    ast.parse(combined)  # the edited file must stay importable
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(combined, encoding="utf-8")
    return new_test


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
        reply = self.engine.run(
            EngineRequest(
                system=_SYSTEM,
                prompt=_build_prompt(unit, sample),
                context={"target": unit.qualname, "existing_test_file": relpath},
            )
        ).text.strip()
        new_test = _strip_code_fence(reply).strip() if reply else _PLACEHOLDER
        new_test = _append_test(tree / relpath, new_test)

        # The fixed NoopEngine placeholder only exercises the read-write-PR
        # path (per design); it never asserts real coverage gain, so it's
        # exempt from the quality gate applied to genuine model output. A
        # real engine that fails to reply (and thus also falls back to the
        # placeholder text) is not exempt -- it should be rejected like any
        # other assertion-free test, not silently treated as a success.
        if isinstance(self.engine, NoopEngine):
            validation = Validation("passed")
        else:
            validation = _validate(tree, relpath, new_test)
        if validation.verdict in ("rejected", "errored"):
            reason = (
                "had no real assertion" if validation.verdict == "rejected" else "could not run"
            )
            detail = f"generated test for {unit.qualname} {reason}"
            self._record("failure", unit.qualname, detail, before, None, None)
            return Result("failure", unit.qualname, None, detail, new_test)

        bug_found = validation.verdict == "failed"
        outcome = "bug_found" if bug_found else "success"
        suffix = " -- test fails, possible bug" if bug_found else ""

        if local_only:
            self._record(outcome, unit.qualname, unit.qualname, before, None, None)
            detail = f"generated test for {unit.qualname}{suffix}"
            return Result(outcome, unit.qualname, None, detail, new_test)

        pr = self._open_pr(
            tree, issue, unit, relpath, bug_found=bug_found, output=validation.output
        )
        self._record(outcome, unit.qualname, unit.qualname, before, None, pr.number)
        detail = f"opened PR for {unit.qualname}{suffix}"
        return Result(outcome, unit.qualname, pr.number, detail, new_test)

    def _open_pr(
        self,
        tree: Path,
        issue: int | None,
        unit: Unit,
        relpath: str,
        *,
        bug_found: bool,
        output: str,
    ) -> PullRequest:
        branch = f"my-tester/{issue}" if issue is not None else "my-tester/auto"
        commit_subject = (
            f"test: {unit.qualname} fails" if bug_found else f"test: cover {unit.qualname}"
        )
        self._git(tree, ["checkout", "-b", branch])
        self._git(tree, ["add", relpath])
        self._git(tree, ["commit", "-m", commit_subject])
        self._git(tree, ["push", "-u", "origin", branch])
        if bug_found:
            title = f"test: {unit.qualname} fails -- possible bug"
            body = (
                f"The generated test for `{unit.qualname}` fails against the current "
                "implementation. This may indicate a real bug -- investigate before merging.\n\n"
                f"```\n{output[-2000:]}\n```"
            )
            if issue is not None:
                body += f"\n\nRelated to #{issue} -- do not close until the failure is resolved."
        else:
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
