# my-tester

Runs pytest with coverage, finds one uncovered unit, and opens a PR adding a
test for it — the smallest full [MyThingsLab](../mythings-core) harness loop
(issue → deterministic pre-work → one Engine call → PR → ledger).

## How it works

1. Check out the target ref in a `isolation.Workspace` git worktree.
2. Run the target repo's `pytest --cov=<package> --cov-report=json` and parse it.
3. Pick the first uncovered function/method (by file, then line), skipping
   `__init__`/dunder/private names. If coverage is 100%, do nothing
   (`outcome=skipped`).
4. Read one existing test from the matching `tests/test_<module>.py` for style.
5. **One Engine call** — generate a single new test function. Against
   `NoopEngine`, a fixed placeholder (`def test_noop_placeholder(): assert True`)
   exercises the read-write-PR path without asserting a real coverage gain.
6. Append the test, then open exactly one PR (head `my-tester/<issue>`). Every
   `git`/`gh` side effect is routed through `Policy.evaluate` (MyGuard) first; a
   `DENY` aborts with `outcome=failure`. Never merges.

## Usage

```bash
mytester run --issue 12 --repo owner/name --base main
mytester run --local-only   # skip the PR; print the generated test
```

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../mythings-core -e ../my-guard -e ".[dev]"
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
