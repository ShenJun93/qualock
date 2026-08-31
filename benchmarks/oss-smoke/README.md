# OSS smoke suite

This curated suite exists to validate Qualock's first real Codex version-to-version experiment. The agent receives a neutral task description and the historical pre-fix repository state. The regression grader is mounted only after the agent exits.

| Canary | Repository | Base commit | Upstream fix |
| --- | --- | --- | --- |
| `starlette-url-empty-authority` | `Kludex/starlette` | `4a18cd4a0869158f830e9bf519979d6f6f60f36a` | PR #3317 |
| `pytest-xdist-loadgroup-crash-recovery` | `pytest-dev/pytest-xdist` | `dd198c35710f22b1cb86b7fc00311f9b7c63d665` | PR #1324 |
| `click-sentinel-duplication` | `pallets/click` | `3cbcf9b11546f4cf10b36d3e2e531733ba6fe001` | PR #3805 |

The graders are Qualock-owned regression checks rather than copies of the upstream test files. The suite is intentionally small; its purpose is to prove the execution and qualification signal before expanding benchmark breadth.

The xdist canary replaces the earlier pytest caplog case because GPT-5.6 Terra's February 16, 2026 knowledge cutoff makes the February 13 caplog report unsuitable as a clean post-cutoff pilot task. The replacement bug was opened on April 18, 2026.
