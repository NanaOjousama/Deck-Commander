# Deck-Commander — Release staging and rollback rehearsal

**Date:** November 24, 2026 · **Status:** Isolated engineering rehearsal, **not an installer** and **not ready to deploy**.

## Why this milestone exists

The previous gate established a least-privilege connector-parent systemd service topology in disposable CI. We still need a defensible update/rollback workflow before any installation on the owner's Steam Deck or on customer machines.

A release must not overwrite a working version, silently package private SSH credentials or journals, switch to unverified code, or delete recovery evidence during failure handling. The application is not commercially security-cleared.

## Implemented isolated rehearsal

The new `service/release_transaction.py` provides **filesystem-only**, single-host release selection in a caller-owned private temporary sandbox. It does **not** install/enable/stop services, touch `/opt` or `/var/lib`, rotate credentials, modify live connectors, change OS accounts, copy customer configuration, or act on a running backend.

- Source staging has a **fixed allowlist** of application/source files and existing review-only service design artifacts. It does not copy `.env`, arbitrary directories, SSH keys, Windows pairing data, logs or recovery journals. Node dependencies are **not bundled**; this is not yet a bootable or signed installer.
- Release IDs are restricted to plain identifiers. A new release directory is created exclusively (no replacement of a preexisting version), with bounded regular-file copies and SHA-256 checksums. Its final ready marker appears only after the staged files and manifest are written and fsynced.
- Selection is tracked by a separate `active.json` **rehearsal metadata file**; it is not used by the systemd unit and does not switch an actual running application. Activation verifies the candidate. The former selected version is retained as the previous version.
- Rollback verifies the previous release, atomically replaces the one-file selection record and retains both code trees. A corrupted *currently selected* release does not prevent reverting to a still-verified previous release.
- A private Linux `flock` serializes cooperative parallel stagings/activations/rollbacks. Atomic metadata file replacement and directory fsync reduce the risk of partially written active selection records. The script does not promise durability under real filesystem corruption or power loss.
- Injected exceptions mid-stage remove only a newly created candidate directory. An uncatchable crash might leave a partial candidate behind: without the final ready marker, it cannot be selected or silently overwritten.

## Acceptance tests

The added tests cover allowlisted code/secret exclusion, synthetic journal preservation, staging collisions, tampered payloads/manifest, malicious IDs, source/marker symlinks, public sandbox permissions, corrupted selection records, partial-stage failures, injected promotion/rollback failures, concurrent promotions and rollback from a corrupted active release.

They use **only newly created temporary directories**. No real SSH account, connector, service unit, OS account, customer files or personal device state is altered. GitHub's `release-rollback-rehearsal.yml` reproduces them on an ephemeral Ubuntu runner and reruns the full Python regression suite.

## What this absolutely does NOT prove

1. **Actual installer or rollback of a systemd deployment:** the selected rehearsal manifest is not connected to `ExecStart`, `DC_APP_ROOT`, service reload/restart or post-upgrade health checks. A future installer needs a coordinated, auditable, reversible service-unit switch.
2. **Signed/trusted release artifacts:** hashes detect accidental modification of staged bytes, not a malicious authorized release or compromised distribution channel. Reproducible build, signed provenance, a dependency lockfile and SBOM are separate gates.
3. **Safe customer migration:** no live pairing, host keys, environment, journal or permission migration has been attempted. No data restore/backup validation of real customer content.
4. **Crash/power-loss transactions:** only bounded fault injection and synthetic crash-remnant rejection, not a real power-cycle/kill during fsync and restart reconciliation.
5. **Complete authentication:** the hosted ChatGPT two-account test remains open; the prior tunnel-client test used two synthetic bearer identities and a protected localhost HTTP MCP stub. It does not establish hosted authorization.

## Next engineering acceptance

Build a **disposable full installation/update/rollback test** with a fresh, non-admin systemd account, correctly bound connector and gateway, an explicit service-unit release path, health-gated activation, and rollback on startup failure. It must preserve private pairing state and recovery journals, not auto-replay ambiguous transfers, and leave unrelated services untouched. Require separate review and explicit approval before touching the user's machine.

The real external connector identity/revocation test requires separate authorized ChatGPT accounts and a disposable hosted connector/tunnel. Do not use the user's current tunnel credentials or disable their live connection.

**Release decision:** This is a verified *rehearsal mechanism* once CI passes—not a secure customer installer, an approved merge, or commercial launch authorization. Keep Draft PR #6 unchanged. Store detailed test logs in GitHub; add a short evidence handoff to Deck-Commander Project Sources when validated.
