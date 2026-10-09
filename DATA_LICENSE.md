# Licensing of published data

Code in this repository, including the schema and the validator, is licensed under the Apache License
2.0 (see [LICENSE](LICENSE)).

**Behavioural records and evidence summaries authored for this project** are licensed under the
[Creative Commons Attribution 4.0 International licence (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
This covers:

- `schema/examples/` and any record in this repository that follows `schema/behavioural-record.schema.json`;
- the README files, tables and summaries under `docs/evidence/`;
- the canary definitions and grader patches written for this project under `benchmarks/`.

You may copy, redistribute and adapt them, including commercially, as long as you credit
"Qualock (https://github.com/ShenJun93/qualock)" and indicate whether you changed anything.

## Third-party material

Some benchmark tasks and evidence packets contain material from other projects: upstream source
code and tests that canaries are built on, agent CLI output, and vendor release metadata. That
material keeps its original licence and is not relicensed by this file. Where a canary is built on an
upstream project, its definition names the upstream repository and commit.

## Why a separate licence for data

Apache-2.0 is written for software. CC BY 4.0 is the usual choice for datasets that should be easy to
reuse, cite and combine with other published evidence.
