# my-tester — agent instructions

You are developing **my-tester**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `mythings-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** runs pytest with coverage, finds one uncovered unit, and opens a
  PR adding a test for it. The smallest full harness loop (issue → deterministic
  pre-work → one Engine call → PR → ledger).
- **The single Engine call:** required — this is the tool's reason to exist.
  Input: the uncovered unit's fully qualified name, its source, and one existing
  test from the target test file for style; `context={"target": "pkg.mod:func",
  "existing_test_file": "tests/test_mod.py"}`. Output: `EngineResult.text` is one
  new test function's full source (appended, not a whole file). Against
  `NoopEngine` the reply is a fixed placeholder test (`def test_noop_placeholder():
  assert True`) — enough to exercise the read-write-PR path without asserting a
  real coverage gain.
- **Invariants / rules:** every `git`/`gh` side effect is wrapped as
  `Action(kind="bash", ...)` and run through `Policy.evaluate` (MyGuard) first; a
  `DENY` aborts and logs `outcome=failure`, an `ASK` under `in_github_actions()`
  is treated as `DENY`. Opens exactly **one** PR (`github.open_pr`), head
  `my-tester/<issue-number>`, never merges. Never touches files outside the one
  test file it edits/creates. If coverage is already 100%, it does nothing
  (`outcome=skipped`, exit 0) — its only no-op branch, deterministic.
- **Backlog label:** `my-tester`.
