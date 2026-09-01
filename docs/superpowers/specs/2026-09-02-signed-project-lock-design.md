# Signed Project Lock Design

## Goal
Make `.qualock/project.lock` tamper-evident so `qualock verify` cannot be weakened by editing the lock inside the repository.

## Format
`ProjectLock` remains the schema-v1 payload. The on-disk file becomes a schema-v2 signed envelope containing the payload plus an HMAC-SHA256 digest. The digest is computed over deterministic canonical JSON of the payload.

## Key lifecycle
QuaLock creates one 32-byte local signing key on the first successful `qualock protect`. The default path is the platform-specific QuaLock user config directory (`~/.config/qualock/project-protection.key` on Linux). The key is never written inside the project. On POSIX it is created with user-only permissions.

`qualock verify` only loads an existing key. It never creates or rotates a key implicitly. A missing, malformed, or mismatched key makes verification incomplete and no protection commands run.

## Verification order
`qualock verify` verifies the signed envelope before reading locked protection definitions into the execution flow. Invalid signature, malformed envelope, or legacy unsigned lock fails closed with a clear message telling the user to run `qualock protect` again only after establishing a trusted known-good state.

## Compatibility
Existing agent qualification config, fingerprints, canaries, Docker isolation, qualification evidence, and exit codes remain unchanged. Legacy unsigned project locks are not silently trusted; they require re-protection. Signed project locks are local-machine artifacts by default because verification depends on the user-level signing key.

## Threat boundary
This protects against repository-local tampering with `.qualock/project.lock`. It does not protect against an agent or process that can also read or modify QuaLock's user-level signing key. OS keychain-backed signing is a possible later hardening layer.
