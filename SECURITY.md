# Security policy

## Supported versions

Qualock is currently pre-1.0. Security fixes are applied to the latest development version until the first stable release line exists.

## Threat model

v0.1 is intended for repositories you trust. Docker provides a filesystem boundary for hidden graders and repeatable prepared state, but Qualock is not a hardened sandbox for arbitrary hostile repositories.

The agent phase disables Codex web search and workspace network access through explicit configuration. Codex authentication may still be mounted read-only into the agent container, so repository code must be treated as trusted until a dedicated credential broker and stronger egress isolation are implemented.

Do not use v0.1 to execute unknown third-party repositories with valuable credentials present.

## Reporting vulnerabilities

Prefer GitHub's private security advisory flow for this repository. If private reporting is unavailable, open a minimal public issue asking for a private contact channel and do not include exploit details, secrets, or proof-of-concept payloads in the public issue.
