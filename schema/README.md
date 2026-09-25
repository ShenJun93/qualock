# Behavioural records for coding-agent releases — draft 0.1.0

A published, machine-readable record of how a specific coding-agent CLI release behaved under stated
conditions, designed so a third party can recheck the claim offline and so the record can be
referenced from a CycloneDX or SPDX document.

**Status: draft, for comment.** Nothing here is standardised. It exists because arguing for a format
without shipping one is cheap.

```
behavioural-record.schema.json   JSON Schema (draft 2020-12)
validate.py                      validator, standard library only
examples/                        two records built from real published evidence
tests/                           records that MUST be rejected, plus one that must warn
```

Run it:

```bash
python3 validate.py examples/*.json     # both PASS
python3 validate.py tests/*.json        # every bad-* record is rejected
```

## Why the required fields are required

Most of this schema is optional. Five things are not, and each one is required because leaving it out
is how behavioural evidence becomes misleading.

**`subject.hashes`** — at least one hash of the exact artefact. A claim about "version 0.150.0" is a
claim about a name; a claim about a hash is a claim about a thing. Two of our own records turned out
to reference *byte-identical* binaries, which is only knowable because both recorded hashes.

**`environment.containerImageDigest` and `environment.isolation`** — a verdict is only meaningful
against a fixed environment, and an agent that could reach the network may have solved the task by
means the experiment did not intend. The isolation posture is recorded rather than asserted in prose,
so a reader can judge it instead of trusting it.

**`method.attempts`** — agents are stochastic. A single attempt establishes nothing. Requiring the
count makes an underpowered claim visible instead of hiding it behind a verdict.

**`observation.verdict`** with `INCOMPLETE` in the vocabulary — a comparison that could not be run
properly must be able to say so. A format offering only pass and fail pressures its users into
overclaiming.

**`claimScope`**, with both `establishes` and `doesNotEstablish` — the most common failure of this kind
of evidence is being read more broadly than it was measured. Forcing the author to write down the
boundary is cheaper than correcting the misreading later.

## What the validator checks beyond the schema

A schema cannot catch a record that is internally inconsistent. These are the checks that matter:

- `method.attempts` must equal the number of observations in `perVariant`. This caught a real error in
  our own first example: 18 attempts were recorded as 6 observations, because `perVariant` keyed only
  by version cannot express a run with three canaries. The schema was changed as a result.
- `perVariant` may not reference a canary that `canaries` does not declare.
- `firstBad` may not name a version whose observations show the behaviour present, and `recovery` may
  not name one where nothing was restored.
- A `PASS` or `BLOCK` backed by fewer than three attempts warns, and suggests `INCOMPLETE`.
- `graderVisibility: visible` warns: the record then measures the agent's ability to find the grader.
- `provider: local-mock` warns unless `claimScope.doesNotEstablish` mentions end-to-end task success,
  because a mock records what the agent *would have sent*.
- `networkEgress: allowed` warns.
- An `externalReferences` entry not marked `independentlyVerified` warns. Both entries in our own
  example carry that warning, correctly: the references are recorded in the evidence packet, and we
  could not reach github.com to confirm they resolve.

That last point is the intent of the whole format. A record is allowed to be incomplete; it is not
allowed to be silently incomplete.

## What using it on real results changed

The format was revised four times while expressing two real bundles in it. Each change came from a
record that could not be written honestly, not from review:

- **`perVariant` gained a canary dimension.** Keyed only by version it could not express 18 attempts
  over three canaries; the validator caught the arithmetic.
- **`containerImageDigest` was split from `containerImageId`.** One field was carrying both a pullable
  registry manifest digest and a local `docker image inspect` ID. They share the `sha256:` shape and
  differ in kind, so a reviewer who tried to pull a local ID would fail and could reasonably conclude
  the evidence was fabricated.
- **`variants[]` was added.** A four-release bisect has eight artefact hashes; `subject` plus
  `comparedWith` could carry three. The other five had nowhere to go, which made most of the record
  unverifiable. A `version-bisect` record now requires it.
- **`isolation.seccomp` was added and made required.** One bundle ran with
  `agent_security_opt=seccomp=unconfined` while the record reported `containerPrivileged: false` and
  `containerSysAdmin: false`. Those two flags without the third read as a tighter sandbox than was
  actually in force. The validator now warns when seccomp is unconfined and nothing says so.
- **`supersedes` was added.** One bundle deliberately retains a contaminated pilot as superseded
  provenance, and the format had no way to point at it — so the most honest thing in the evidence was
  the one thing the format could not express.

## Alignment with existing formats

This deliberately does not reinvent provenance. `subject` follows CycloneDX component conventions
(`name`, `version`, `purl`, `hashes`) so it can be lifted into a component entry, and
`externalReferences` mirrors the CycloneDX shape. SBOM and attestation tooling answers *what is in a
build and who produced it*; this answers *how did the tool that wrote it behave*. They compose.

Field naming belongs in the CycloneDX and SPDX communities rather than in one repository, and we would
rather contribute it there than maintain a parallel vocabulary.

## Open questions

1. Should a record be signed? Everything here is verifiable but nothing is attributable.
2. Publishing canaries creates a training-data risk. Separating public canary definitions from private
   grading assertions mitigates it and does not solve it.
3. Comparing records across environments is not yet defined. Today a differing image digest simply
   means the two records are about different things, which is honest but unhelpful.
4. There is no revocation story for a record later found to be wrong.
