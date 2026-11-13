# Changelog

## 2026-11-12 — Multi-device reliability and transfer features

- Implemented automatic Windows MCP connection retirement and on-demand reconnection, with fast offline cooldown.
- Added bounded Windows hostname lookup and optional Deck IP drift warnings.
- Reduced unnecessary short-command output polling with a capped gateway wait.
- Hardened single-file copy with SHA-256 checks, no-clobber commit and recovery journal.
- Added recursive folder transfers with explicit per-file resume, empty directory support and safety checks.
- Added verified byte-level file resume via Windows OpenSSH SFTP `reput`/`reget`.
- Enabled SSH connection multiplexing for lower per-command overhead.
- Verified both directions against real Windows with disposable files, corrected an issue where unfinished `.dc-part-*` files could leak into folder source inventories.
- Extended MCP surface to **29 tools**. Existing ChatGPT chats may cache older tool schemas until refreshed.

## 2026-11-11 — Performance audit and staging

- Audited repeated process output checks and found substantial redundant polling.
- Benchmarked waiting strategies; staged and then deployed gateway smart-wait improvements.
- Prepared reconnect logic and safe deployment/rollback guards.

## 2026-11-10 — Initial multi-device gateway

- Implemented two explicit device aliases and fingerprint-verified Windows SSH pairing.
- Introduced the local `router.py` for Windows terminal, listing, and SFTP operations.
- Switched the existing authorized private tunnel to the new device-aware gateway, with original configuration backed up locally.
- Verified a basic Steam Deck → Windows → Steam Deck checksum round trip.

See [Development log](docs/DEVELOPMENT_LOG.md) for evidence, caveats and future work.