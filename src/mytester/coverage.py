from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Unit:
    qualname: str  # "pkg.mod:func" or "pkg.mod:Class.method"
    module: str  # "pkg.mod"
    name: str  # bare def name
    file: str  # path relative to the worktree
    lineno: int
    source: str  # the def's own source segment


class CoverageError(RuntimeError):
    pass


def _pythonpath(worktree: Path) -> str:
    # Make a flat or src-layout target package importable without installing it.
    return os.pathsep.join(
        p for p in (str(worktree), str(worktree / "src"), os.environ.get("PYTHONPATH", "")) if p
    )


def measure_coverage(worktree: Path, package: str) -> dict:
    # Shells out to the *target repo's* pytest (MyTester stays dependency-free at
    # runtime; pytest-cov is assumed a dev-dep of the target). JSON report is more
    # robust to parse than --cov text output.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", f"--cov={package}", "--cov-report=json", "-q"],
        cwd=worktree,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _pythonpath(worktree)},
    )
    report = worktree / "coverage.json"
    if not report.exists():
        raise CoverageError(
            f"no coverage.json produced (pytest exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return json.loads(report.read_text(encoding="utf-8"))


def run_suite(worktree: Path) -> subprocess.CompletedProcess:
    # The RED phase: no --cov, just pass/fail -- this stays a separate call from
    # measure_coverage (GAP's own full-suite run) rather than one doing double duty,
    # since the two subcommands never run in the same invocation.
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=worktree,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _pythonpath(worktree)},
    )


# pytest's "short test summary info" section ends the run with one "FAILED
# <nodeid> - <reason>" line per failing test -- already unique per parametrized
# variant, since the node id includes the case -- so it's exactly the grouping
# key RED needs without any extra parsing of the fuller per-test tracebacks.
_FAILED_LINE = re.compile(r"^FAILED (?P<nodeid>\S+)(?: - (?P<reason>.*))?$", re.MULTILINE)


def parse_failures(output: str) -> list[tuple[str, str]]:
    return [(m["nodeid"], (m["reason"] or "").strip()) for m in _FAILED_LINE.finditer(output)]


def run_single_test(worktree: Path, relpath: str, test_name: str) -> subprocess.CompletedProcess:
    # Runs just the newly generated test node, not the whole suite, so an
    # unrelated pre-existing failure can't be mistaken for this generation.
    return subprocess.run(
        [sys.executable, "-m", "pytest", f"{relpath}::{test_name}", "-q"],
        cwd=worktree,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _pythonpath(worktree)},
    )


def total_percent(coverage: dict) -> float:
    return float(coverage.get("totals", {}).get("percent_covered", 0.0))


def _module_name(relpath: str) -> str:
    parts = relpath.removesuffix(".py").split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts)


_Def = ast.FunctionDef | ast.AsyncFunctionDef


def _iter_defs(node: ast.AST, prefix: str = "") -> list[tuple[_Def, str]]:
    out: list[tuple[_Def, str]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            out.append((child, f"{prefix}{child.name}"))
        elif isinstance(child, ast.ClassDef):
            out.extend(_iter_defs(child, f"{prefix}{child.name}."))
    return out


def _skip(name: str) -> bool:
    # __init__, dunders, and single-underscore private are all excluded by "_" prefix.
    return name.startswith("_")


def pick_uncovered_unit(worktree: Path, coverage: dict) -> Unit | None:
    # First uncovered function/method by (file path, then line number) — deterministic.
    for relpath in sorted(coverage.get("files", {})):
        missing = set(coverage["files"][relpath].get("missing_lines", []))
        if not missing:
            continue
        source = (worktree / relpath).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for func, qual in sorted(_iter_defs(tree), key=lambda item: item[0].lineno):
            if _skip(qual.rsplit(".", 1)[-1]):
                continue
            end = func.end_lineno or func.lineno
            if missing.isdisjoint(range(func.lineno, end + 1)):
                continue
            module = _module_name(relpath)
            segment = ast.get_source_segment(source, func) or ""
            return Unit(
                qualname=f"{module}:{qual}",
                module=module,
                name=func.name,
                file=relpath,
                lineno=func.lineno,
                source=segment,
            )
    return None
