# Status and roadmap

**As of November 12, 2026** — sanitized public source snapshot; the separately operated private gateway was verified active with 29 tools and zero pending transfer journals after final testing.

| Area | Current evidence | Remaining work |
|---|---|---|
| Steam Deck default tools | Working; original upstream 25 tools proxied | Test unexercised upstream operations when needed |
| Windows routing | Real SSH/MCP access validated | Measure cold-boot and network-change behavior |
| Smart wait | Five-second command completed in one tool call | Quantify real long-term call savings and interactive edge cases |
| Offline recovery | Simulated backend failure and real Windows-off responses passed | Test unexpected router/network drops over longer periods |
| IP-change diagnostics | Warns when Deck's current IP differs from configured allowance | Router DHCP reservation or narrowly scoped Windows firewall adjustment requires explicit network access |
| Single-file copy | Real Deck ↔ Windows SHA-256 verified | Larger-file stress and remote failure recovery testing |
| Folder transfer | Real bidirectional small folder and interrupted per-file resume passed | Reduce per-file SSH/Powershell overhead on thousands of files |
| Byte-level resume | Real 2 MiB uploads/downloads from 128 KiB partials passed | Larger-scale interruption tests and bounded long-running execution |
| GitHub distribution | Sanitized source, tests and docs | Build a guided installer and a secure configuration migration path |

## Recommended next development order

1. **Long-running transfers:** design a detached, bounded job manager with explicit cancellation, progress reports, and recovery after gateway timeout. Avoid blind automatic retries or invisible background work.
2. **Large-folder throughput:** benchmark batches and reduce excessive per-file PowerShell and SFTP process creation while retaining SHA-256 verification and no-clobber guarantees.
3. **Cold-boot/IP resilience:** test reconnect from fully powered-down Windows and a deliberately changed LAN address, without weakening SSH host-key validation or firewall restrictions.
4. **Setup tooling:** create a repeatable installer with explicit permission prompts, readable logs, credential-safe backups and clear rollback paths.
5. **Maintenance:** automated secret scanning, dependency updates, integration tests, release tags and upstream license review.

### Acceptance rules

Each change should have an offline regression test, an isolated integration test where feasible, a before/after measurement, a non-destructive rollback, and documented limits. Do not spend setup complexity on an optimization without evidence of user benefit.