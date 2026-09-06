"""Fixed, secure GitHub Actions workflow templates for PR qualification."""

CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"
UPLOAD_ARTIFACT_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
DOWNLOAD_ARTIFACT_SHA = "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"

PRODUCER_WORKFLOW = f"""\
name: QuaLock PR Qualification

on:
  pull_request_target:
    types: [opened, reopened, synchronize, ready_for_review]

permissions:
  contents: read
  pull-requests: read

concurrency:
  group: qualock-pr-${{{{ github.repository }}}}-${{{{ github.event.pull_request.number }}}}
  cancel-in-progress: true

jobs:
  qualify:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout trusted base
        uses: actions/checkout@{CHECKOUT_SHA}
        with:
          ref: ${{{{ github.event.pull_request.base.sha }}}}
          persist-credentials: false

      - name: Set up Python
        uses: actions/setup-python@{SETUP_PYTHON_SHA}
        with:
          python-version: "3.12"

      - name: Install QuaLock from trusted checkout
        run: python -m pip install .

      - name: Prepare PR context
        id: prepare
        run: |
          qualock github prepare-pr \\
            --event "$GITHUB_EVENT_PATH" \\
            --context-out "$RUNNER_TEMP/pr-context.json" \\
            --report-out "$RUNNER_TEMP/pr-report.json" \\
            --proposed-lock-out "$RUNNER_TEMP/proposed-baseline.lock"

      - name: Upload PR context artifact
        uses: actions/upload-artifact@{UPLOAD_ARTIFACT_SHA}
        with:
          name: qualock-pr-context
          path: ${{{{ runner.temp }}}}/pr-context.json
          if-no-files-found: error

      - name: Plan qualification
        id: plan
        env:
          QUALOCK_CONTEXT: ${{{{ runner.temp }}}}/pr-context.json
          QUALOCK_PROPOSED_LOCK: ${{{{ runner.temp }}}}/proposed-baseline.lock
          QUALOCK_REPORT: ${{{{ runner.temp }}}}/pr-report.json
        run: |
          python - <<'PY'
          import json
          import os
          from pathlib import Path

          with open(os.environ["QUALOCK_CONTEXT"], encoding="utf-8") as handle:
              context = json.load(handle)
          classification = context["classification"]
          agent = context.get("agent") or ""
          ready = (
              classification == "upgrade"
              and agent in {{"codex", "claude"}}
              and Path(os.environ["QUALOCK_PROPOSED_LOCK"]).is_file()
              and not Path(os.environ["QUALOCK_REPORT"]).is_file()
          )
          with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
              output.write(f"classification={{classification}}\\n")
              output.write(f"agent={{agent}}\\n")
              output.write(f"ready={{'true' if ready else 'false'}}\\n")
          PY

      - name: Materialize codex credential
        id: credential
        if: steps.plan.outputs.ready == 'true' && steps.plan.outputs.agent == 'codex'
        env:
          QUALOCK_CODEX_AUTH_B64: ${{{{ secrets.QUALOCK_CODEX_AUTH_B64 }}}}
        run: |
          set +x
          install -d -m 700 "$HOME/.codex"
          if [ -n "$QUALOCK_CODEX_AUTH_B64" ]; then
            printf '%s' "$QUALOCK_CODEX_AUTH_B64" | base64 -d > "$HOME/.codex/auth.json"
            chmod 600 "$HOME/.codex/auth.json"
            echo "available=true" >> "$GITHUB_OUTPUT"
          else
            echo "available=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Qualify upgrade (codex)
        if: steps.plan.outputs.ready == 'true' && steps.plan.outputs.agent == 'codex'
        env:
          QUALOCK_CREDENTIAL_AVAILABLE: ${{{{ steps.credential.outputs.available }}}}
        run: |
          qualock github qualify-pr \\
            --context "$RUNNER_TEMP/pr-context.json" \\
            --proposed-lock "$RUNNER_TEMP/proposed-baseline.lock" \\
            --report-out "$RUNNER_TEMP/pr-report.json" \\
            --credential-available "$QUALOCK_CREDENTIAL_AVAILABLE"

      - name: Qualify upgrade (claude)
        if: steps.plan.outputs.ready == 'true' && steps.plan.outputs.agent == 'claude'
        env:
          ANTHROPIC_AUTH_TOKEN: ${{{{ secrets.QUALOCK_ANTHROPIC_AUTH_TOKEN }}}}
          ANTHROPIC_API_KEY: ${{{{ secrets.QUALOCK_ANTHROPIC_API_KEY }}}}
          CLAUDE_CODE_OAUTH_TOKEN: ${{{{ secrets.QUALOCK_CLAUDE_CODE_OAUTH_TOKEN }}}}
        run: |
          set +x
          if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; then
            unset ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY
            credential_available=true
          elif [ -n "$ANTHROPIC_API_KEY" ]; then
            unset ANTHROPIC_AUTH_TOKEN
            credential_available=true
          elif [ -n "$ANTHROPIC_AUTH_TOKEN" ]; then
            credential_available=true
          else
            unset ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN
            credential_available=false
          fi
          qualock github qualify-pr \\
            --context "$RUNNER_TEMP/pr-context.json" \\
            --proposed-lock "$RUNNER_TEMP/proposed-baseline.lock" \\
            --report-out "$RUNNER_TEMP/pr-report.json" \\
            --credential-available "$credential_available"

      - name: Clean up codex credential
        if: always()
        run: rm -f "$HOME/.codex/auth.json"

      - name: Upload PR report artifact
        if: always()
        uses: actions/upload-artifact@{UPLOAD_ARTIFACT_SHA}
        with:
          name: qualock-pr-report
          path: ${{{{ runner.temp }}}}/pr-report.json
          if-no-files-found: error
"""

REPORTER_WORKFLOW = f"""\
name: QuaLock PR Reporter

on:
  workflow_run:
    workflows: ["QuaLock PR Qualification"]
    types: [completed]

permissions:
  actions: read
  contents: read
  statuses: write
  pull-requests: write

jobs:
  report:
    runs-on: ubuntu-latest
    if: github.event.workflow_run.event == 'pull_request_target'
    steps:
      - name: Checkout trusted head
        uses: actions/checkout@{CHECKOUT_SHA}
        with:
          ref: ${{{{ github.event.workflow_run.head_sha }}}}
          persist-credentials: false

      - name: Set up Python
        uses: actions/setup-python@{SETUP_PYTHON_SHA}
        with:
          python-version: "3.12"

      - name: Install QuaLock from trusted checkout
        run: python -m pip install .

      - name: Download PR context
        uses: actions/download-artifact@{DOWNLOAD_ARTIFACT_SHA}
        with:
          name: qualock-pr-context
          run-id: ${{{{ github.event.workflow_run.id }}}}
          path: ${{{{ runner.temp }}}}/qualock-pr-context
          github-token: ${{{{ secrets.GITHUB_TOKEN }}}}

      - name: Download PR report
        id: download_report
        continue-on-error: true
        uses: actions/download-artifact@{DOWNLOAD_ARTIFACT_SHA}
        with:
          name: qualock-pr-report
          run-id: ${{{{ github.event.workflow_run.id }}}}
          path: ${{{{ runner.temp }}}}/qualock-pr-report
          github-token: ${{{{ secrets.GITHUB_TOKEN }}}}

      - name: Report PR qualification status
        run: |
          qualock github report-pr \\
            --event "$GITHUB_EVENT_PATH" \\
            --context "$RUNNER_TEMP/qualock-pr-context/pr-context.json" \\
            --report "$RUNNER_TEMP/qualock-pr-report/pr-report.json"
"""
