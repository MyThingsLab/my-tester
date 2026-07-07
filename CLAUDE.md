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
- **Quality gate on the generated test:** before committing, the appended test
  is checked for a real (non-trivial) assertion or `pytest.raises` block —
  `assert True`/no-assertion replies are rejected (`outcome=failure`, no PR) —
  then actually executed via `pytest <file>::<test>`. Three outcomes: it
  passes (`outcome=success`, normal PR); it fails on a genuine assertion
  mismatch (`outcome=bug_found` — the PR still opens, titled/labeled as a
  possible bug in the target code rather than a coverage PR, `exit 2` from the
  CLI); or it can't run at all, e.g. a missing import (`outcome=failure`, no
  PR — bad codegen, not a target-code bug). `NoopEngine`'s fixed placeholder is
  exempt from this gate (see above).
- **Name-collision guard:** if the generated test's function name matches one
  already in the target test file, `_append_test` renames it (`test_x` →
  `test_x_2`, ...) before writing, so it can never silently shadow a
  pre-existing test at module scope.
- **Backlog label:** `my-tester`.
