from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

from mythings.isolation import Workspace
from mythings.ledger import Ledger

from mytester.coverage import run_suite
from mytester.tester import _has_real_assertion

# Every pytest run ends with a one-line summary like "3 failed, 40 passed in
# 6.21s" or "1 error in 0.05s" -- exactly the totals a health record needs,
# already produced by the suite run health shares with RED.
_SUMMARY_COUNT = re.compile(r"(\d+) (passed|failed|error|skipped|xfailed|xpassed)")

_IGNORED_DIRS = {".venv", ".git", "node_modules"}

# Ledger outcomes counted into the `loop` scorecard, across every phase
# (GAP's own success/skipped/failure/bug_found, plus RED/GREEN's).
_LOOP_OUTCOMES = (
    "success",
    "skipped",
    "failure",
    "bug_found",
    "clean",
    "red_filed",
    "verified",
    "reopened",
    "needs_human",
)


@dataclass(frozen=True)
class HealthResult:
    totals: dict[str, int]
    loop: dict[str, int]
    scores: dict[str, float | None]

    def to_json(self) -> str:
        return json.dumps(
            {"totals": self.totals, "loop": self.loop, "scores": self.scores},
            indent=2,
            sort_keys=True,
        )


def parse_totals(output: str) -> dict[str, int]:
    lines = [line for line in output.splitlines() if line.strip()]
    summary = lines[-1] if lines else ""
    counts = dict.fromkeys(("passed", "failed", "error", "skipped", "xfailed", "xpassed"), 0)
    for n, kind in _SUMMARY_COUNT.findall(summary):
        counts[kind] = int(n)
    return counts


def _test_functions(source: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test")
    ]


def quality_counts(worktree: Path) -> tuple[int, int]:
    # (clean, total) test functions across the whole suite, "clean" meaning
    # _has_real_assertion's charter: not a vacuous assert-True/no-assertion body.
    clean = 0
    total = 0
    for path in sorted(worktree.rglob("test_*.py")):
        if _IGNORED_DIRS & set(path.relative_to(worktree).parts):
            continue
        source = path.read_text(encoding="utf-8")
        for func in _test_functions(source):
            total += 1
            segment = ast.get_source_segment(source, func) or ""
            if _has_real_assertion(segment):
                clean += 1
    return clean, total


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


class Health:
    def __init__(self, *, repo: str | Path, ledger: Ledger, base: str = "main") -> None:
        self.repo = Path(repo)
        self.ledger = ledger
        self.base = base

    def run(self) -> HealthResult:
        with Workspace(self.repo, self.base) as tree:
            proc = run_suite(tree)
            totals = parse_totals(proc.stdout + proc.stderr)
            clean, total_tests = quality_counts(tree)

        loop = dict.fromkeys(_LOOP_OUTCOMES, 0)
        for entry in self.ledger:
            if entry.tool == "mytester" and entry.outcome in loop:
                loop[entry.outcome] += 1

        scores: dict[str, float | None] = {
            "pass_rate": _rate(
                totals["passed"], totals["passed"] + totals["failed"] + totals["error"]
            ),
            "quality": _rate(clean, total_tests),
            "verified_rate": _rate(
                loop["verified"], loop["verified"] + loop["reopened"] + loop["needs_human"]
            ),
        }
        return HealthResult(totals=totals, loop=loop, scores=scores)


def write(result: HealthResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.to_json() + "\n", encoding="utf-8")
