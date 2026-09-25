#!/usr/bin/env python3
"""Validate a behavioural record against behavioural-record.schema.json.

Diagnostics are ASCII only: this runs in CI on machines whose stdout is cp1252, where a
non-ASCII character in a message is mangled and looks like a corrupt file.

Standard library only, matching qualock's zero-dependency discipline. Implements the subset of
JSON Schema draft 2020-12 the schema uses, and adds semantic checks a plain schema cannot express.

    python3 validate.py examples/*.json
"""
import json
import re
import sys
from pathlib import Path

SCHEMA = Path(__file__).with_name("behavioural-record.schema.json")


def resolve(node, root):
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"only local refs are supported, got {ref}")
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        node = target
    return node


def check(value, schema, root, path, errors):
    schema = resolve(schema, root)

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}, got {value!r}")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not one of {schema['enum']}")
        return

    expected = schema.get("type")
    if expected:
        allowed = expected if isinstance(expected, list) else [expected]
        actual = (
            "null" if value is None
            else "boolean" if isinstance(value, bool)
            else "integer" if isinstance(value, int)
            else "number" if isinstance(value, float)
            else "string" if isinstance(value, str)
            else "array" if isinstance(value, list)
            else "object" if isinstance(value, dict)
            else "unknown"
        )
        if actual == "integer" and "number" in allowed:
            actual = "number"
        if actual not in allowed:
            errors.append(f"{path}: expected type {expected}, got {actual}")
            return

    if isinstance(value, str):
        pattern = schema.get("pattern")
        if pattern and not re.match(pattern, value):
            errors.append(f"{path}: {value!r} does not match {pattern}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} below minimum {schema['minimum']}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required field {key!r}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for key, sub in value.items():
            if key in props:
                check(sub, props[key], root, f"{path}.{key}", errors)
            elif extra is False:
                errors.append(f"{path}: unexpected field {key!r}")
            elif isinstance(extra, dict):
                check(sub, extra, root, f"{path}.{key}", errors)

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(value):
                check(item, schema["items"], root, f"{path}[{i}]", errors)


def conditionals(record, root, errors):
    """The allOf if/then rules, applied explicitly so failures name the rule."""
    kind = record.get("method", {}).get("kind")
    if kind == "paired-interleaved-canary":
        for field in ("comparedWith", "canaries"):
            if field not in record:
                errors.append(f"$: a paired-interleaved-canary record requires {field!r}")
    if kind == "version-bisect" and "firstBad" not in record.get("observation", {}):
        errors.append("$: a version-bisect record requires observation.firstBad")
    if kind == "version-bisect" and "variants" not in record:
        errors.append(
            "$: a version-bisect record requires variants[] - subject and comparedWith "
            "cannot carry hashes for more than two releases"
        )


def semantics(record, warnings, errors):
    """Checks the schema cannot express, and which are the point of the format."""
    method = record.get("method", {})
    obs = record.get("observation", {})
    per = obs.get("perVariant") or {}

    def count(block):
        if isinstance(block, list):
            return len(block)
        if isinstance(block, dict):
            return sum(len(v) for v in block.values() if isinstance(v, list))
        return 0

    attempts = method.get("attempts")
    if attempts is not None and per:
        counted = sum(count(v) for v in per.values())
        if counted != attempts:
            errors.append(
                f"$.method.attempts is {attempts} but perVariant lists {counted} observations"
            )

    if method.get("kind") == "paired-interleaved-canary":
        subject = record.get("subject", {}).get("version")
        compared = record.get("comparedWith", {}).get("version")
        for version in (subject, compared):
            if version and version not in per:
                warnings.append(f"perVariant has no entry for compared version {version!r}")

    if obs.get("verdict") in {"PASS", "BLOCK"} and (attempts or 0) < 3:
        warnings.append(
            f"verdict {obs['verdict']} is backed by only {attempts} attempt(s); "
            "consider INCOMPLETE - a stochastic system needs repetition"
        )

    if method.get("graderVisibility") == "visible":
        warnings.append(
            "graderVisibility is 'visible': the record measures the agent's ability to find the "
            "grader, not the behaviour under test"
        )

    if method.get("provider") == "local-mock":
        scope = (record.get("claimScope", {}).get("doesNotEstablish") or "").lower()
        if "end-to-end" not in scope:
            warnings.append(
                "provider is local-mock but claimScope.doesNotEstablish does not mention "
                "end-to-end task success; a mock records what would have been sent"
            )

    isolation = record.get("environment", {}).get("isolation", {})
    if isolation.get("seccomp") == "unconfined" and isolation.get("containerPrivileged") is False:
        blob = " ".join(str(v) for v in record.get("claimScope", {}).values())
        notes = isolation.get("notes") or ""
        if "seccomp" not in blob.lower() and "seccomp" not in notes.lower():
            warnings.append(
                "seccomp is unconfined while containerPrivileged is false: say so in "
                "isolation.notes or claimScope, otherwise the record reads as better "
                "isolated than it was"
            )

    every = {v.get("version") for v in record.get("variants", [])}
    if every:
        for name in ("subject", "comparedWith"):
            ver = record.get(name, {}).get("version")
            if ver and ver not in every:
                warnings.append(f"{name}.version {ver!r} has no entry in variants[]")
        for version in (record.get("observation", {}).get("perVariant") or {}):
            if version not in every:
                errors.append(
                    f"perVariant has results for {version!r} but variants[] does not list it"
                )

    if isolation.get("networkEgress") == "allowed":
        warnings.append(
            "networkEgress is 'allowed': attempts may have been solved by means the experiment "
            "did not intend"
        )

    for ref in record.get("externalReferences", []):
        if ref.get("independentlyVerified") is not True:
            warnings.append(
                f"external reference {ref.get('ref')!r} is not marked independentlyVerified"
            )

    def observed(block):
        if isinstance(block, list):
            return [x for x in block if x]
        if isinstance(block, dict):
            return [x for v in block.values() if isinstance(v, list) for x in v if x]
        return []

    fb, rec = obs.get("firstBad"), obs.get("recovery")
    if fb and per and fb in per and observed(per[fb]):
        errors.append(f"observation.firstBad is {fb!r} but perVariant[{fb!r}] shows the behaviour present")
    if rec and per and rec in per and not observed(per[rec]):
        errors.append(f"observation.recovery is {rec!r} but perVariant[{rec!r}] shows nothing restored")

    if record.get("canaries") and per:
        declared = {c["id"] for c in record["canaries"] if "id" in c}
        for version, block in per.items():
            if isinstance(block, dict):
                unknown = set(block) - declared
                if unknown:
                    errors.append(
                        f"perVariant[{version!r}] references canaries not declared in $.canaries: "
                        f"{sorted(unknown)}"
                    )
                missing = declared - set(block)
                if missing:
                    warnings.append(
                        f"perVariant[{version!r}] has no results for declared canaries {sorted(missing)}"
                    )


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = json.loads(SCHEMA.read_text())
    failed = False
    for arg in argv[1:]:
        path = Path(arg)
        record = json.loads(path.read_text())
        errors, warnings = [], []
        check(record, root, root, "$", errors)
        conditionals(record, root, errors)
        semantics(record, warnings, errors)

        status = "FAIL" if errors else "PASS"
        print(f"{status}  {path.name}")
        for e in errors:
            print(f"    error:   {e}")
        for w in warnings:
            print(f"    warning: {w}")
        failed |= bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
