# What counts as a weakened test

CLAUDE.md: never weaken an existing test unless its spec changed. In a diff of `tests/`, each of
these is a weakening unless the change's delta spec (`openspec/changes/<name>/specs/`) changed
the behaviour the test pins:

- An `assert` removed, or relaxed (`==` → `in`, exact count → `>=`, specific error type → `Exception`).
- `pytest.mark.skip`, `skipif`, `xfail`, or `pytest.skip(...)` added to an existing test.
- An existing test function deleted or renamed away without a replacement covering its scenario.
- An expected value edited to match new output (the "update the snapshot" move).
- A PII column dropped from a PII test's expected-stripped set, or a mock widened so a guard
  never fires.

Not weakening: moving or splitting a test with the same assertions, adding tests, tightening
assertions, or fixing a test that asserted a bug the spec says is wrong (cite the spec line).
