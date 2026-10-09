# Licensing of published data

Code in this repository, including the schema and the validator, is licensed under the Apache License
2.0 (see [LICENSE](LICENSE)).

**Behavioural records and evidence summaries authored for this project** are dedicated to the public
domain under [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/). This covers:

- `schema/examples/` and any record in this repository that follows `schema/behavioural-record.schema.json`;
- the README files, tables and summaries under `docs/evidence/`;
- the canary definitions and grader patches written for this project under `benchmarks/`.

You may copy, redistribute, adapt and combine them with other data for any purpose, without asking
and without attribution.

## Please cite

Attribution is not required, but it helps others find the original evidence. If you use these
records, please cite "Qualock (https://github.com/ShenJun93/qualock)" and the record or evidence
directory you used.

## Third-party material

Some benchmark tasks and evidence packets contain material from other projects: upstream source
code and tests that canaries are built on, agent CLI output, and vendor release metadata. That
material keeps its original licence and is not covered by this dedication. Where a canary is built on
an upstream project, its definition names the upstream repository and commit (`repository.url`,
`repository.base_sha`).

## Why CC0 for data

The records are meant to be consumed by other tools: referenced from CycloneDX or SPDX documents and
ingested into graphs such as GUAC alongside SBOMs and attestations. An attribution requirement is hard
to carry through that kind of aggregation, so it would discourage exactly the reuse the records exist
for. CC0 is the usual choice for machine-readable metadata of this kind.
