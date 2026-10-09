# How Qualock relates to existing supply-chain tooling

Qualock records **how a specific coding-agent CLI release behaved** on fixed tasks in a fixed
environment. Existing supply-chain tooling records **what an artefact is, how it was built, and who
built it**. The two answer different questions and are meant to be used together. This page says
where each existing project stops, what Qualock adds, and where Qualock should reuse rather than
reinvent.

| Project | What it records or checks | Where it stops for this problem | How Qualock should relate |
|---|---|---|---|
| [in-toto attestations](https://github.com/in-toto/attestation) | Signed statements about artefacts: a `subject` (digests) plus a typed `predicate`. Existing predicate types include SLSA Provenance, Test Result (`https://in-toto.io/attestation/test-result/v0.1`), Runtime Trace and Reference. | The envelope is general, but no predicate type describes paired, repeated runs of a stochastic agent with an isolation posture, a hidden-grader boundary and an `INCOMPLETE` verdict. The Test Result predicate has only `PASSED/WARNED/FAILED` lists and no attempt counts or claim scope. | Publish the behavioural record as an **in-toto predicate type** whose `subject` is the agent release's artefact digests. Do not invent a new envelope. |
| [Sigstore](https://www.sigstore.dev/) (cosign, Fulcio, Rekor) | Keyless signing and a public transparency log for artefacts and attestations. | Signing proves who published a statement and that it was not altered. It says nothing about whether the statement is true. | Use it to answer open question 1 in [`schema/README.md`](../schema/README.md): sign records with cosign and log them in Rekor, so records become attributable as well as verifiable. |
| [SLSA](https://slsa.dev/) | Build provenance and build-integrity levels: how an artefact was produced and whether the build can be trusted. | A release can be built perfectly and still behave worse than the previous one. SLSA does not test behaviour. | Reference the release's SLSA provenance from the record (`externalReferences`) when the vendor publishes it. The two compose. |
| [GUAC](https://guac.sh/) | Ingests SBOMs, in-toto/SLSA attestations, Scorecard results and vulnerability data into a queryable graph. | It aggregates evidence that already exists; it does not produce behavioural evidence. | A natural **consumer**. Once records are in-toto attestations, GUAC can ingest them alongside SBOMs, so "which agent release did this build use, and how did it behave?" becomes one query. |
| [OpenSSF Scorecard](https://securityscorecards.dev/) | Automated checks of a repository's security practices (branch protection, pinned dependencies, code review, and so on). | It scores project hygiene, not the behaviour of a shipped release. A well-run project can still ship a regression. | Complementary signal. No overlap in what is measured. |
| [CycloneDX](https://cyclonedx.org/) (current spec 1.7; attestations added in 1.6) | Component inventory (SBOM), `formulation` (how components were made) and `declarations` (CycloneDX Attestations: claims, evidence and counter-evidence against stated requirements). | A component entry can say an agent CLI was used, but there is no field for how that release behaved. | The record's `subject` already follows CycloneDX component conventions and `externalReferences` mirrors its shape. A record can be referenced from a component entry or carried as evidence in `declarations`. Field naming should be proposed to the CycloneDX community rather than kept as a parallel vocabulary. |
| [SPDX](https://spdx.dev/) 3 | SBOM with build, AI and dataset profiles. | Same gap as CycloneDX: inventory and provenance, not observed release behaviour. | Same as CycloneDX: reference records by URL and digest. |
| Agent benchmarks (for example SWE-bench, Terminal-Bench) | Model or agent capability on a shared task set, usually as a leaderboard score. | Scores are aggregated across many tasks and runs, rarely tied to one hashed CLI binary in a recorded environment, and are not built to answer "did release N regress against N-1 on *my* task?". | Different purpose. Qualock is a release-qualification gate, not a leaderboard: paired runs of two pinned releases, explicit `INCOMPLETE`, and a claim scope that says what was not established. |

## What is genuinely new

1. **A release-to-release behavioural comparison as a first-class artefact.** The record is tied to
   artefact hashes, a container image digest and a recorded isolation posture, and it carries attempt
   counts and an explicit `claimScope.doesNotEstablish`.
2. **Hidden-grader separation as a recorded property.** The record states whether the agent could see
   the grader, because an agent that can read the grader is measuring something else.
3. **Honest incompleteness.** `INCOMPLETE` is part of the vocabulary, and the validator warns when a
   `PASS` or `BLOCK` rests on fewer than three attempts.

## What Qualock should not build

- Its own signing scheme or transparency log: use Sigstore.
- Its own attestation envelope: use in-toto.
- Its own SBOM fields: propose them to CycloneDX and SPDX.
- A graph database for cross-referencing evidence: let GUAC ingest the records.

## Known limits that no format fixes

- Hosted models cannot be pinned. When the agent calls a server-side model, a record is about the CLI
  release **plus** whatever the provider served on that day. Records state the provider and date; they
  cannot freeze the model.
- Re-running a record needs the same vendor account type and costs inference. A third party can always
  verify a record offline (hashes, arithmetic, claim scope). Re-running it independently requires their
  own paid access.
- Published canaries can end up in training data. Separating the public canary definition from private
  grading assertions reduces this risk but does not remove it.
