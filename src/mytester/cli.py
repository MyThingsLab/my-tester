from __future__ import annotations

import argparse
from pathlib import Path

from mythings.engine import ClaudeCLIEngine, Engine, NoopEngine
from mythings.github import GitHub
from mythings.ledger import Ledger

from mytester.green import Green, GreenResult
from mytester.red import Red, RedResult
from mytester.tester import Result, Tester

_ENGINES: dict[str, type[Engine]] = {"noop": NoopEngine, "claude-cli": ClaudeCLIEngine}


def _render(result: Result) -> str:
    line = f"{result.outcome}: {result.detail}"
    if result.pr is not None:
        line += f" (PR #{result.pr})"
    if result.test:
        line += f"\n---\n{result.test}"
    return line


def _render_red(result: RedResult) -> str:
    line = f"{result.outcome}: {result.detail}"
    if result.filed:
        line += f" (issues: {', '.join(f'#{n}' for n in result.filed)})"
    return line


def _render_green(result: GreenResult) -> str:
    return f"{result.outcome}: {result.detail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mytester",
        description="Find one uncovered unit and open a PR adding a test for it.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="test the first uncovered unit")
    run.add_argument("--issue", type=int, help="the backlog issue this run addresses")
    run.add_argument(
        "--repo", help="GitHub slug owner/name for the PR (defaults to the local remote)"
    )  # noqa: E501
    run.add_argument("--base", default="main", help="base branch for the PR")
    run.add_argument("--source", type=Path, default=Path.cwd(), help="local git repo to test")
    run.add_argument("--package", help="importable package to measure (inferred if omitted)")
    run.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    run.add_argument(
        "--local-only",
        action="store_true",
        help="skip the PR; print the generated test (dev loop)",
    )
    run.add_argument(
        "--engine",
        choices=sorted(_ENGINES),
        default="noop",
        help="Engine backend for writing the test (default: noop — a fixed placeholder test)",
    )

    red = sub.add_parser("red", help="run the suite, file one issue per distinct failure")
    red.add_argument("--repo", help="GitHub slug owner/name (defaults to the local remote)")
    red.add_argument("--base", default="main", help="base branch to run the suite against")
    red.add_argument("--source", type=Path, default=Path.cwd(), help="local git repo to test")
    red.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))

    green = sub.add_parser("green", help="verify a closed test-driven issue's fix")
    green.add_argument("issue", type=int, help="the test-driven issue to re-verify")
    green.add_argument("--repo", help="GitHub slug owner/name (defaults to the local remote)")
    green.add_argument("--base", default="main", help="base branch to run the test against")
    green.add_argument("--source", type=Path, default=Path.cwd(), help="local git repo to test")
    green.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))

    args = parser.parse_args(argv)
    if args.cmd == "red":
        red_result = Red(
            repo=args.source,
            ledger=Ledger(args.ledger),
            github=GitHub(args.repo),
            base=args.base,
        ).run()
        print(_render_red(red_result))
        return 1 if red_result.outcome == "failure" else 0

    if args.cmd == "green":
        green_result = Green(
            repo=args.source,
            ledger=Ledger(args.ledger),
            github=GitHub(args.repo),
            base=args.base,
        ).run(args.issue)
        print(_render_green(green_result))
        return 1 if green_result.outcome in ("needs_human", "not_found") else 0

    tester = Tester(
        repo=args.source,
        ledger=Ledger(args.ledger),
        github=GitHub(args.repo),
        base=args.base,
        package=args.package,
        engine=_ENGINES[args.engine]() if args.engine != "noop" else None,
    )
    result = tester.run(args.issue, local_only=args.local_only)
    print(_render(result))
    # bug_found gets its own nonzero code, distinct from a plain tool failure,
    # so automation chaining on exit status can't mistake a discovered bug
    # for an ordinary success but can still tell it apart from mytester itself
    # having failed to do its job.
    if result.outcome == "failure":
        return 1
    if result.outcome == "bug_found":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
