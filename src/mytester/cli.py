from __future__ import annotations

import argparse
from pathlib import Path

from mythings.github import GitHub
from mythings.ledger import Ledger

from mytester.tester import Result, Tester


def _render(result: Result) -> str:
    line = f"{result.outcome}: {result.detail}"
    if result.pr is not None:
        line += f" (PR #{result.pr})"
    if result.test:
        line += f"\n---\n{result.test}"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mytester",
        description="Find one uncovered unit and open a PR adding a test for it.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="test the first uncovered unit")
    run.add_argument("--issue", type=int, help="the backlog issue this run addresses")
    run.add_argument("--repo", help="GitHub slug owner/name for the PR (defaults to the local remote)")  # noqa: E501
    run.add_argument("--base", default="main", help="base branch for the PR")
    run.add_argument("--source", type=Path, default=Path.cwd(), help="local git repo to test")
    run.add_argument("--package", help="importable package to measure (inferred if omitted)")
    run.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    run.add_argument(
        "--local-only",
        action="store_true",
        help="skip the PR; print the generated test (dev loop)",
    )

    args = parser.parse_args(argv)
    tester = Tester(
        repo=args.source,
        ledger=Ledger(args.ledger),
        github=GitHub(args.repo),
        base=args.base,
        package=args.package,
    )
    result = tester.run(args.issue, local_only=args.local_only)
    print(_render(result))
    return 0 if result.outcome != "failure" else 1


if __name__ == "__main__":
    raise SystemExit(main())
