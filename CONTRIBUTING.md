# Contributing

Qualock is early-stage. Contributions that improve reproducibility, isolation, deterministic qualification, Codex compatibility, or benchmark quality are welcome.

## Development

```bash
python -m pip install -e . pytest
python -m pytest -q
```

The normal test suite must not require OpenAI credentials. Tests that require Docker or live Codex access must be explicitly isolated from the default suite.

## Pull requests

Keep changes focused. Add or update tests for behavior changes, preserve backward compatibility for published file formats when possible, and document any change that affects qualification semantics.

Changes to the policy engine, fingerprint inputs, hidden-grader boundary, or evidence validity rules require especially careful review because they can change PASS/WARN/BLOCK outcomes.

By submitting a contribution, you agree that your contribution is licensed under the Apache License 2.0 used by this repository.
