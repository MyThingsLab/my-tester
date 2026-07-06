from __future__ import annotations

import ast
import json
import os
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


def measure_coverage(worktree: Path, package: str) -> dict:
    # Shells out to the *target repo's* pytest (MyTester stays dependency-free at
    # runtime; pytest-cov is assumed a dev-dep of the target). JSON report is more
    # robust to parse than --cov text output.
    # Make a flat or src-layout target package importable without installing it.
    pythonpath = os.pathsep.join(
        p for p in (str(worktree), str(worktree / "src"), os.environ.get("PYTHONPATH", "")) if p
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", f"--cov={package}", "--cov-report=json", "-q"],
        cwd=worktree,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": pythonpath},
    )
    report = worktree / "coverage.json"
    if not report.exists():
        raise CoverageError(
            f"no coverage.json produced (pytest exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return json.loads(report.read_text(encoding="utf-8"))


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
