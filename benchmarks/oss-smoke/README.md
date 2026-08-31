# OSS smoke suite

This curated suite exists to validate Qualock's first real Codex version-to-version experiment. The agent receives a neutral task description and the historical pre-fix repository state. The regression grader is mounted only after the agent exits.

| Canary | Repository | Base commit | Upstream fix |
| --- | --- | --- | --- |
| `starlette-url-empty-authority` | `Kludex/starlette` | `4a18cd4a0869158f830e9bf519979d6f6f60f36a` | PR #3317 |
| `pytest-caplog-nested-filter` | `pytest-dev/pytest` | `ced9022c0ca87ae2a0a604c68d2e1c462f8a5c6f` | PR #14284 |
| `click-sentinel-duplication` | `pallets/click` | `3cbcf9b11546f4cf10b36d3e2e531733ba6fe001` | PR #3805 |

The graders are Qualock-owned regression checks rather than copies of the upstream test files. The suite is intentionally small; its purpose is to prove the execution and qualification signal before expanding benchmark breadth.
